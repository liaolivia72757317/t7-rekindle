using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using NLog;
using NLog.Config;
using NLog.Targets;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;
using T7.Rekindle.Desktop.Views;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class DiagnosticLogTests
    {
        internal static void Run(string directory, string output)
        {
            TestTimestamps(directory);
            TestForwardedLogs(directory);
            TestSingleExceptionRecord(directory);
            TestDiagnosticScrolling(output);
            TestLiveDiagnostics(directory);
        }

        private static void TestTimestamps(string directory)
        {
            var culture = Thread.CurrentThread.CurrentCulture;
            try
            {
                Thread.CurrentThread.CurrentCulture = CultureInfo.GetCultureInfo("th-TH");
                using (var model = new MainWindowViewModel(new FakeLauncherBridge(),
                    new SettingsService(Path.Combine(directory, "log-timestamps")),
                    new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "Player" }, null,
                    path => Task.FromResult(ValidDirectory(path)), new FakeDesktopInteraction()))
                {
                    RunTask(model.ValidationTask);
                    var earliest = DateTime.Now.AddSeconds(-1);
                    RunTask(model.CheckCommand.ExecuteAsync(null));
                    var latest = DateTime.Now;
                    var lines = model.NativeLogText.Split(new[] { Environment.NewLine }, StringSplitOptions.None);
                    Assert(lines.Length >= 2, "log fixture did not produce launcher records");
                    foreach (var line in lines)
                    {
                        Assert(line.Length > 21 && DateTime.TryParseExact(line.Substring(0, 19),
                            "yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture, DateTimeStyles.None, out var timestamp)
                            && timestamp >= earliest && timestamp <= latest && line.Substring(19, 2) == "  ",
                            "launcher log timestamp is not local yyyy-MM-dd HH:mm:ss: " + line);
                    }
                }

                var configuration = new XmlLoggingConfiguration(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "NLog.config"));
                var target = (FileTarget)configuration.FindTargetByName("file");
                var record = new LogEventInfo(LogLevel.Info, "fixture", "message")
                {
                    TimeStamp = new DateTime(2026, 1, 2, 3, 4, 5, 678, DateTimeKind.Local)
                };
                Assert(target.Layout.Render(record).StartsWith("2026-01-02 03:04:05|INFO|fixture|message", StringComparison.Ordinal),
                    "desktop log timestamp is not invariant yyyy-MM-dd HH:mm:ss");
            }
            finally { Thread.CurrentThread.CurrentCulture = culture; }
        }

        private static void TestForwardedLogs(string directory)
        {
            var previousConfiguration = LogManager.Configuration;
            var culture = Thread.CurrentThread.CurrentCulture;
            try
            {
                Thread.CurrentThread.CurrentCulture = CultureInfo.GetCultureInfo("th-TH");
                var fileConfiguration = new XmlLoggingConfiguration(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "NLog.config"));
                var layout = ((FileTarget)fileConfiguration.FindTargetByName("file")).Layout;
                var target = new MemoryTarget("forwarded") { Layout = layout };
                var errors = new MemoryTarget("native-errors") { Layout = layout };
                var configuration = new LoggingConfiguration();
                configuration.AddRule(LogLevel.Info, LogLevel.Fatal, target);
                configuration.AddRule(LogLevel.Error, LogLevel.Fatal, errors, "NativeBridge");
                LogManager.Configuration = configuration;

                const string timestamp = "2026-01-02 03:04:05";
                var nativeLines = new[]
                {
                    timestamp + "  ERROR  [NativeBridge] 原生错误",
                    timestamp + "  INFO  [NativeBridge] 原生信息 | [detail] {value}",
                    timestamp + "  WARNING  [NativeBridge] 原生警告",
                    timestamp + "  ERROR  [NativeBridge] ",
                    "unstructured native line",
                    "2026-02-30 03:04:05  ERROR  [NativeBridge] invalid timestamp",
                    timestamp + "  UNKNOWN  [NativeBridge] unknown level",
                    timestamp + "  ERROR  [Journal] #42 synthetic fixture business failure",
                    timestamp + "  WARNING  [Journal] WIRE COVERAGE INCOMPLETE"
                };
                var bridge = new FakeLauncherBridge { LogLines = nativeLines, Failure = "launcher failure" };
                using (var model = new MainWindowViewModel(bridge,
                    new SettingsService(Path.Combine(directory, "forwarded-logs")),
                    new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "Player" }, null,
                    path => Task.FromResult(ValidDirectory(path)), new FakeDesktopInteraction()))
                {
                    RunTask(model.ValidationTask);
                    var deadline = DateTime.UtcNow.AddSeconds(5);
                    while (target.Logs.Count < nativeLines.Length && DateTime.UtcNow < deadline)
                    {
                        Pump();
                        Thread.Sleep(10);
                    }
                    Assert(target.Logs.Count == nativeLines.Length, "native log polling lost or duplicated records");
                    Assert(target.Logs[0] == timestamp + "|ERROR|NativeBridge|原生错误 ",
                        "forwarded native error lost its original timestamp, severity or source: " + target.Logs[0]);
                    Assert(target.Logs[1] == timestamp + "|INFO|NativeBridge|原生信息 | [detail] {value} "
                        && target.Logs[2] == timestamp + "|WARN|NativeBridge|原生警告 "
                        && target.Logs[3] == timestamp + "|ERROR|NativeBridge| ",
                        "forwarded native logs changed their message or severity");
                    Assert(errors.Logs.SequenceEqual(new[] { target.Logs[0], target.Logs[3] }),
                        "forwarded logs did not honor native source and error-level filtering");
                    Assert(target.Logs[7] == timestamp + "|ERROR|Journal|#42 synthetic fixture business failure "
                        && target.Logs[8] == timestamp + "|WARN|Journal|WIRE COVERAGE INCOMPLETE ",
                        "forwarded journal logs lost their original timestamp, severity or source");
                    for (var index = 4; index < 7; index++)
                        Assert(target.Logs[index].EndsWith("|" + nativeLines[index] + " ", StringComparison.Ordinal),
                            "unrecognized native log text was discarded or changed");
                    Assert(model.NativeLogText == string.Join(Environment.NewLine, nativeLines),
                        "desktop log formatting changed the original UI log text");

                    RunTask(model.StartCommand.ExecuteAsync(null));
                    var launcherLines = model.NativeLogText.Split(new[] { Environment.NewLine }, StringSplitOptions.None)
                        .Where(line => line.Contains("  [Launcher] ")).ToArray();
                    Assert(launcherLines.Length == 2 && launcherLines.Any(line => line.Contains("  ERROR  ")),
                        "launcher fixture did not produce info and error records");
                    foreach (var line in launcherLines)
                    {
                        var expected = line.Replace("  INFO  [Launcher] ", "|INFO|Launcher|")
                            .Replace("  ERROR  [Launcher] ", "|ERROR|Launcher|") + " ";
                        Assert(target.Logs.Contains(expected), "launcher log was double-formatted or lost its severity");
                    }
                }
            }
            finally
            {
                LogManager.Configuration = previousConfiguration;
                Thread.CurrentThread.CurrentCulture = culture;
            }
        }

        private static void TestSingleExceptionRecord(string directory)
        {
            var previousConfiguration = LogManager.Configuration;
            try
            {
                var target = new MemoryTarget("operation-errors")
                {
                    Layout = "${level}|${logger}|${message}|${exception:format=tostring}"
                };
                var configuration = new LoggingConfiguration();
                configuration.AddRule(LogLevel.Error, LogLevel.Fatal, target);
                LogManager.Configuration = configuration;
                var inspections = 0;
                using (var model = new MainWindowViewModel(new FakeLauncherBridge(),
                    new SettingsService(Path.Combine(directory, "operation-errors")),
                    new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "Player" }, null,
                    path =>
                    {
                        if (++inspections > 1) throw new InvalidOperationException("fixture inspection failure");
                        return Task.FromResult(ValidDirectory(path));
                    }, new FakeDesktopInteraction()))
                {
                    RunTask(model.ValidationTask);
                    RunTask(model.StartCommand.ExecuteAsync(null));
                    Assert(target.Logs.Count == 1, "one operation exception produced duplicate error records");
                    Assert(target.Logs[0].StartsWith("Error|Launcher|", StringComparison.Ordinal)
                        && target.Logs[0].Contains("System.InvalidOperationException: fixture inspection failure")
                        && target.Logs[0].Contains(nameof(TestSingleExceptionRecord)),
                        "operation error lost its source, exception type or stack trace");
                    Assert(model.NativeLogText.Contains("  ERROR  [Launcher] 启动失败：fixture inspection failure"),
                        "deduplicating the file log removed the UI error");
                }
            }
            finally { LogManager.Configuration = previousConfiguration; }
        }

        private static void TestDiagnosticScrolling(string output)
        {
            var longLine = "2026-01-02 03:04:05  INFO  [NativeBridge] " + new string('A', 240);
            var text = string.Join(Environment.NewLine, Enumerable.Repeat(longLine, 80));
            var dialog = new TextDialog("启动诊断", text, false, true);
            try
            {
                var document = (TextBox)dialog.FindName("DocumentText");
                LauncherLayoutTests.Render((FrameworkElement)dialog.Content, null, null, "diagnostic-logs", 644, 441);
                document.RaiseEvent(new RoutedEventArgs(FrameworkElement.LoadedEvent));
                Pump();
                document.UpdateLayout();
                Assert(document.TextWrapping == TextWrapping.NoWrap
                    && document.HorizontalScrollBarVisibility == ScrollBarVisibility.Auto
                    && document.ExtentWidth > document.ViewportWidth,
                    "diagnostics wrap long log lines or hide horizontal scrolling");
                AssertAtBottom(document);
                Assert(document.HorizontalOffset == 0, "opening diagnostics hid log timestamps horizontally");

                document.ScrollToHome();
                document.UpdateLayout();
                document.AppendText(Environment.NewLine + "2026-01-02 03:04:06  INFO  [Launcher] token=fixture-secret");
                LauncherLayoutTests.Render((FrameworkElement)dialog.Content, null, output, "diagnostic-logs-bottom", 644, 441);
                document.UpdateLayout();
                AssertAtBottom(document);
                string copied = null;
                dialog.CopyDiagnosticText = value => copied = value;
                typeof(TextDialog).GetMethod("OnCopyDiagnostics", BindingFlags.Instance | BindingFlags.NonPublic)
                    .Invoke(dialog, new object[] { dialog, new RoutedEventArgs() });
                Assert(copied == DiagnosticSanitizer.Redact(document.Text) && !copied.Contains("fixture-secret"),
                    "diagnostic copy did not include and redact the latest log text");
            }
            finally { dialog.Close(); }

            var explanation = new TextDialog("说明", text);
            try
            {
                var document = (TextBox)explanation.FindName("DocumentText");
                LauncherLayoutTests.Render((FrameworkElement)explanation.Content, null, null, "explanation", 644, 441);
                document.RaiseEvent(new RoutedEventArgs(FrameworkElement.LoadedEvent));
                Pump();
                document.UpdateLayout();
                Assert(document.TextWrapping == TextWrapping.Wrap && document.VerticalOffset == 0,
                    "diagnostic scrolling changed ordinary document behavior");
            }
            finally { explanation.Close(); }
        }

        private static void AssertAtBottom(TextBox document) => Assert(document.ExtentHeight > document.ViewportHeight
            && Math.Abs(document.VerticalOffset + document.ViewportHeight - document.ExtentHeight) < 1,
            "diagnostics did not scroll to the latest log line");

        private static void TestLiveDiagnostics(string directory)
        {
            var bridge = new FakeLauncherBridge { Failure = new string('A', 180) + " token=fixture-secret" };
            using (var model = new MainWindowViewModel(bridge, new SettingsService(Path.Combine(directory, "live-diagnostics")),
                new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "Player" }, null,
                path => Task.FromResult(ValidDirectory(path)), new FakeDesktopInteraction()))
            {
                RunTask(model.ValidationTask);
                var dialog = new TextDialog(model);
                try
                {
                    var document = (TextBox)dialog.FindName("DocumentText");
                    LauncherLayoutTests.Render((FrameworkElement)dialog.Content, null, null, "live-diagnostics", 464, 281);
                    Assert(dialog.Height == 480 && document.Text.Contains(model.DiagnosticText),
                        "live diagnostics lost its initial state or shrank to the short-document size");
                    for (var index = 0; index < 12; index++) RunTask(model.StartCommand.ExecuteAsync(null));
                    LauncherLayoutTests.Render((FrameworkElement)dialog.Content, null, null, "live-diagnostics", 464, 281);
                    document.UpdateLayout();
                    Assert(document.Text.StartsWith("当前状态：" + model.StatusText, StringComparison.Ordinal)
                        && document.Text.Contains(model.DiagnosticText) && document.Text.Contains(model.EndpointText)
                        && document.Text.EndsWith(model.NativeLogText, StringComparison.Ordinal),
                        "open diagnostics did not track session state and incoming logs");
                    AssertAtBottom(document);
                    string copied = null;
                    dialog.CopyDiagnosticText = value => copied = value;
                    typeof(TextDialog).GetMethod("OnCopyDiagnostics", BindingFlags.Instance | BindingFlags.NonPublic)
                        .Invoke(dialog, new object[] { dialog, new RoutedEventArgs() });
                    Assert(copied == DiagnosticSanitizer.Redact(document.Text) && !copied.Contains("fixture-secret"),
                        "live diagnostics copied stale or unredacted content");
                }
                finally { dialog.Close(); }
            }
        }
    }
}
