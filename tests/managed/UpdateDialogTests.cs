using System;
using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Threading;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class UpdateDialogTests
    {
        internal static void Run()
        {
            var previous = SynchronizationContext.Current;
            SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
            var directory = Path.Combine(Path.GetTempPath(), "T7-update-dialog-tests-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            try
            {
                var installer = Path.Combine(directory, "Setup.exe");
                File.WriteAllBytes(installer, UpdateFixtures.Payload);
                TestDownloadAndCancel(installer);
                TestDownloadAndInstall(installer);
                RunTask(TestInstallationAsync(installer));
            }
            finally
            {
                SynchronizationContext.SetSynchronizationContext(previous);
                Directory.Delete(directory, true);
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
            using (var model = new UpdateDialogViewModel(Info(), (asset, progress, token) =>
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
                    && model.CancelDownloadCommand.CanExecute(null) && model.Percent > 0,
                    "download state did not expose progress and cancellation");
                RunTask(model.CancelAndWaitAsync());
                RunTask(operation);
                Assert(!model.IsDownloading && model.StatusText.Contains("取消") && model.ErrorText.Length == 0
                    && !model.HasDownloadedInstaller && installs == 0 && downloads == 1,
                    "cancellation started installation, reported an error or left busy state");
            }
        }

        private static void TestDownloadAndInstall(string installer)
        {
            var downloads = 0;
            var installs = 0;
            using (var model = new UpdateDialogViewModel(Info(), (asset, progress, token) =>
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
                Assert(!model.HasDownloadedInstaller && model.PrimaryActionText == "下载安装版",
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
            Assert(!await coordinator.InstallAsync(path), "concurrent installation was accepted");
            pending.SetResult(true);
            Assert(await first && string.Join(",", calls) == "save,start,close", "installation ran more than once");
        }
    }
}
