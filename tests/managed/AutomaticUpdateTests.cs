using System;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class AutomaticUpdateTests
    {
        internal static void Run(string directory)
        {
            TestStartupAndSchedule(directory);
            TestPeriodicPolling(directory);
            TestGameEnd(directory);
            TestConcurrentTriggers(directory);
            TestDisposal(directory);
            TestAvailableNotice(directory);
            TestNoticeDeduplication(directory);
            TestNoticeCheckOrigins(directory);
        }

        private static LauncherUpdateInfo NewRelease() => new LauncherUpdateInfo
        {
            CurrentVersion = "v1.0.0", TargetVersion = "v1.1.0", IsNewVersion = true
        };

        private static MainWindowViewModel CreateModel(string directory, FakeLauncherBridge bridge,
            Func<Task<LauncherUpdateInfo>> checkUpdate, FakeDesktopInteraction interaction = null) =>
            new MainWindowViewModel(bridge, new SettingsService(directory),
                new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "玩家" }, null,
                path => Task.FromResult(ValidDirectory(path)), interaction ?? new FakeDesktopInteraction(),
                checkUpdate: checkUpdate);

        private static void TestStartupAndSchedule(string directory)
        {
            var calls = 0;
            var interaction = new FakeDesktopInteraction();
            using (var model = CreateModel(Path.Combine(directory, "update-schedule"), new FakeLauncherBridge(),
                () => { calls++; return Task.FromResult(NewRelease()); }, interaction))
            {
                RunTask(model.ValidationTask);
                var startedAt = DateTime.UtcNow;
                model.StartUpdateChecks(startedAt);
                Assert(calls == 1 && model.About.HasNewUpdate && model.About.LastCheckText != "尚未检查",
                    "startup did not execute the shared update check");
                model.StartUpdateChecks(startedAt.AddMinutes(1));
                Assert(calls == 1, "starting the update scheduler twice repeated the startup check");
                model.CheckScheduledUpdate(startedAt.AddMinutes(30).AddTicks(-1));
                Assert(calls == 1, "periodic update check ran before 30 minutes");
                model.CheckScheduledUpdate(startedAt.AddMinutes(30));
                model.CheckScheduledUpdate(startedAt.AddMinutes(30));
                Assert(calls == 2, "periodic update check did not run exactly once at 30 minutes");
                RunTask(model.About.CheckUpdateCommand.ExecuteAsync(null));
                model.CheckScheduledUpdate(startedAt.AddMinutes(60).AddTicks(-1));
                Assert(calls == 3, "manual update check changed the periodic schedule");
                model.CheckScheduledUpdate(startedAt.AddMinutes(60));
                Assert(calls == 4, "periodic update checks did not continue every 30 minutes");
                Assert(model.IsHomeSelected && interaction.Text == null && interaction.ConfirmCount == 0,
                    "automatic update check navigated or opened a dialog");
            }
        }

        private static void TestPeriodicPolling(string directory)
        {
            var calls = 0;
            var periodicCheck = new TaskCompletionSource<bool>();
            using (var model = CreateModel(Path.Combine(directory, "update-polling"), new FakeLauncherBridge(), () =>
            {
                if (++calls == 2) periodicCheck.SetResult(true);
                return Task.FromResult(NewRelease());
            }))
            {
                model.StartUpdateChecks(DateTime.UtcNow.AddMinutes(-30));
                RunTask(periodicCheck.Task);
                Assert(calls == 2, "the launcher poller did not trigger the due update check");
            }
        }

        private static void TestGameEnd(string directory)
        {
            var calls = 0;
            var bridge = new FakeLauncherBridge();
            using (var model = CreateModel(Path.Combine(directory, "update-game-end"), bridge,
                () => { calls++; return Task.FromResult(NewRelease()); }))
            {
                RunTask(model.ValidationTask);
                model.StartUpdateChecks(DateTime.UtcNow);
                bridge.Snapshot = new SessionSnapshot { State = SessionState.StartingRuntime };
                model.Refresh();
                bridge.Snapshot = new SessionSnapshot { State = SessionState.Failed, CleanupComplete = true };
                model.Refresh();
                Assert(calls == 1, "a game that never ran triggered an end-of-game update check");

                foreach (var endState in new[] { SessionState.Idle, SessionState.Failed })
                {
                    var before = calls;
                    bridge.Snapshot = new SessionSnapshot { State = SessionState.Running };
                    model.Refresh();
                    bridge.Snapshot = new SessionSnapshot { State = SessionState.StoppingRuntime };
                    model.Refresh();
                    bridge.Snapshot = new SessionSnapshot { State = SessionState.FailedCleaning };
                    model.Refresh();
                    Assert(calls == before, "update check ran before game cleanup finished");
                    bridge.Snapshot = new SessionSnapshot { State = endState, CleanupComplete = true };
                    model.Refresh();
                    model.Refresh();
                    Assert(calls == before + 1, "game end did not trigger exactly one update check");
                }

                var beforeStop = calls;
                RunTask(model.StartCommand.ExecuteAsync(null));
                Assert(calls == beforeStop, "starting a game triggered an update check");
                RunTask(model.StopCommand.ExecuteAsync(null));
                Assert(calls == beforeStop + 1, "manually ending the game did not check for updates");
            }
        }

        private static void TestConcurrentTriggers(string directory)
        {
            var calls = 0;
            var pending = new TaskCompletionSource<LauncherUpdateInfo>();
            var bridge = new FakeLauncherBridge();
            using (var model = CreateModel(Path.Combine(directory, "update-concurrency"), bridge,
                () => { calls++; return pending.Task; }))
            {
                var startedAt = DateTime.UtcNow;
                model.StartUpdateChecks(startedAt);
                var startupCheck = model.About.CheckUpdateCommand.ExecutionTask;
                model.CheckScheduledUpdate(startedAt.AddMinutes(30));
                EndGame(model, bridge);
                RunTask(model.About.CheckUpdateCommand.ExecuteAsync(null));
                Assert(calls == 1 && model.About.IsCheckingUpdate && !model.About.CheckUpdateCommand.CanExecute(null),
                    "manual, periodic or game-end check overlapped the startup check");
                pending.SetResult(NewRelease());
                RunTask(startupCheck);
                model.CheckScheduledUpdate(startedAt.AddMinutes(30).AddSeconds(1));
                Assert(calls == 1 && model.About.HasNewUpdate, "busy triggers queued a redundant update check");

                pending = new TaskCompletionSource<LauncherUpdateInfo>();
                var manualCheck = model.About.CheckUpdateCommand.ExecuteAsync(null);
                model.CheckScheduledUpdate(startedAt.AddMinutes(60));
                EndGame(model, bridge);
                Assert(calls == 2, "automatic update check overlapped a manual check");
                pending.SetException(new IOException("update failure fixture"));
                RunTask(manualCheck);
                Assert(model.About.UpdateFailed && !model.About.IsCheckingUpdate && model.About.CheckUpdateCommand.CanExecute(null),
                    "failed update check did not release the shared concurrency guard");

                pending = new TaskCompletionSource<LauncherUpdateInfo>();
                pending.SetResult(NewRelease());
                model.CheckScheduledUpdate(startedAt.AddMinutes(90));
                Assert(calls == 3 && !model.About.UpdateFailed && model.About.HasNewUpdate,
                    "automatic update check did not recover after a previous failure");
            }
        }

        private static void EndGame(MainWindowViewModel model, FakeLauncherBridge bridge)
        {
            bridge.Snapshot = new SessionSnapshot { State = SessionState.Running };
            model.Refresh();
            bridge.Snapshot = new SessionSnapshot { State = SessionState.Idle, CleanupComplete = true };
            model.Refresh();
        }

        private static void TestAvailableNotice(string directory)
        {
            var interaction = new FakeDesktopInteraction();
            using (var model = CreateModel(Path.Combine(directory, "update-notice"), new FakeLauncherBridge(),
                () => Task.FromResult(NewRelease()), interaction))
            {
                model.StartUpdateChecks(DateTime.UtcNow);
                Assert(model.Notices.Visible.Count == 1, "automatic discovery did not publish an update notice");
                var notice = model.Notices.Visible.Single();
                Assert(notice.Message == "发现启动器新版本 v1.1.0" && notice.Severity == NoticeSeverity.Info
                    && notice.ActionText == "查看" && notice.Remaining == TimeSpan.FromSeconds(6),
                    "update notice lost its version, action or six-second duration");
                Assert(model.About.UpdateDownload.Info.Installer == null && model.IsHomeSelected,
                    "release without an installer did not preserve the existing update decision");
                notice.ActionCommand.Execute(null);
                Assert(model.IsUpdateSelected && interaction.Text == null && interaction.ConfirmCount == 0
                    && !model.About.IsUpdating, "update notice started an operation instead of navigating");
                model.Notices.Tick(notice.CreatedAt.AddSeconds(6).AddMilliseconds(-1));
                Assert(model.Notices.Visible.Contains(notice), "update notice expired before six seconds");
                model.Notices.Tick(notice.CreatedAt.AddSeconds(6));
                Assert(!model.Notices.Visible.Contains(notice), "update notice did not expire after six seconds");
            }
        }

        private static void TestNoticeDeduplication(string directory)
        {
            var update = NewRelease();
            using (var model = CreateModel(Path.Combine(directory, "update-notice-dedup"), new FakeLauncherBridge(),
                () => Task.FromResult(update)))
            {
                var startedAt = DateTime.UtcNow;
                model.StartUpdateChecks(startedAt);
                foreach (var notice in model.Notices.Visible.ToArray()) notice.CloseCommand.Execute(null);
                for (var index = 0; index < 25; index++)
                    model.Notices.Publish("fixture-" + index, "fixture", NoticeSeverity.Success);
                Assert(!model.Notices.History.Any(item => item.Message.StartsWith("发现启动器新版本")),
                    "notice history was not evicted for the session deduplication test");
                var history = model.Notices.History.ToArray();
                model.CheckScheduledUpdate(startedAt.AddMinutes(30));
                Assert(model.Notices.History.SequenceEqual(history), "same update was repeated after history eviction");
                update = NewRelease();
                update.TargetVersion = "v1.2.0";
                model.CheckScheduledUpdate(startedAt.AddMinutes(60));
                Assert(model.Notices.History.Count(item => item.Message == "发现启动器新版本 v1.2.0") == 1,
                    "a different target did not receive its own notice");
                update = new LauncherUpdateInfo { TargetVersion = "v1.2.0" };
                model.CheckScheduledUpdate(startedAt.AddMinutes(90));
                update = NewRelease();
                model.CheckScheduledUpdate(startedAt.AddMinutes(120));
                Assert(!model.Notices.History.Any(item => item.Message == "发现启动器新版本 v1.1.0"),
                    "clearing and rediscovering an update reset session deduplication");
            }
            using (var model = CreateModel(Path.Combine(directory, "update-notice-new-session"), new FakeLauncherBridge(),
                () => Task.FromResult(NewRelease())))
            {
                model.StartUpdateChecks(DateTime.UtcNow);
                Assert(model.Notices.Visible.Any(item => item.Message == "发现启动器新版本 v1.1.0"),
                    "update notice deduplication leaked into a new launcher session");
            }
        }

        private static void TestNoticeCheckOrigins(string directory)
        {
            var pending = new TaskCompletionSource<LauncherUpdateInfo>();
            using (var model = CreateModel(Path.Combine(directory, "update-notice-origin"), new FakeLauncherBridge(),
                () => pending.Task))
            {
                var startedAt = DateTime.UtcNow;
                var manual = model.About.CheckUpdateCommand.ExecuteAsync(null);
                model.StartUpdateChecks(startedAt);
                pending.SetResult(NewRelease());
                RunTask(manual);
                Assert(model.Notices.History.Count == 0, "skipped automatic check turned a manual result into a toast");
                pending = new TaskCompletionSource<LauncherUpdateInfo>();
                model.CheckScheduledUpdate(startedAt.AddMinutes(30));
                var automatic = model.About.CheckUpdateCommand.ExecutionTask;
                pending.SetException(new IOException("update fixture"));
                RunTask(automatic);
                Assert(!model.Notices.History.Any(item => item.Message.StartsWith("发现启动器新版本")),
                    "a failed check announced its stale update result");
                pending = new TaskCompletionSource<LauncherUpdateInfo>();
                pending.SetResult(NewRelease());
                model.CheckScheduledUpdate(startedAt.AddMinutes(60));
                Assert(model.Notices.History.Count(item => item.Message == "发现启动器新版本 v1.1.0") == 1,
                    "successful automatic retry did not announce the update");
                pending = new TaskCompletionSource<LauncherUpdateInfo>();
                model.CheckScheduledUpdate(startedAt.AddMinutes(90));
                automatic = model.About.CheckUpdateCommand.ExecutionTask;
                model.Dispose();
                var count = model.Notices.History.Count;
                var next = NewRelease();
                next.TargetVersion = "v1.3.0";
                pending.SetResult(next);
                RunTask(automatic);
                Assert(model.Notices.History.Count == count, "disposed launcher announced a completed update check");
            }
        }

        private static void TestDisposal(string directory)
        {
            var calls = 0;
            var bridge = new FakeLauncherBridge();
            var startedAt = DateTime.UtcNow;
            using (var model = CreateModel(Path.Combine(directory, "update-dispose"), bridge,
                () => { calls++; return Task.FromResult(NewRelease()); }))
            {
                model.StartUpdateChecks(startedAt);
                model.Dispose();
                model.StartUpdateChecks(startedAt.AddMinutes(1));
                model.CheckScheduledUpdate(startedAt.AddMinutes(30));
                EndGame(model, bridge);
                Assert(calls == 1, "disposed launcher continued triggering automatic update checks");
            }
            using (var model = CreateModel(Path.Combine(directory, "update-dispose-before-start"), bridge,
                () => { calls++; return Task.FromResult(NewRelease()); }))
            {
                model.Dispose();
                model.StartUpdateChecks(startedAt);
                model.CheckScheduledUpdate(startedAt.AddMinutes(30));
                Assert(calls == 1, "disposed launcher started automatic update checks");
            }
        }
    }
}
