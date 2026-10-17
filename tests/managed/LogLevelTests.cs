using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Controls;
using NLog;
using NLog.Config;
using NLog.Targets;
using Newtonsoft.Json.Linq;
using T7R.Core;
using T7R.Desktop.Services;
using T7R.Desktop.ViewModels;
using T7R.Desktop.Views;
using static T7R.ManagedHarness.LauncherTests;

namespace T7R.ManagedHarness
{
    internal static class LogLevelTests
    {
        internal static void Run(string directory, string output)
        {
            var configuration = LogManager.Configuration;
            var minimum = LogService.MinimumLevel;
            try
            {
                TestSettings(Path.Combine(directory, "log-level-schema"));
                TestFilters(Path.Combine(directory, "log-level-file"));
                TestPreference(Path.Combine(directory, "log-level-preference"), output);
                TestPreviewStartup(Path.Combine(directory, "log-level-preview-startup"));
                TestPreviewChannel(Path.Combine(directory, "log-level-preview-channel"), output);
                TestPolling(Path.Combine(directory, "log-level-polling"));
            }
            finally
            {
                LogManager.Configuration = configuration;
                LogService.SetMinimumLevel(minimum);
            }
        }

        private static void TestSettings(string directory)
        {
            var service = new SettingsService(directory);
            Assert(service.Load().LogLevel == "INFO", "new settings must default to INFO");
            var original = new UserSettings { PlayerName = "保留玩家", LogLevel = "DEBUG", MinimizeToTray = true };
            service.Save(original);
            Assert(service.Load().LogLevel == "DEBUG", "log level did not round trip");
            var path = Path.Combine(directory, "settings.json");
            var legacy = JObject.Parse(File.ReadAllText(path, Encoding.UTF8));
            legacy.Remove("logLevel");
            File.WriteAllText(path, legacy.ToString(), Encoding.UTF8);
            Assert(service.Load().LogLevel == "INFO" && service.Load().PlayerName == original.PlayerName,
                "legacy default lost unrelated settings");
            foreach (var value in new[] { "DEBUG", "INFO", "WARN", "ERROR" })
                Assert(SettingsSchema.IsValid(new UserSettings { LogLevel = value }), "valid log level rejected");
            foreach (var value in new[] { null, "", "WARNING", "TRACE", "info", "4" })
                Assert(!SettingsSchema.IsValid(new UserSettings { LogLevel = value }), "invalid log level accepted");
            service.Save(original);
            service.Save(new UserSettings { LogLevel = "ERROR" });
            var invalid = JObject.Parse(File.ReadAllText(path, Encoding.UTF8));
            invalid["logLevel"] = "unknown";
            File.WriteAllText(path, invalid.ToString(), Encoding.UTF8);
            Assert(service.Load().LogLevel == "DEBUG" && service.LastWarning.Contains("备份"),
                "invalid level did not follow backup recovery");
        }

        private static void TestFilters(string directory)
        {
            Directory.CreateDirectory(directory);
            var configuration = new XmlLoggingConfiguration(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "NLog.config"));
            var file = (FileTarget)configuration.FindTargetByName("file");
            file.FileName = Path.Combine(directory, "desktop.log");
            var memory = new MemoryTarget("levels") { Layout = "${date:format=yyyy-MM-dd HH\\:mm\\:ss}|${level:uppercase=true}|${logger}|${message}" };
            configuration.AddRule(LogLevel.Debug, LogLevel.Fatal, memory);
            LogManager.Configuration = configuration;
            var service = new LogService();
            for (var minimum = DiagnosticLogLevel.Debug; minimum <= DiagnosticLogLevel.Error; ++minimum)
            {
                LogService.SetMinimumLevel(minimum);
                memory.Logs.Clear();
                for (var level = DiagnosticLogLevel.Debug; level <= DiagnosticLogLevel.Error; ++level)
                {
                    var raw = "2026-01-02 03:04:05  " + DiagnosticLogLevels.Name(level) + "  [Journal] INFO stays in message";
                    var text = service.WriteRecord(raw);
                    Assert((text != null) == (level >= minimum), "UI threshold mismatch");
                    if (text != null)
                        Assert(text == "2026-01-02 03:04:05  " + DiagnosticLogLevels.DisplayName(level)
                            + "  [Journal] INFO stays in message", "Chinese severity changed timestamp, source or body");
                }
                Assert(memory.Logs.Count == 4 - (int)minimum && memory.Logs.All(line => line.Contains("|Journal|INFO stays in message")),
                    "NLog severity forwarding mismatch");
            }
            LogManager.Flush();
            string[] lines;
            using (var stream = new FileStream(Path.Combine(directory, "desktop.log"), FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            using (var reader = new StreamReader(stream, Encoding.UTF8))
                lines = reader.ReadToEnd().Split(new[] { Environment.NewLine }, StringSplitOptions.RemoveEmptyEntries);
            Assert(lines.Length == 10 && lines.Count(line => line.Contains("|DEBUG|")) == 1
                && lines.Count(line => line.Contains("|ERROR|")) == 4, "dynamic file threshold did not take effect");
            LogService.SetMinimumLevel(DiagnosticLogLevel.Warn);
            Assert(service.WriteRecord("2026-01-02 03:04:05  WARNING  [fixture] legacy").Contains("  警告  ")
                && memory.Logs.Last().Contains("|WARN|fixture|legacy"), "legacy WARNING was not normalized");
            Assert(service.WriteRecord("unstructured") == null, "unstructured fallback escaped INFO filtering");
        }

        private static MainWindowViewModel CreateModel(FakeLauncherBridge bridge, SettingsService service, UserSettings settings,
            PreviewAccessService previewAccess = null, FakeDesktopInteraction interaction = null)
            => new MainWindowViewModel(bridge,
                service,
                settings,
                null,
                new FakeClientEnvironment(path => Task.FromResult(ValidDirectory(path))),
                interaction ?? new FakeDesktopInteraction(),
                updates: new LauncherUpdateFactory(check: channel => Task.FromResult(new LauncherUpdateInfo { Channel = channel })),
                previewAccess: previewAccess);

        private static void TestPreference(string directory, string output)
        {
            var service = new SettingsService(directory);
            var bridge = new FakeLauncherBridge();
            using (var model = CreateModel(bridge, service,
                new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "保留玩家", LogLevel = "WARN" }))
            {
                RunTask(model.ValidationTask);
                Assert(model.SelectedLogLevel == DiagnosticLogLevel.Warn && bridge.LogLevel == DiagnosticLogLevel.Warn
                    && LogService.MinimumLevel == DiagnosticLogLevel.Warn, "saved startup threshold was not applied");
                model.IsSettingsSelected = true;
                model.SettingsTabIndex = 1;
                var window = new T7R.Desktop.MainWindow { DataContext = model };
                var page = (GameSettingsPage)window.FindName("SettingsPage");
                LauncherLayoutTests.Render((System.Windows.FrameworkElement)window.Content, window, output, "log-level-settings", 1200, 900);
                var selector = (ComboBox)page.FindName("LogLevelSelector");
                Assert(selector.Items.Cast<ComboBoxItem>().Select(item => (string)item.Content)
                    .SequenceEqual(new[] { "调试", "信息", "警告", "错误" }), "log level choices are not localized");
                Assert((DiagnosticLogLevel)selector.SelectedValue == DiagnosticLogLevel.Warn, "selector lost persisted level");
                bridge.Snapshot.State = SessionState.Running;
                selector.SelectedValue = DiagnosticLogLevel.Debug;
                Pump();
                Assert(model.SelectedLogLevel == DiagnosticLogLevel.Debug && bridge.LogLevel == DiagnosticLogLevel.Debug
                    && LogService.MinimumLevel == DiagnosticLogLevel.Debug && service.Load().LogLevel == "DEBUG",
                    "running session log level was not applied and saved");
                model.MinimizeToTray = true;
                model.SaveSettings();
                Assert(service.Load().LogLevel == "DEBUG" && service.Load().PlayerName == "保留玩家",
                    "other preferences lost the selected level");

                bridge.RejectedLogLevel = DiagnosticLogLevel.Error;
                selector.SelectedValue = DiagnosticLogLevel.Error;
                Pump();
                Assert((DiagnosticLogLevel)selector.SelectedValue == DiagnosticLogLevel.Debug
                    && bridge.LogLevel == DiagnosticLogLevel.Debug && LogService.MinimumLevel == DiagnosticLogLevel.Debug
                    && service.Load().LogLevel == "DEBUG" && model.PreferenceError.Length != 0,
                    "native failure changed persisted or displayed level");
                bridge.RejectedLogLevel = null;
                using (File.Open(Path.Combine(directory, "settings.json"), FileMode.Open, FileAccess.Read, FileShare.Read))
                {
                    selector.SelectedValue = DiagnosticLogLevel.Info;
                    Pump();
                    Assert((DiagnosticLogLevel)selector.SelectedValue == DiagnosticLogLevel.Debug
                        && bridge.LogLevel == DiagnosticLogLevel.Debug && LogService.MinimumLevel == DiagnosticLogLevel.Debug
                        && model.PreferenceError.Length != 0, "save failure did not restore runtime thresholds");
                }
                selector.SelectedValue = DiagnosticLogLevel.Info;
                Pump();
                Assert(service.Load().LogLevel == "INFO" && model.PreferenceError.Length == 0, "log preference could not retry");
            }
            using (var restarted = CreateModel(new FakeLauncherBridge(), service, service.Load()))
                Assert(restarted.SelectedLogLevel == DiagnosticLogLevel.Info, "restart lost selected log level");
        }

        private static void TestPreviewStartup(string directory)
        {
            foreach (var level in new[] { "DEBUG", "INFO", "WARN", "ERROR" })
            {
                var service = new SettingsService(Path.Combine(directory, level));
                service.Save(new UserSettings { UpdateChannel = "preview", LogLevel = level });
                var access = PreviewAccessFixture.Authorized(service.DirectoryPath);
                var bridge = new FakeLauncherBridge();
                using (var model = CreateModel(bridge, service, service.Load(), access))
                {
                    RunTask(model.ValidationTask);
                    Assert(model.SelectedUpdateChannel == UpdateChannel.Preview
                        && model.SelectedLogLevel == DiagnosticLogLevel.Debug && bridge.LogLevel == DiagnosticLogLevel.Debug
                        && LogService.MinimumLevel == DiagnosticLogLevel.Debug, "preview startup did not force DEBUG");
                    model.SelectedLogLevel = DiagnosticLogLevel.Error;
                    model.MinimizeToTray = true;
                    model.SaveSettings();
                    Assert(model.SelectedLogLevel == DiagnosticLogLevel.Debug && service.Load().LogLevel == "DEBUG",
                        "preview startup allowed another level to be applied or saved");
                }
            }
        }

        private static void TestPreviewChannel(string directory, string output)
        {
            var service = new SettingsService(directory);
            service.Save(new UserSettings { LogLevel = "WARN", PlayerName = "保留玩家" });
            var bridge = new FakeLauncherBridge();
            var interaction = new FakeDesktopInteraction { ConfirmResult = false };
            using (var fixture = new PreviewAccessFixture())
            {
                var access = fixture.Create(directory);
                Assert(access.TryImport(fixture.Issue(), out _), "preview log fixture authorization failed");
                using (var model = CreateModel(bridge, service, service.Load(), access, interaction))
                {
                    RunTask(model.ValidationTask);
                    model.IsSettingsSelected = true;
                    model.SettingsTabIndex = 1;
                    var window = new T7R.Desktop.MainWindow { DataContext = model };
                    var page = (GameSettingsPage)window.FindName("SettingsPage");
                    LauncherLayoutTests.Render((System.Windows.FrameworkElement)window.Content, window, null, "", 1200, 900);
                    var selector = (ComboBox)page.FindName("LogLevelSelector");
                    Assert(selector.IsEnabled, "stable channel locked the log selector");

                    model.SelectedUpdateChannel = UpdateChannel.Preview;
                    Pump();
                    Assert(model.SelectedUpdateChannel == UpdateChannel.Stable && selector.IsEnabled
                        && model.SelectedLogLevel == DiagnosticLogLevel.Warn && service.Load().LogLevel == "WARN",
                        "cancelled preview switch changed the log level");
                    interaction.ConfirmResult = true;
                    bridge.RejectedLogLevel = DiagnosticLogLevel.Debug;
                    model.SelectedUpdateChannel = UpdateChannel.Preview;
                    Pump();
                    Assert(model.SelectedUpdateChannel == UpdateChannel.Stable && service.Load().UpdateChannel == "stable"
                        && model.SelectedLogLevel == DiagnosticLogLevel.Warn && bridge.LogLevel == DiagnosticLogLevel.Warn
                        && LogService.MinimumLevel == DiagnosticLogLevel.Warn && model.PreferenceError.Length != 0,
                        "preview switch ignored native log level failure");
                    bridge.RejectedLogLevel = null;
                    using (File.Open(Path.Combine(directory, "settings.json"), FileMode.Open, FileAccess.Read, FileShare.Read))
                    {
                        model.SelectedUpdateChannel = UpdateChannel.Preview;
                        Pump();
                        Assert(model.SelectedUpdateChannel == UpdateChannel.Stable && selector.IsEnabled
                            && model.SelectedLogLevel == DiagnosticLogLevel.Warn && bridge.LogLevel == DiagnosticLogLevel.Warn
                            && LogService.MinimumLevel == DiagnosticLogLevel.Warn && service.Load().LogLevel == "WARN"
                            && model.PreferenceError.Length != 0, "failed preview save did not restore runtime log levels");
                    }

                    model.SelectedUpdateChannel = UpdateChannel.Preview;
                    RunTask(model.About.CheckUpdateCommand.ExecutionTask);
                    model.IsSettingsSelected = true;
                    LauncherLayoutTests.Render((System.Windows.FrameworkElement)window.Content, window, output,
                        "preview-log-level-settings", 1200, 900);
                    Assert(model.SelectedUpdateChannel == UpdateChannel.Preview && !selector.IsEnabled
                        && (DiagnosticLogLevel)selector.SelectedValue == DiagnosticLogLevel.Debug
                        && bridge.LogLevel == DiagnosticLogLevel.Debug && LogService.MinimumLevel == DiagnosticLogLevel.Debug
                        && service.Load().LogLevel == "DEBUG" && service.Load().UpdateChannel == "preview"
                        && service.Load().PlayerName == "保留玩家", "preview switch did not lock and persist DEBUG");
                    foreach (var level in new[] { DiagnosticLogLevel.Info, DiagnosticLogLevel.Warn, DiagnosticLogLevel.Error })
                    {
                        model.SelectedLogLevel = level;
                        selector.SelectedValue = level;
                        Pump();
                        Assert(model.SelectedLogLevel == DiagnosticLogLevel.Debug
                            && (DiagnosticLogLevel)selector.SelectedValue == DiagnosticLogLevel.Debug
                            && bridge.LogLevel == DiagnosticLogLevel.Debug && LogService.MinimumLevel == DiagnosticLogLevel.Debug
                            && service.Load().LogLevel == "DEBUG", "preview log lock accepted a lower verbosity");
                    }
                    model.MinimizeToTray = true;
                    model.SaveSettings();
                    Assert(service.Load().LogLevel == "DEBUG", "saving preview preferences lost the log lock");

                    model.SelectedUpdateChannel = UpdateChannel.Stable;
                    RunTask(model.About.CheckUpdateCommand.ExecutionTask);
                    Pump();
                    Assert(selector.IsEnabled, "returning to stable did not unlock log selection");
                    selector.SelectedValue = DiagnosticLogLevel.Error;
                    Pump();
                    Assert(model.SelectedLogLevel == DiagnosticLogLevel.Error && bridge.LogLevel == DiagnosticLogLevel.Error
                        && LogService.MinimumLevel == DiagnosticLogLevel.Error && service.Load().LogLevel == "ERROR",
                        "stable channel could not change the log level after leaving preview");

                    model.SelectedUpdateChannel = UpdateChannel.Preview;
                    RunTask(model.About.CheckUpdateCommand.ExecutionTask);
                    Pump();
                    Assert(!selector.IsEnabled && bridge.LogLevel == DiagnosticLogLevel.Debug, "re-entering preview lost the log lock");
                    fixture.Now = fixture.Now.AddDays(8);
                    model.CheckPreviewAccess();
                    RunTask(model.About.CheckUpdateCommand.ExecutionTask);
                    Pump();
                    Assert(model.SelectedUpdateChannel == UpdateChannel.Stable && selector.IsEnabled,
                        "expired preview access left the log selector locked");
                }
            }
        }

        private static void TestPolling(string directory)
        {
            var bridge = new FakeLauncherBridge();
            using (var model = CreateModel(bridge, new SettingsService(directory), new UserSettings { ClientDirectory = @"C:\Games\T7" }))
            {
                RunTask(model.ValidationTask);
                bridge.LogLines = new[] {
                    "2026-01-02 03:04:05  DEBUG  [NativeBridge] hidden-debug",
                    "2026-01-02 03:04:05  INFO  [Journal] retained-info"
                };
                WaitUntil(() => model.NativeLogText.Contains("retained-info"));
                Assert(!model.NativeLogText.Contains("hidden-debug") && model.NativeLogText.Contains("  信息  [Journal]"),
                    "default UI filtering or localization failed");
                model.SelectedLogLevel = DiagnosticLogLevel.Error;
                bridge.LogLines = bridge.LogLines.Concat(new[] {
                    "2026-01-02 03:04:06  WARN  [NativeBridge] hidden-warning",
                    "2026-01-02 03:04:06  ERROR  [NativeBridge] visible-error"
                }).ToArray();
                WaitUntil(() => model.NativeLogText.Contains("visible-error"));
                Assert(model.NativeLogText.Contains("retained-info") && !model.NativeLogText.Contains("hidden-warning")
                    && model.NativeLogText.Contains("  错误  [NativeBridge]"), "level change rewrote history or leaked warnings");
                model.SelectedLogLevel = DiagnosticLogLevel.Debug;
                Assert(!model.NativeLogText.Contains("hidden-debug"), "level change backfilled discarded logs");
            }
        }

        private static void WaitUntil(Func<bool> ready)
        {
            var deadline = DateTime.UtcNow.AddSeconds(5);
            while (!ready() && DateTime.UtcNow < deadline) { Pump(); System.Threading.Thread.Sleep(10); }
            Assert(ready(), "log poll did not complete");
        }
    }
}
