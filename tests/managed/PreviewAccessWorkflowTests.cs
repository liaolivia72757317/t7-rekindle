using System;
using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Threading;
using T7R.Core;
using T7R.Desktop.Services;
using T7R.Desktop.ViewModels;
using static T7R.ManagedHarness.LauncherTests;

namespace T7R.ManagedHarness
{
    internal static class PreviewAccessWorkflowTests
    {
        internal static void Run(string directory)
        {
            var previous = SynchronizationContext.Current;
            SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
            try
            {
                TestEntry(Path.Combine(directory, "entry"));
                TestPromptRace(Path.Combine(directory, "race"));
                foreach (var operation in new[] { "idle", "check", "download", "paused", "ready", "install", "history" })
                    TestExpiry(Path.Combine(directory, operation), operation);
                TestForcedSaveFailure(Path.Combine(directory, "save-failure"));
                TestAuthorizedSaveFailure(Path.Combine(directory, "authorized-save-failure"));
            }
            finally { SynchronizationContext.SetSynchronizationContext(previous); }
        }

        private static MainWindowViewModel Model(SettingsService settings, PreviewAccessService access,
            Func<UpdateChannel, Task<LauncherUpdateInfo>> check, Func<PreviewAccessViewModel, bool> prompt = null,
            Func<LauncherUpdateInfo, UpdateDownloadViewModel> download = null, FakeLauncherBridge bridge = null,
            Func<UpdateChannel, CancellationToken, Task<IReadOnlyList<LauncherHistoryEntry>>> history = null,
            FakeDesktopInteraction interaction = null) => new MainWindowViewModel(bridge ?? new FakeLauncherBridge(),
                settings,
                new UserSettings { PlayerName = "玩家", ClientDirectory = @"C:\Games\T7" },
                null,
                new FakeClientEnvironment(path => Task.FromResult(ValidDirectory(path))),
                interaction ?? new FakeDesktopInteraction { ConfirmResult = true },
                updates: new LauncherUpdateFactory(check: check, download: download, history: history),
                previewAccess: access,
                requestPreviewAccess: prompt ?? (_ => false));

        private static LauncherUpdateInfo PreviewInfo() => new LauncherUpdateInfo
        {
            TargetVersion = "v0.1.0", Channel = UpdateChannel.Preview, TargetBuild = UpdateChannelTests.Preview(),
            IsNewVersion = true, Installer = UpdateFixtures.Asset()
        };

        private static void TestEntry(string directory)
        {
            using (var fixture = new PreviewAccessFixture())
            {
                var access = fixture.Create(directory);
                var settings = new SettingsService(directory);
                var checks = new List<UpdateChannel>();
                var code = "invalid";
                var prompts = 0;
                var interaction = new FakeDesktopInteraction { ConfirmResult = false };
                using (var model = Model(settings, access, channel => { checks.Add(channel); return Task.FromResult(new LauncherUpdateInfo()); },
                    dialog => { prompts++; dialog.GrantCode = code; dialog.AuthorizeCommand.Execute(null); return dialog.IsAccepted; }, interaction: interaction))
                {
                    model.IsSettingsSelected = true;
                    model.SelectedUpdateChannel = UpdateChannel.Preview;
                    Assert(model.SelectedUpdateChannel == UpdateChannel.Stable && checks.Count == 0 && model.IsSettingsSelected,
                        "invalid code changed channel, page or started an update");
                    code = fixture.Issue();
                    model.SelectedUpdateChannel = UpdateChannel.Preview;
                    Assert(model.SelectedUpdateChannel == UpdateChannel.Preview && access.IsAuthorized && checks.Count == 1
                        && model.IsSettingsSelected && settings.LoadUpdateChannel(out _) == UpdateChannel.Preview, "verified entry did not persist before checking");
                    model.SelectedUpdateChannel = UpdateChannel.Stable;
                    interaction.ConfirmResult = true;
                    model.SelectedUpdateChannel = UpdateChannel.Preview;
                    Assert(prompts == 2 && checks.Count == 3, "valid grant was not reused on reentry");
                }
            }
        }

        private static void TestPromptRace(string directory)
        {
            foreach (var race in new[] { "busy", "expiry" })
            using (var fixture = new PreviewAccessFixture())
            {
                var path = Path.Combine(directory, race);
                var access = fixture.Create(path);
                var pending = new TaskCompletionSource<LauncherUpdateInfo>();
                MainWindowViewModel active = null;
                using (var model = Model(new SettingsService(path), access, _ => pending.Task, dialog =>
                {
                    dialog.GrantCode = fixture.Issue();
                    dialog.AuthorizeCommand.Execute(null);
                    if (race == "busy") active.StartUpdateChecks(DateTime.UtcNow);
                    else fixture.Now = fixture.Now.AddDays(7);
                    return dialog.IsAccepted;
                }))
                {
                    active = model;
                    model.SelectedUpdateChannel = UpdateChannel.Preview;
                    Assert(model.SelectedUpdateChannel == UpdateChannel.Stable, "dialog race bypassed final access/busy check");
                    pending.SetResult(new LauncherUpdateInfo());
                    if (model.About.CheckUpdateCommand.ExecutionTask != null) RunTask(model.About.CheckUpdateCommand.ExecutionTask);
                }
            }
        }

        private static void TestExpiry(string directory, string operation)
        {
            using (var fixture = new PreviewAccessFixture())
            {
                var access = fixture.Create(directory);
                Assert(access.TryImport(fixture.Issue(), out _), "fixture grant failed");
                var settings = new SettingsService(directory);
                settings.SaveUpdateChannel(UpdateChannel.Preview);
                var pendingCheck = new TaskCompletionSource<LauncherUpdateInfo>();
                var pendingDownload = new TaskCompletionSource<string>();
                var pendingInstall = new TaskCompletionSource<bool>();
                var pendingHistory = new TaskCompletionSource<IReadOnlyList<LauncherHistoryEntry>>();
                var installs = 0;
                var stableChecks = 0;
                var cancelled = false;
                var bridge = new FakeLauncherBridge { Snapshot = new SessionSnapshot { State = SessionState.Running } };
                using (var model = Model(settings, access,
                    channel => channel == UpdateChannel.Stable ? Task.FromResult(StableResult(ref stableChecks))
                        : operation == "check" ? pendingCheck.Task : Task.FromResult(PreviewInfo()),
                    download: info => new UpdateDownloadViewModel(info, (asset, progress, token, control) =>
                    {
                        token.Register(() => cancelled = true);
                        return operation == "download" || operation == "paused" ? pendingDownload.Task : Task.FromResult("fixture.exe");
                    }, _ => { installs++; return pendingInstall.Task; }, _ => { }), bridge: bridge,
                    history: (_, __) => pendingHistory.Task))
                {
                    Task operationTask = null;
                    UpdateDownloadViewModel oldDownload = null;
                    if (operation == "history") operationTask = model.About.History.RefreshCommand.ExecuteAsync(null);
                    else if (operation != "idle")
                    {
                        operationTask = model.About.CheckUpdateCommand.ExecuteAsync(null);
                        if (operation != "check")
                        {
                            RunTask(operationTask);
                            oldDownload = model.About.UpdateDownload;
                            operationTask = oldDownload.PrimaryCommand.ExecuteAsync(null);
                            if (operation == "ready" || operation == "install") RunTask(operationTask);
                            if (operation == "install") operationTask = oldDownload.PrimaryCommand.ExecuteAsync(null);
                            if (operation == "paused") oldDownload.PauseDownloadCommand.Execute(null);
                        }
                    }
                    fixture.Now = fixture.Now.AddDays(7);
                    model.CheckPreviewAccess();
                    var busy = operation != "idle" && operation != "ready";
                    Assert(model.SelectedUpdateChannel == (busy ? UpdateChannel.Preview : UpdateChannel.Stable), "expiry ignored current operation state");
                    if (busy)
                    {
                        Assert(!model.About.CheckUpdateCommand.CanExecute(null) && !model.About.History.RefreshCommand.CanExecute(null)
                            && model.PreviewAccessStatus.Contains("等待"), "expiry allowed new operations while draining");
                        if (operation == "paused")
                        {
                            Assert(oldDownload.PauseDownloadCommand.CanExecute(null), "expiry blocked resuming an existing download");
                            oldDownload.PauseDownloadCommand.Execute(null);
                        }
                        pendingCheck.SetResult(PreviewInfo());
                        pendingDownload.SetResult("fixture.exe");
                        pendingInstall.SetResult(false);
                        pendingHistory.SetResult(Array.Empty<LauncherHistoryEntry>());
                        RunTask(operationTask);
                        Pump();
                        model.CheckPreviewAccess();
                    }
                    Assert(!cancelled && model.SelectedUpdateChannel == UpdateChannel.Stable && stableChecks == 1
                        && settings.LoadUpdateChannel(out _) == UpdateChannel.Stable && !model.About.HasUpdateReminder,
                        "expiry cancelled work, retained preview results, or failed to check stable exactly once");
                    if (oldDownload != null) Assert(!oldDownload.PrimaryCommand.CanExecute(null), "expired installer remained actionable");
                    Assert(installs == (operation == "install" ? 1 : 0) && bridge.Snapshot.State == SessionState.Running,
                        "expiry started an installer or affected the game");
                }
            }
        }

        private static LauncherUpdateInfo StableResult(ref int count) { count++; return new LauncherUpdateInfo(); }

        private static void TestForcedSaveFailure(string directory)
        {
            using (var fixture = new PreviewAccessFixture())
            {
                var access = fixture.Create(directory);
                Assert(access.TryImport(fixture.Issue(), out _), "fixture grant failed");
                var settings = new SettingsService(directory);
                settings.SaveUpdateChannel(UpdateChannel.Preview);
                using (var model = Model(settings, access, _ => Task.FromResult(new LauncherUpdateInfo())))
                using (var blocked = new FileStream(Path.Combine(directory, "settings.json"), FileMode.Open, FileAccess.Read, FileShare.None))
                {
                    fixture.Now = fixture.Now.AddDays(7);
                    model.CheckPreviewAccess();
                    Assert(model.SelectedUpdateChannel == UpdateChannel.Stable && model.PreferenceError.Length != 0,
                        "failed forced save left effective channel on preview");
                }
                Assert(fixture.Create(directory).InitializeChannel(settings, out _) == UpdateChannel.Stable, "failed forced save was not retried on startup");
            }
        }

        private static void TestAuthorizedSaveFailure(string directory)
        {
            using (var fixture = new PreviewAccessFixture())
            {
                var access = fixture.Create(directory);
                var settings = new SettingsService(directory);
                var prompts = 0;
                var checks = 0;
                using (var model = Model(settings, access, _ => { checks++; return Task.FromResult(new LauncherUpdateInfo()); }, dialog =>
                {
                    prompts++;
                    dialog.GrantCode = fixture.Issue();
                    dialog.AuthorizeCommand.Execute(null);
                    return dialog.IsAccepted;
                }))
                {
                    model.IsSettingsSelected = true;
                    using (var blocked = new FileStream(Path.Combine(directory, "settings.json"), FileMode.Open, FileAccess.Read, FileShare.None))
                    {
                        model.SelectedUpdateChannel = UpdateChannel.Preview;
                        Assert(access.IsAuthorized && model.SelectedUpdateChannel == UpdateChannel.Stable && model.IsSettingsSelected
                            && model.PreferenceError.Length != 0 && checks == 0, "channel save failure applied preview or lost the separately saved grant");
                    }
                    model.SelectedUpdateChannel = UpdateChannel.Preview;
                    Assert(prompts == 1 && checks == 1 && model.SelectedUpdateChannel == UpdateChannel.Preview,
                        "channel save retry asked for another grant");
                }
            }
        }
    }
}
