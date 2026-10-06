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
