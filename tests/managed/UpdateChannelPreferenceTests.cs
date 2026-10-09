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
            TestNavigationConfirmation(Path.Combine(directory, "channel-navigation"));
            TestPreviewConfirmation(Path.Combine(directory, "channel-confirmation"));
            TestCheckDuringConfirmation(Path.Combine(directory, "channel-confirmation-check"));
            TestSwitch(Path.Combine(directory, "channel-switch"));
            TestSaveFailure(Path.Combine(directory, "channel-blocked"));
            TestBusyDownload(Path.Combine(directory, "channel-download"));
            TestReminderIdentity(Path.Combine(directory, "channel-reminder"));
        }

        private static MainWindowViewModel Model(SettingsService settings, Func<UpdateChannel, Task<LauncherUpdateInfo>> check,
            Func<LauncherUpdateInfo, UpdateDownloadViewModel> download = null, FakeDesktopInteraction interaction = null) =>
            new MainWindowViewModel(new FakeLauncherBridge(), settings,
                new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "玩家" }, null,
                path => Task.FromResult(ValidDirectory(path)), interaction ?? new FakeDesktopInteraction(),
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
            var interaction = new FakeDesktopInteraction { ConfirmResult = false };
            using (var model = Model(settings, _ => Task.FromResult(PreviewInfo()), interaction: interaction))
            {
                Assert(model.SelectedUpdateChannel == UpdateChannel.Preview && interaction.ConfirmCount == 0 && model.IsHomeSelected,
                    "reopening lost the saved channel, prompted again or changed the initial page");
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

        private static void TestNavigationConfirmation(string directory)
        {
            foreach (var channel in new[] { UpdateChannel.Stable, UpdateChannel.Preview })
            foreach (var navigate in new[] { true, false })
            {
                var settings = new SettingsService(Path.Combine(directory, channel + "-" + navigate));
                settings.SaveUpdateChannel(channel == UpdateChannel.Stable ? UpdateChannel.Preview : UpdateChannel.Stable);
                var interaction = new FakeDesktopInteraction();
                var pending = new TaskCompletionSource<LauncherUpdateInfo>();
                var calls = new List<UpdateChannel>();
                var riskConfirmations = 0;
                var navigationConfirmations = 0;
                using (var model = Model(settings, value => { calls.Add(value); return pending.Task; }, interaction: interaction))
                {
                    model.IsSettingsSelected = true;
                    interaction.Confirming = () =>
                    {
                        if (interaction.Title == "切换到预览版？") { riskConfirmations++; return; }
                        navigationConfirmations++;
                        Assert(interaction.Title == "前往更新页？" && interaction.ConfirmationAction == "前往更新页"
                            && interaction.Text.Contains(channel == UpdateChannel.Preview ? "预览版" : "正式版")
                            && interaction.Text.Contains("不影响"), "navigation confirmation did not explain the saved channel");
                        Assert(model.IsSettingsSelected && model.SelectedUpdateChannel == channel
                            && settings.LoadUpdateChannel(out _) == channel && model.About.IsCheckingUpdate
                            && calls.SequenceEqual(new[] { channel }),
                            "navigation confirmation was shown before saving/checking or after navigating");
                        interaction.ConfirmResult = navigate;
                    };
                    model.SelectedUpdateChannel = channel;
                    Assert(navigationConfirmations == 1 && riskConfirmations == (channel == UpdateChannel.Preview ? 1 : 0),
                        "channel switch skipped navigation confirmation or repeated the risk prompt");
                    Assert(model.IsUpdateSelected == navigate && model.IsSettingsSelected == !navigate
                        && model.SelectedUpdateChannel == channel && settings.LoadUpdateChannel(out _) == channel,
                        "navigation choice was ignored or reverted the saved channel");
                    pending.SetResult(new LauncherUpdateInfo { Channel = channel });
                    RunTask(model.About.CheckUpdateCommand.ExecutionTask);
                    Assert(model.IsUpdateSelected == navigate && calls.SequenceEqual(new[] { channel }),
                        "update completion changed the navigation choice or repeated the check");
                    var confirmCount = interaction.ConfirmCount;
                    model.SelectedUpdateChannel = channel;
                    Assert(interaction.ConfirmCount == confirmCount,
                        "unchanged channel prompted for navigation again");
                }
            }
        }

        private static void TestPreviewConfirmation(string directory)
        {
            var settings = new SettingsService(directory);
            settings.SaveUpdateChannel(UpdateChannel.Stable);
            var saved = File.ReadAllText(Path.Combine(directory, "settings.json"), Encoding.UTF8);
            var interaction = new FakeDesktopInteraction { ConfirmResult = false };
            var calls = new List<UpdateChannel>();
            using (var model = Model(settings, channel =>
            {
                calls.Add(channel);
                return Task.FromResult(channel == UpdateChannel.Preview ? PreviewInfo() : UpdateDownloadViewModelTests.Info());
            }, interaction: interaction))
            {
                model.IsSettingsSelected = true;
                RunTask(model.About.CheckUpdateCommand.ExecuteAsync(null));
                var previous = model.About.UpdateDownload;
                var reminder = model.About.UpdateReminderVersion;
                var noticeCount = model.Notices.History.Count;
                interaction.Confirming = () =>
                {
                    if (interaction.Title != "切换到预览版？") return;
                    Assert(model.SelectedUpdateChannel == UpdateChannel.Stable
                        && settings.LoadUpdateChannel(out _) == UpdateChannel.Stable && !model.About.IsCheckingUpdate,
                        "preview channel was applied before confirmation completed");
                };
                var selectionNotified = false;
                model.PropertyChanged += (_, args) =>
                {
                    if (args.PropertyName == nameof(model.SelectedUpdateChannel)) selectionNotified = true;
                };
                model.SelectedUpdateChannel = UpdateChannel.Preview;
                Pump();
                Assert(interaction.ConfirmCount == 1 && interaction.Title == "切换到预览版？"
                    && interaction.ConfirmationAction == "切换到预览版" && interaction.Text.Contains("预览版")
                    && interaction.Text.Contains("崩溃") && interaction.Text.Contains("兼容性") && interaction.Text.Contains("备份"),
                    "preview selection did not ask for confirmation with its risks");
                Assert(selectionNotified && model.SelectedUpdateChannel == UpdateChannel.Stable && model.IsSettingsSelected
                    && calls.SequenceEqual(new[] { UpdateChannel.Stable })
                    && File.ReadAllText(Path.Combine(directory, "settings.json"), Encoding.UTF8) == saved,
                    "cancelled preview selection changed settings, navigation or update checks");
                Assert(ReferenceEquals(previous, model.About.UpdateDownload) && previous.PrimaryCommand.CanExecute(null)
                    && model.About.UpdateReminderVersion == reminder && model.Notices.History.Count == noticeCount,
                    "cancelled preview selection cleared the current update or notices");

                interaction.ConfirmResult = true;
                model.SelectedUpdateChannel = UpdateChannel.Preview;
                RunTask(model.About.CheckUpdateCommand.ExecutionTask);
                Assert(interaction.ConfirmCount == 3 && model.SelectedUpdateChannel == UpdateChannel.Preview && model.IsUpdateSelected
                    && settings.LoadUpdateChannel(out _) == UpdateChannel.Preview
                    && calls.SequenceEqual(new[] { UpdateChannel.Stable, UpdateChannel.Preview }),
                    "confirmed preview selection did not save, navigate and check the new channel exactly once");
                model.IsSettingsSelected = true;
                model.SelectedUpdateChannel = UpdateChannel.Preview;
                Assert(interaction.ConfirmCount == 3 && calls.Count == 2 && model.IsSettingsSelected,
                    "unchanged channel prompted, checked again or navigated away");

                model.SelectedUpdateChannel = UpdateChannel.Stable;
                RunTask(model.About.CheckUpdateCommand.ExecutionTask);
                Assert(interaction.ConfirmCount == 4 && settings.LoadUpdateChannel(out _) == UpdateChannel.Stable && model.IsUpdateSelected,
                    "returning to stable did not confirm navigation, persist or show the update page");
                model.SelectedUpdateChannel = UpdateChannel.Preview;
                RunTask(model.About.CheckUpdateCommand.ExecutionTask);
                Assert(interaction.ConfirmCount == 6 && calls.Count == 4, "re-entering preview skipped confirmation");
            }
        }

        private static void TestCheckDuringConfirmation(string directory)
        {
            var settings = new SettingsService(directory);
            var interaction = new FakeDesktopInteraction();
            var pending = new TaskCompletionSource<LauncherUpdateInfo>();
            var calls = new List<UpdateChannel>();
            using (var model = Model(settings, channel => { calls.Add(channel); return pending.Task; }, interaction: interaction))
            {
                model.IsSettingsSelected = true;
                interaction.Confirming = () => model.StartUpdateChecks(DateTime.UtcNow);
                model.SelectedUpdateChannel = UpdateChannel.Preview;
                Assert(model.SelectedUpdateChannel == UpdateChannel.Stable && settings.LoadUpdateChannel(out _) == UpdateChannel.Stable
                    && calls.SequenceEqual(new[] { UpdateChannel.Stable }) && model.About.IsCheckingUpdate && model.IsSettingsSelected,
                    "preview confirmation changed channel or page during a background update check");
                pending.SetResult(UpdateDownloadViewModelTests.Info());
                RunTask(model.About.CheckUpdateCommand.ExecutionTask);
            }
        }

        private static void TestSwitch(string directory)
        {
            var calls = new List<UpdateChannel>();
            var pending = new TaskCompletionSource<LauncherUpdateInfo>();
            var settings = new SettingsService(directory);
            var interaction = new FakeDesktopInteraction();
            using (var model = Model(settings, channel => { calls.Add(channel); return pending.Task; }, interaction: interaction))
            {
                model.IsSettingsSelected = true;
                var checking = model.About.CheckUpdateCommand.ExecuteAsync(null);
                Assert(!model.CanChangeUpdateChannel, "channel editable during check");
                model.SelectedUpdateChannel = UpdateChannel.Preview;
                Assert(model.SelectedUpdateChannel == UpdateChannel.Stable && calls.Count == 1 && interaction.ConfirmCount == 0
                    && model.IsSettingsSelected,
                    "busy check changed channel, prompted for confirmation or navigated away");
                pending.SetResult(UpdateDownloadViewModelTests.Info());
                RunTask(checking);
                var previous = model.About.UpdateDownload;
                pending = new TaskCompletionSource<LauncherUpdateInfo>();
                model.SelectedUpdateChannel = UpdateChannel.Preview;
                Assert(model.IsUpdateSelected, "preview switch did not navigate before the update check completed");
                Assert(calls.SequenceEqual(new[] { UpdateChannel.Stable, UpdateChannel.Preview })
                    && settings.LoadUpdateChannel(out _) == UpdateChannel.Preview && model.About.IsCheckingUpdate
                    && !model.About.HasUpdateReminder && model.About.UpdateDownload == null
                    && !previous.PrimaryCommand.CanExecute(null), "channel switch reused old result or installer");
                pending.SetResult(PreviewInfo());
                RunTask(model.About.CheckUpdateCommand.ExecutionTask);
                Assert(model.About.UpdateChannelText == "预览渠道" && model.About.UpdateReminderVersion == "v0.1.0p12.1",
                    "preview UI identity was lost");
                pending = new TaskCompletionSource<LauncherUpdateInfo>();
                model.IsSettingsSelected = true;
                model.SelectedUpdateChannel = UpdateChannel.Stable;
                Assert(model.IsUpdateSelected, "stable switch did not navigate before the update check completed");
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
            var interaction = new FakeDesktopInteraction();
            using (var model = Model(new SettingsService(path), _ => { calls++; return Task.FromResult(PreviewInfo()); }, interaction: interaction))
            {
                model.IsSettingsSelected = true;
                model.SelectedUpdateChannel = UpdateChannel.Preview;
                Assert(model.SelectedUpdateChannel == UpdateChannel.Stable && calls == 0 && model.PreferenceError.Length != 0
                    && model.IsSettingsSelected && interaction.ConfirmCount == 1 && interaction.Title == "切换到预览版？",
                    "failed preference save changed channel/page, checked updates or prompted for navigation");
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
