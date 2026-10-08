using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class UpdateChannelPreferenceTests
    {
        internal static void Run(string directory)
        {
            TestPersistence(Path.Combine(directory, "channel-persistence"));
            TestSwitch(Path.Combine(directory, "channel-switch"));
            TestSaveFailure(Path.Combine(directory, "channel-blocked"));
            TestBusyDownload(Path.Combine(directory, "channel-download"));
            TestReminderIdentity(Path.Combine(directory, "channel-reminder"));
        }

        private static MainWindowViewModel Model(SettingsService settings, Func<UpdateChannel, Task<LauncherUpdateInfo>> check,
            Func<LauncherUpdateInfo, UpdateDownloadViewModel> download = null) =>
            new MainWindowViewModel(new FakeLauncherBridge(), settings,
                new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "玩家" }, null,
                path => Task.FromResult(ValidDirectory(path)), new FakeDesktopInteraction(),
                createUpdateDownload: download, checkChannelUpdate: check);

        private static LauncherUpdateInfo PreviewInfo(long number = 12) => new LauncherUpdateInfo
        {
            TargetVersion = "v0.1.0", Channel = UpdateChannel.Preview,
            TargetBuild = UpdateChannelTests.Preview(number), IsNewVersion = true
        };

        private static void TestPersistence(string directory)
        {
            var settings = new SettingsService(directory);
            Assert(settings.LoadUpdateChannel(out var warning) == UpdateChannel.Stable && warning.Length == 0,
                "missing channel did not default to stable");
            settings.Save(new UserSettings { PlayerName = "保留玩家", ClientDirectory = @"C:\Games\T7" });
            settings.SaveUpdateChannel(UpdateChannel.Stable);
            settings.SaveUpdateChannel(UpdateChannel.Preview);
            Assert((string)JObject.Parse(File.ReadAllText(Path.Combine(directory, "settings.json"), Encoding.UTF8))["updateChannel"] == "preview"
                && settings.Load().PlayerName == "保留玩家" && settings.LastWarning.Length == 0,
                "channel was not saved in the unified settings file");
            Assert(!File.Exists(Path.Combine(directory, "update-settings.json"))
                && !File.Exists(Path.Combine(directory, "update-settings.json.bak")), "saving channel still created separate settings files");
            using (var model = Model(settings, _ => Task.FromResult(PreviewInfo())))
            {
                Assert(model.SelectedUpdateChannel == UpdateChannel.Preview, "channel did not survive reopening");
                model.SaveSettings();
                Assert(settings.LoadUpdateChannel(out warning) == UpdateChannel.Preview, "saving window settings lost the channel");
            }
            File.WriteAllText(Path.Combine(directory, "settings.json"), "invalid");
            Assert(settings.LoadUpdateChannel(out warning) == UpdateChannel.Preview && warning.Contains("备份"),
                "invalid unified settings did not restore backup");
            File.WriteAllText(Path.Combine(directory, "settings.json.bak"), "{\"schemaVersion\":1,\"updateChannel\":\"unknown\"}");
            Assert(settings.LoadUpdateChannel(out warning) == UpdateChannel.Stable && warning.Length != 0,
                "invalid channel and backup did not fall back visibly");
        }

        private static void TestSwitch(string directory)
        {
            var calls = new List<UpdateChannel>();
            var pending = new TaskCompletionSource<LauncherUpdateInfo>();
            var settings = new SettingsService(directory);
            using (var model = Model(settings, channel => { calls.Add(channel); return pending.Task; }))
            {
                var checking = model.About.CheckUpdateCommand.ExecuteAsync(null);
                Assert(!model.CanChangeUpdateChannel, "channel editable during check");
                model.SelectedUpdateChannel = UpdateChannel.Preview;
                Assert(model.SelectedUpdateChannel == UpdateChannel.Stable && calls.Count == 1, "busy check changed channel");
                pending.SetResult(UpdateDownloadViewModelTests.Info());
                RunTask(checking);
                var previous = model.About.UpdateDownload;
                pending = new TaskCompletionSource<LauncherUpdateInfo>();
                model.SelectedUpdateChannel = UpdateChannel.Preview;
                Assert(calls.SequenceEqual(new[] { UpdateChannel.Stable, UpdateChannel.Preview })
                    && settings.LoadUpdateChannel(out _) == UpdateChannel.Preview && model.About.IsCheckingUpdate
                    && !model.About.HasUpdateReminder && model.About.UpdateDownload == null
                    && !previous.PrimaryCommand.CanExecute(null), "channel switch reused old result or installer");
                pending.SetResult(PreviewInfo());
                RunTask(model.About.CheckUpdateCommand.ExecutionTask);
                Assert(model.About.UpdateChannelText == "预览渠道" && model.About.UpdateReminderVersion.Contains("12.1"),
                    "preview UI identity was lost");
                pending = new TaskCompletionSource<LauncherUpdateInfo>();
                model.SelectedUpdateChannel = UpdateChannel.Stable;
                pending.SetResult(new LauncherUpdateInfo { HasPublishedRelease = false });
                RunTask(model.About.CheckUpdateCommand.ExecutionTask);
                Assert(!model.About.HasUpdateReminder && model.About.UpdateChannelText == "正式渠道",
                    "returning to stable retained a preview reminder");
            }
        }

        private static void TestSaveFailure(string path)
        {
            File.WriteAllText(path, "blocked settings directory");
            var calls = 0;
            using (var model = Model(new SettingsService(path), _ => { calls++; return Task.FromResult(PreviewInfo()); }))
            {
                model.SelectedUpdateChannel = UpdateChannel.Preview;
                Assert(model.SelectedUpdateChannel == UpdateChannel.Stable && calls == 0 && model.PreferenceError.Length != 0,
                    "failed preference save changed runtime channel or checked updates");
            }
        }

        private static void TestBusyDownload(string directory)
        {
            var pendingDownload = new TaskCompletionSource<string>();
            var pendingInstall = new TaskCompletionSource<bool>();
            using (var model = Model(new SettingsService(directory), _ => Task.FromResult(UpdateDownloadViewModelTests.Info()),
                info => new UpdateDownloadViewModel(info, (asset, progress, token, control) => pendingDownload.Task,
                    _ => pendingInstall.Task, _ => { })))
            {
                RunTask(model.About.CheckUpdateCommand.ExecuteAsync(null));
                var download = model.About.UpdateDownload;
                var downloading = download.PrimaryCommand.ExecuteAsync(null);
                Assert(!model.CanChangeUpdateChannel, "channel editable during download");
                download.PauseDownloadCommand.Execute(null);
                model.SelectedUpdateChannel = UpdateChannel.Preview;
                Assert(download.IsPaused && !model.CanChangeUpdateChannel && model.SelectedUpdateChannel == UpdateChannel.Stable,
                    "paused download allowed a channel change");
                download.PauseDownloadCommand.Execute(null);
                pendingDownload.SetResult("installer.fixture");
                RunTask(downloading);
                Assert(model.CanChangeUpdateChannel, "completed download kept channel locked");
                var installing = download.PrimaryCommand.ExecuteAsync(null);
                model.SelectedUpdateChannel = UpdateChannel.Preview;
                Assert(!model.CanChangeUpdateChannel && model.SelectedUpdateChannel == UpdateChannel.Stable,
                    "installing allowed a channel change");
                pendingInstall.SetResult(false);
                RunTask(installing);
                model.SelectedUpdateChannel = UpdateChannel.Preview;
                RunTask(model.About.CheckUpdateCommand.ExecutionTask);
                Assert(!download.PrimaryCommand.CanExecute(null), "old downloaded installer remained actionable");
            }
        }

        private static void TestReminderIdentity(string directory)
        {
            var settings = new SettingsService(directory);
            settings.SaveUpdateChannel(UpdateChannel.Preview);
            var info = PreviewInfo();
            using (var model = Model(settings, _ => Task.FromResult(info)))
            {
                var now = DateTime.UtcNow;
                model.StartUpdateChecks(now);
                var first = model.About.UpdateDownload;
                model.CheckScheduledUpdate(now.AddMinutes(30));
                Assert(ReferenceEquals(first, model.About.UpdateDownload), "same build did not retain download state");
                info = PreviewInfo(13);
                model.CheckScheduledUpdate(now.AddMinutes(60));
                Assert(!ReferenceEquals(first, model.About.UpdateDownload)
                    && model.Notices.History.Count(notice => notice.Message.StartsWith("发现启动器新版本")) == 2,
                    "distinct CI builds with equal numeric versions shared a reminder or installer");
            }
        }
    }
}
