using System;
using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Threading;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class UpdateDownloadViewModelTests
    {
        internal static void Run()
        {
            var previous = SynchronizationContext.Current;
            SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
            var directory = Path.Combine(Path.GetTempPath(), "T7-update-view-model-tests-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            try
            {
                var installer = Path.Combine(directory, "Setup.exe");
                File.WriteAllBytes(installer, UpdateFixtures.Payload);
                TestDownloadAndCancel(installer);
                TestDownloadAndInstall(installer);
                TestInstallerStartInfo(installer);
                RunTask(TestInstallationAsync(installer));
                TestInstallationEndsGame(installer, directory);
            }
            finally
            {
                SynchronizationContext.SetSynchronizationContext(previous);
                Directory.Delete(directory, true);
            }
        }

        private static void TestInstallerStartInfo(string installer)
        {
            foreach (var directory in new[] { @"C:\Games\T7", @"C:\Games\重燃 Launcher\", @"C:\" })
            {
                var start = UpdateInstallationService.CreateStartInfo(installer, directory, 1234);
                Assert(start.FileName == installer && start.UseShellExecute
                    && start.Arguments.Contains("/SILENT") && start.Arguments.Contains("/SP-")
                    && start.Arguments.Contains("/NORESTART") && start.Arguments.Contains("/LAUNCHERPID=1234")
                    && start.Arguments.Contains("/DIR=\"" + directory + "\""),
                    "in-app installer lost its current directory, visible progress or launcher exit dependency");
                Assert(!start.Arguments.Contains("/VERYSILENT") && !start.Arguments.Contains("/SUPPRESSMSGBOXES"),
                    "in-app installer hid progress or installation errors");
            }
        }

        internal static LauncherUpdateInfo Info() => new LauncherUpdateInfo
        {
            CurrentVersion = "v1.0.0", TargetVersion = "v1.2.3", Summary = "发布说明", IsNewVersion = true,
            DownloadAddress = UpdateFixtures.Repository + "/releases/tag/v1.2.3", Installer = UpdateFixtures.Asset()
        };

        private static void TestDownloadAndCancel(string installer)
        {
            var downloads = 0;
            var installs = 0;
            using (var model = new UpdateDownloadViewModel(Info(), (asset, progress, token, control) =>
            {
                downloads++;
                progress.Report(new UpdateDownloadProgress(12, asset.Size, "R2", ""));
                var pending = new TaskCompletionSource<string>();
                token.Register(() => pending.TrySetCanceled());
                return pending.Task;
            }, path => { installs++; return Task.FromResult(true); }, address => { throw new Exception("browser opened"); }))
            {
                var operation = model.PrimaryCommand.ExecuteAsync(null);
                Pump();
                Assert(model.IsDownloading && !model.PrimaryCommand.CanExecute(null)
                    && model.PauseDownloadCommand.CanExecute(null) && model.CancelDownloadCommand.CanExecute(null) && model.Percent > 0,
                    "download state did not expose progress and cancellation");
                model.PauseDownloadCommand.Execute(null);
                Assert(model.IsPaused && model.PauseActionText == "继续下载" && model.CancelDownloadCommand.CanExecute(null),
                    "paused download lost its resume or cancellation action");
                model.PauseDownloadCommand.Execute(null);
                Assert(!model.IsPaused && model.PauseActionText == "暂停下载", "download did not resume");
                model.PauseDownloadCommand.Execute(null);
                RunTask(model.CancelAndWaitAsync());
                RunTask(operation);
                Assert(!model.IsDownloading && !model.IsPaused && model.StatusText.Contains("取消") && model.ErrorText.Length == 0
                    && !model.HasDownloadedInstaller && installs == 0 && downloads == 1,
                    "cancellation started installation, reported an error or left busy state");
            }
        }

        private static void TestDownloadAndInstall(string installer)
        {
            var downloads = 0;
            var installs = 0;
            using (var model = new UpdateDownloadViewModel(Info(), (asset, progress, token, control) =>
            {
                downloads++;
                return downloads == 1 ? Task.FromException<string>(new IOException("disk fixture"))
                    : Task.FromResult(installer);
            }, path =>
            {
                Assert(path == installer, "installation received a different path");
                installs++;
                return installs == 1 ? Task.FromResult(false)
                    : Task.FromException<bool>(new FileNotFoundException("installer removed"));
            }, address => { throw new Exception("browser opened"); }))
            {
                RunTask(model.PrimaryCommand.ExecuteAsync(null));
                Assert(model.ErrorText.Contains("disk fixture") && model.PrimaryCommand.CanExecute(null),
                    "download failure did not allow retry");
                RunTask(model.PrimaryCommand.ExecuteAsync(null));
                Assert(model.HasDownloadedInstaller && model.Percent == 100 && model.PrimaryActionText == "立即安装"
                    && installs == 0 && model.ErrorText.Length == 0, "download automatically ran the installer");
                RunTask(model.PrimaryCommand.ExecuteAsync(null));
                Assert(installs == 1 && downloads == 2 && model.HasDownloadedInstaller && model.CanClose
                    && model.StatusText.Contains("取消"), "declining installation lost the verified file");
                RunTask(model.PrimaryCommand.ExecuteAsync(null));
                Assert(!model.HasDownloadedInstaller && model.PrimaryActionText == "下载更新",
                    "a removed cached installer could not be downloaded again");
            }
        }

        private static async Task TestInstallationAsync(string path)
        {
            var calls = new List<string>();
            var allowClose = false;
            var coordinator = new UpdateInstallationService(
                () => { calls.Add("confirm"); return Task.FromResult(allowClose); },
                () => calls.Add("save"), installer => calls.Add("start"), () => calls.Add("close"));
            Assert(!await coordinator.InstallAsync(path) && string.Join(",", calls) == "confirm",
                "declining session cleanup started installation");
            calls.Clear();
            allowClose = true;
            Assert(await coordinator.InstallAsync(path) && string.Join(",", calls) == "confirm,save,start,close",
                "installer handoff skipped or reordered session cleanup");
            foreach (var failure in new[] { "cleanup", "save", "start" })
            {
                calls.Clear();
                coordinator = new UpdateInstallationService(
                    () => failure == "cleanup" ? Task.FromException<bool>(new IOException(failure)) : Task.FromResult(true),
                    () => { if (failure == "save") throw new IOException(failure); calls.Add("save"); },
                    installer => { if (failure == "start") throw new IOException(failure); calls.Add("start"); },
                    () => calls.Add("close"));
                await UpdateDownloadTests.ExpectAsync<IOException>(() => coordinator.InstallAsync(path));
                Assert(!calls.Contains("close") && !calls.Contains("start"),
                    "failed handoff closed the app or started installation");
            }
            var pending = new TaskCompletionSource<bool>();
            calls.Clear();
            coordinator = new UpdateInstallationService(() => pending.Task, () => calls.Add("save"),
                installer => calls.Add("start"), () => calls.Add("close"));
            var first = coordinator.InstallAsync(path);
            Assert(calls.Count == 0, "installer started before game session cleanup completed");
            Assert(!await coordinator.InstallAsync(path), "concurrent installation was accepted");
            pending.SetResult(true);
            Assert(await first && string.Join(",", calls) == "save,start,close", "installation ran more than once");
        }

        private static void TestInstallationEndsGame(string installer, string directory)
        {
            var bridge = new FakeLauncherBridge();
            var interaction = new FakeDesktopInteraction { ConfirmResult = false };
            using (var model = new MainWindowViewModel(bridge, new SettingsService(directory),
                new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "玩家" }, null,
                path => Task.FromResult(ValidDirectory(path)), interaction,
                checkUpdate: () => Task.FromResult(new LauncherUpdateInfo())))
            {
                RunTask(model.ValidationTask);
                RunTask(model.StartCommand.ExecuteAsync(null));
                var calls = new List<string>();
                var installation = new UpdateInstallationService(model.RequestCloseAsync, () => calls.Add("save"),
                    path =>
                    {
                        Assert(model.CanClose && !model.IsManagedGameRunning && bridge.Snapshot.CleanupComplete,
                            "installer started while the game or its session was still running");
                        calls.Add("start");
                    }, () => calls.Add("close"));
                RunTask(installation.InstallAsync(installer));
                Assert(model.IsManagedGameRunning && calls.Count == 0,
                    "declining game shutdown changed files or started installation");
                interaction.ConfirmResult = true;
                RunTask(installation.InstallAsync(installer));
                Assert(string.Join(",", calls) == "save,start,close" && model.CanClose,
                    "in-app installation did not clean up the game before installer handoff");
            }
        }
    }
}
