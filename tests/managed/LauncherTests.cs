using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Threading;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;

namespace T7.ManagedHarness
{
    internal static class LauncherTests
    {
        internal static void Run()
        {
            var context = SynchronizationContext.Current;
            SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
            var directory = Path.Combine(Path.GetTempPath(), "T7-launcher-tests-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            try
            {
                TestNames();
                TestDirectories(directory);
                ClientDirectorySearchTests.Run(directory);
                DirectorySelectionTests.Run(directory);
                TestPersistence(directory);
                TestSettingsNavigation(directory);
                TestStartPreflight(directory);
                TestLifecycle(directory);
                TestLatestValidation(directory);
                TestDelayedOperations(directory);
                TestSaveFailure(directory);
                TestNoticeSeverity(directory);
                TestUpdates();
            }
            finally
            {
                SynchronizationContext.SetSynchronizationContext(context);
                Directory.Delete(directory, true);
            }
        }

        private static void TestNames()
        {
            foreach (var name in new[] { "重燃玩家", "Rekindler", new string('界', 15) + "A", new string('A', 31) })
                Assert(PlayerNameRules.Validate("  " + name + "  ") == "", "valid GBK name rejected");
            foreach (var name in new[] { "", "  ", "a\0b", "a\nb", "a\tb", new string('界', 16), new string('A', 32), "😀", "€", "\uE000", "\uD800" })
                Assert(PlayerNameRules.Validate(name).Length != 0, "invalid GBK name accepted");
            Assert(!SettingsSchema.IsValid(new UserSettings { PlayerName = "😀" }), "settings accepted invalid name");
        }

        private static void TestDirectories(string directory)
        {
            var root = Path.Combine(directory, "game");
            var bin = Path.Combine(root, "Bin");
            Directory.CreateDirectory(bin);
            Assert(!ClientDirectoryService.Inspect(bin).IsValid, "a directory named Bin was normalized without evidence");
            File.WriteAllText(Path.Combine(bin, "TieJiClient.exe"), "fixture");
            File.WriteAllText(Path.Combine(bin, "ProtocalHandler.dll"), "fixture");
            Assert(!ClientDirectoryService.Inspect(root).IsValid, "missing resources accepted");
            Directory.CreateDirectory(Path.Combine(root, "Data"));
            Directory.CreateDirectory(Path.Combine(root, "vfs"));
            var fromRoot = ClientDirectoryService.Inspect(root);
            var fromBin = ClientDirectoryService.Inspect(bin);
            Assert(fromRoot.IsValid && fromBin.IsValid && fromBin.Root == root && fromRoot.Directory == bin, "root/Bin recognition failed");
            Assert(ClientDirectoryService.Inspect(bin + "\\").Root == root, "trailing Bin separator broke root recognition");
            Assert(ClientDirectoryService.Inspect(root + "\\").Root == root, "trailing root separator was not normalized");
            Assert(!ClientDirectoryService.Inspect("relative\\client").IsValid, "relative path accepted");
            Assert(!ClientDirectoryService.Inspect(root + "\0").IsValid, "NUL path accepted");
            Assert(!ClientDirectoryService.Inspect("").IsValid, "empty path accepted");
        }

        private static void TestPersistence(string directory)
        {
            var service = new SettingsService(Path.Combine(directory, "persistence"));
            service.Save(new UserSettings { ClientDirectory = @"C:\Games\First", PlayerName = "玩家甲" });
            service.Save(new UserSettings { ClientDirectory = @"C:\Games\Second", PlayerName = "玩家乙" });
            File.WriteAllText(Path.Combine(directory, "persistence", "settings.json"), "not-json");
            var recovered = service.Load();
            Assert(recovered.PlayerName == "玩家甲" && recovered.ClientDirectory == @"C:\Games\First"
                && service.LastWarning.Length != 0, "settings backup recovery failed");
            File.WriteAllText(Path.Combine(directory, "persistence", "settings.json"),
                "{\"schemaVersion\":1,\"clientDirectory\":\"\",\"windowWidth\":760,\"windowHeight\":520,\"darkTheme\":false}");
            Assert(service.Load().PlayerName == "" && service.LastWarning.Length == 0, "old settings migration failed");
            File.WriteAllText(Path.Combine(directory, "persistence", "settings.json"), "{}{}");
            Assert(service.Load().PlayerName == "玩家甲" && service.LastWarning.Length != 0, "trailing JSON was accepted");
        }

        internal static ClientDirectoryResult ValidDirectory(string path) => new ClientDirectoryResult(path, Path.Combine(path, "Bin"), "已找到 Bin\\TieJiClient.exe");

        private static void TestSettingsNavigation(string directory)
        {
            var bridge = new FakeLauncherBridge();
            var service = new SettingsService(Path.Combine(directory, "settings-navigation"));
            using (var model = new MainWindowViewModel(bridge, service, new UserSettings { PlayerName = "玩家" }, null,
                path => Task.FromResult(path.Length == 0 ? new ClientDirectoryResult("", "", "请选择目录。") : ValidDirectory(path)),
                new FakeDesktopInteraction()))
            {
                RunTask(model.ValidationTask);
                Assert(model.IsHomeSelected && !model.IsSettingsSelected && !model.IsAboutSelected, "initial page selection is invalid");
                Assert(model.DiagnosticText.Contains("游戏设置"), "missing directory did not point to game settings");
                var selectionChanges = 0;
                model.PropertyChanged += (_, change) => { if (change.PropertyName == nameof(model.IsSettingsSelected)) selectionChanges++; };
                model.IsSettingsSelected = true;
                Assert(model.IsSettingsSelected && !model.IsHomeSelected && !model.IsAboutSelected && selectionChanges == 1,
                    "settings tab did not select its page exclusively");
                model.IsSettingsSelected = false;
                Assert(model.IsSettingsSelected, "unchecked navigation changed the active page");
                model.ClientDirectory = @"C:\Games\T7";
                RunTask(model.ValidationTask);
                Assert(model.CanStart && service.Load().ClientDirectory == @"C:\Games\T7", "directory edit was not validated and saved");
                RunTask(model.CheckCommand.ExecuteAsync(null));
                Assert(model.IsSettingsSelected && bridge.CheckCount == 1, "manual check left the settings page");
                model.IsAboutSelected = true;
                Assert(model.IsAboutSelected && !model.IsSettingsSelected && !model.IsHomeSelected && selectionChanges == 2,
                    "about tab did not deselect game settings");
                model.ShowHomeCommand.Execute(null);
                Assert(model.IsHomeSelected && !model.IsSettingsSelected && !model.IsAboutSelected && selectionChanges == 3,
                    "status link did not return to the launch page");
                Assert(model.ClientDirectory == @"C:\Games\T7" && model.PlayerName == "玩家" && model.CanStart,
                    "page navigation lost shared configuration");
            }
        }

        private static void TestStartPreflight(string directory)
        {
            foreach (var status in new[] { OperationStatus.Succeeded, OperationStatus.Failed, OperationStatus.Cancelled })
            {
                var bridge = new FakeLauncherBridge { PendingCheck = new TaskCompletionSource<OperationSnapshot>() };
                var settings = new SettingsService(Path.Combine(directory, "start-preflight-" + status));
                using (var model = new MainWindowViewModel(bridge, settings,
                    new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "玩家" }, null,
                    path => Task.FromResult(ValidDirectory(path)), new FakeDesktopInteraction()))
                {
                    RunTask(model.ValidationTask);
                    var start = model.StartCommand.ExecuteAsync(null);
                    Pump();
                    Assert(bridge.CheckCount == 1 && bridge.StartCount == 0 && !start.IsCompleted,
                        "start did not await native preflight");
                    Assert(model.AreSessionFieldsLocked && !model.CanStart,
                        "start preflight allowed a concurrent launch");
                    bridge.Snapshot = new SessionSnapshot { State = SessionState.Idle, CleanupComplete = true };
                    bridge.PendingCheck.SetResult(new OperationSnapshot
                    {
                        Status = status,
                        Error = status == OperationStatus.Failed ? "synthetic preflight failure" : string.Empty
                    });
                    RunTask(start);
                    Assert(bridge.StartCount == (status == OperationStatus.Succeeded ? 1 : 0),
                        "client launch did not follow the native preflight result");
                    if (status == OperationStatus.Succeeded)
                        Assert(bridge.StartedDirectory == @"C:\Games\T7\Bin" && bridge.StartedName == "玩家",
                            "native launch did not receive the selected client and player");
                }
            }
        }

        private static void TestLifecycle(string directory)
        {
            var bridge = new FakeLauncherBridge();
            var interaction = new FakeDesktopInteraction();
            var service = new SettingsService(Path.Combine(directory, "lifecycle"));
            using (var model = new MainWindowViewModel(bridge, service, new UserSettings(), null,
                path => Task.FromResult(path.Length == 0 ? new ClientDirectoryResult("", "", "请选择目录。") : ValidDirectory(path)), interaction))
            {
                RunTask(model.ValidationTask);
                Assert(!model.StartCommand.CanExecute(null), "first run must disable launch");
                Assert(model.HasPlayerNameError && !model.ShowPlayerNameError, "untouched empty name appeared as an error");
                model.PlayerName = " ";
                Assert(model.ShowPlayerNameError && !model.CanStart, "edited empty name did not show validation feedback");
                model.ClientDirectory = @"C:\Games\T7";
                model.PlayerName = "  重燃玩家  ";
                RunTask(model.ValidationTask);
                Assert(model.CanStart && service.Load().PlayerName == "重燃玩家", "valid fields were not saved");
                model.IsSettingsSelected = true;
                RunTask(model.StartCommand.ExecuteAsync(null));
                Assert(model.SelectedPage == 0 && bridge.StartedName == "重燃玩家" && bridge.StartedDirectory == @"C:\Games\T7\Bin",
                    "launch did not preserve the normalized directory/name or return to home");
                Assert(bridge.CheckCount == 1 && model.IsBusy && model.AreSessionFieldsLocked && !model.CanCancel
                    && !model.StartCommand.CanExecute(null) && model.CanStop, "running command state is invalid");
                model.PlayerName = "不应生效";
                model.IsSettingsSelected = true;
                model.ClientDirectory = @"C:\Games\Other";
                Assert(model.PlayerName == "重燃玩家" && model.ClientDirectory == @"C:\Games\T7"
                    && !model.BrowseCommand.CanExecute(null) && !model.CheckCommand.CanExecute(null), "running session fields were changed");
                model.SelectedPage = 1;
                Assert(model.StatusText == "游戏进程运行中", "page switch reset the session");
                interaction.ConfirmResult = false;
                RunTask(model.StopCommand.ExecuteAsync(null));
                Assert(model.IsBusy, "stop ignored negative confirmation");
                interaction.ConfirmResult = true;
                RunTask(model.StopCommand.ExecuteAsync(null));
                Assert(!model.IsBusy && model.CanStart && interaction.ConfirmCount == 2, "stop confirmation failed");

                bridge.HoldStart = true;
                var start = model.StartCommand.ExecuteAsync(null);
                Pump();
                Assert(model.CanCancel && model.AreSessionFieldsLocked, "startup cancellation state is invalid");
                model.CancelCommand.Execute(null);
                RunTask(start);
                Assert(bridge.Cancelled && !model.IsBusy && !model.CanCancel, "cancel did not await cleanup");
                bridge.HoldStart = false;
                bridge.Failure = "synthetic startup error";
                RunTask(model.StartCommand.ExecuteAsync(null));
                Assert(model.LogsExpanded && model.StatusTone == "Danger" && model.MainActionText == "重试启动", "failure presentation is invalid");
                bridge.Failure = null;
                RunTask(model.StartCommand.ExecuteAsync(null));
                bridge.Snapshot = new SessionSnapshot { State = SessionState.Failed, ErrorCode = 1003, CleanupComplete = true };
                model.Refresh();
                Assert(model.StatusText == "游戏异常退出" && model.CanStart, "abnormal exit was not distinguished");
                RunTask(model.StartCommand.ExecuteAsync(null));
                bridge.Snapshot = new SessionSnapshot { State = SessionState.Idle, Phase = "client-exited", CleanupComplete = true };
                model.Refresh();
                Assert(model.StatusText == "游戏已正常退出" && model.MainActionText == "再次启动", "normal exit was not distinguished");
                var previous = service.Load().ClientDirectory;
                model.ClientDirectory = "";
                RunTask(model.ValidationTask);
                model.SaveSettings(800, 600);
                Assert(service.Load().ClientDirectory == previous, "invalid edit replaced the last valid directory");
            }
        }

        private static void TestLatestValidation(string directory)
        {
            var first = new TaskCompletionSource<ClientDirectoryResult>();
            using (var model = new MainWindowViewModel(new FakeLauncherBridge(), new SettingsService(Path.Combine(directory, "latest")),
                new UserSettings { ClientDirectory = @"C:\Old", PlayerName = "Player" }, null,
                path => path == @"C:\Old" ? first.Task : Task.FromResult(ValidDirectory(path)), new FakeDesktopInteraction()))
            {
                var oldTask = model.ValidationTask;
                model.ClientDirectory = @"C:\New";
                RunTask(model.ValidationTask);
                first.SetResult(ValidDirectory(@"C:\Old"));
                RunTask(oldTask);
                Assert(model.ClientDirectory == @"C:\New" && model.IsDirectoryValid, "stale directory result replaced latest input");
            }
        }

        private static void TestSaveFailure(string directory)
        {
            var blocked = Path.Combine(directory, "not-a-directory");
            File.WriteAllText(blocked, "fixture");
            using (var model = new MainWindowViewModel(new FakeLauncherBridge(), new SettingsService(blocked), new UserSettings(), null,
                path => Task.FromResult(ValidDirectory(path)), new FakeDesktopInteraction()))
            {
                model.ClientDirectory = @"C:\Game";
                model.PlayerName = "玩家";
                RunTask(model.ValidationTask);
                Assert(model.SettingsFeedback.StartsWith("保存设置失败"), "save failure was reported as success");
                RunTask(model.StartCommand.ExecuteAsync(null));
                Assert(model.StatusTone == "Danger" && !model.IsBusy, "save failure was ignored before launch");
            }
        }

        private static void TestDelayedOperations(string directory)
        {
            var bridge = new FakeLauncherBridge { PendingCheck = new TaskCompletionSource<OperationSnapshot>() };
            using (var model = new MainWindowViewModel(bridge, new SettingsService(Path.Combine(directory, "delayed")),
                new UserSettings { ClientDirectory = @"C:\Old", PlayerName = "Player" }, null,
                path => Task.FromResult(ValidDirectory(path)), new FakeDesktopInteraction()))
            {
                RunTask(model.ValidationTask);
                var pending = model.CheckCommand.ExecuteAsync(null);
                Assert(!model.AreSessionFieldsLocked && !model.CanStart, "manual check did not allow edits or blocked launch incorrectly");
                model.ClientDirectory = @"C:\New";
                RunTask(model.ValidationTask);
                bridge.Snapshot = new SessionSnapshot { State = SessionState.Failed, ErrorCode = 1000, CleanupComplete = true };
                bridge.PendingCheck.SetResult(new OperationSnapshot { Status = OperationStatus.Failed, Error = "old input failed" });
                RunTask(pending);
                Assert(model.ClientDirectory == @"C:\New" && model.StatusTone == "Success", "old native check failure replaced new validation");
                bridge.PendingCheck = null;
                bridge.PendingStart = new TaskCompletionSource<OperationSnapshot>();
                pending = model.StartCommand.ExecuteAsync(null);
                bridge.Snapshot = new SessionSnapshot { State = SessionState.Failed, ErrorCode = 1000, CleanupComplete = true };
                bridge.PendingStart.SetResult(new OperationSnapshot { Status = OperationStatus.Failed, Error = "specific startup failure" });
                RunTask(pending);
                Assert(model.StatusText == "启动失败" && model.DiagnosticText.Contains("specific startup failure"), "late failure lost its stage or reason");
            }
        }

        private static void TestNoticeSeverity(string directory)
        {
            var interaction = new FakeDesktopInteraction { DirectoryError = new IOException("fixture error") };
            using (var model = new MainWindowViewModel(new FakeLauncherBridge(), new SettingsService(Path.Combine(directory, "notices")),
                new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "Player" }, "设置恢复提示",
                path => Task.FromResult(ValidDirectory(path)), interaction))
            {
                RunTask(model.ValidationTask);
                Assert(model.HasNotice && model.IsNoticeError, "settings warning lost its error presentation");
                model.BrowseCommand.Execute(null);
                Assert(model.IsNoticeError && model.NoticeText.Contains("fixture error"), "directory dialog error was not reported");
                model.CopyLogsCommand.Execute(null);
                Assert(model.HasNotice && !model.IsNoticeError && model.NoticeText.Contains("已复制"), "successful copy appeared as an error");
                interaction.ConfirmResult = false;
                model.CopyLogsCommand.Execute(null);
                Assert(!model.IsNoticeError, "cancelled copy changed notice severity");
                Assert(interaction.Text.Contains("本次运行的诊断信息"), "copy confirmation still refers to inline logs");
            }
        }

        private static void TestUpdates()
        {
            var interaction = new FakeDesktopInteraction();
            var about = new AboutViewModel(interaction);
            var checkingWhenResultShown = true;
            interaction.OnShowUpdate = () => checkingWhenResultShown = about.IsCheckingUpdate;
            RunTask(about.CheckUpdateCommand.ExecuteAsync(null));
            Assert(about.UpdateStatus == "发现新版本" && interaction.Update.IsNewVersion, "update result caption is invalid");
            Assert(!checkingWhenResultShown, "update result dialog still displayed the checking state");
            about.ShowRepositoryCommand.Execute(null);
            Assert(interaction.Text == about.RepositoryAddress, "repository address contains presentation annotations");
            about.CopyRepositoryCommand.Execute(null);
            Assert(about.Feedback == "仓库地址已复制。", "repository copy feedback is invalid");
            about.ShowChangelogCommand.Execute(null);
            Assert(interaction.Text.Contains("0.1.0") && interaction.IsMarkdown, "offline changelog missing or not rendered as Markdown");
            about.ShowLicensesCommand.Execute(null);
            Assert(interaction.Text.Contains("MIT License") && !interaction.IsMarkdown, "offline license missing or no longer plain text");
            about.ShowThanksCommand.Execute(null);
            Assert(interaction.Text.Contains("贡献者") && interaction.IsMarkdown, "thanks document missing or not rendered as Markdown");
            about = new AboutViewModel(interaction, () => Task.FromException<LauncherUpdateInfo>(new IOException("fixture failure")));
            RunTask(about.CheckUpdateCommand.ExecuteAsync(null));
            Assert(!about.IsCheckingUpdate && about.UpdateStatus.Contains("失败"), "update failure not handled");
            about = new AboutViewModel(interaction, () => Task.FromResult(new LauncherUpdateInfo { IsNewVersion = false }));
            RunTask(about.CheckUpdateCommand.ExecuteAsync(null));
            Assert(about.UpdateStatus.Contains("最新"), "up-to-date state missing");
        }

        internal static void RunTask(Task task)
        {
            var deadline = DateTime.UtcNow.AddSeconds(8);
            while (!task.IsCompleted && DateTime.UtcNow < deadline) { Pump(); Thread.Sleep(5); }
            Assert(task.IsCompleted, "launcher test timed out");
            task.GetAwaiter().GetResult();
            Pump();
        }

        internal static void Pump()
        {
            var frame = new DispatcherFrame();
            Dispatcher.CurrentDispatcher.BeginInvoke(DispatcherPriority.Background, new Action(() => frame.Continue = false));
            Dispatcher.PushFrame(frame);
        }

        internal static void Assert(bool condition, string message)
        {
            if (!condition) throw new InvalidOperationException(message);
        }
    }
}
