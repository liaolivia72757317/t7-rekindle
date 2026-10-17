using System;
using System.IO;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using T7R.Core;
using T7R.Desktop.Services;
using T7R.Desktop.ViewModels;
using static T7R.ManagedHarness.LauncherTests;

namespace T7R.ManagedHarness
{
    internal static class RemoteAnnouncementTests
    {
        private static readonly LauncherAnnouncement Remote = new LauncherAnnouncement(
            "12345678-1234-1234-1234-123456789abc", "线上标题", "线上摘要", "## 线上正文", DateTimeOffset.UtcNow);

        internal static void Run(string directory)
        {
            AnnouncementApiTests.Run();
            RefreshAndRead(directory);
            FailuresUseBuiltIn(directory);
            CloseIgnoresLateResponse(directory);
        }

        private static void RefreshAndRead(string directory)
        {
            var now = new DateTime(2026, 10, 12, 0, 0, 0, DateTimeKind.Utc);
            var pending = new TaskCompletionSource<LauncherAnnouncement>();
            int calls = 0;
            var interaction = new FakeDesktopInteraction();
            using (var model = Create(directory, interaction, token => { calls++; return pending.Task; }, () => now))
            {
                RunTask(model.ValidationTask);
                AssertBuiltIn(model, interaction);
                Assert(calls == 0, "announcement fetch ran in the constructor");
                model.SelectedPage = 2;
                model.SelectedPage = 0;
                Assert(calls == 0, "announcement checks started before the application requested them");
                model.StartAnnouncementChecks();
                var task = model.AnnouncementTask;
                model.StartAnnouncementChecks();
                Assert(calls == 1 && ReferenceEquals(task, model.RefreshAnnouncementAsync()), "in-flight announcement requests were not merged");
                now += TimeSpan.FromMinutes(2);
                model.SelectedPage = 2;
                model.SelectedPage = 0;
                Assert(calls == 1, "returning home overlapped an in-flight request");
                pending.SetResult(Remote);
                RunTask(task);
                Assert(model.AnnouncementTitle == Remote.Title && model.AnnouncementSummary == Remote.Summary, "remote announcement did not update the card");
                model.ShowAnnouncementCommand.Execute(null);
                Assert(interaction.Title == Remote.Title && interaction.Text == Remote.BodyMarkdown && interaction.IsMarkdown,
                    "remote full text did not come from the same snapshot");
                Assert(calls == 1 && model.Notices.Visible.Count == 0 && model.IsHomeSelected, "reading caused a request, notice, or navigation");
                pending = new TaskCompletionSource<LauncherAnnouncement>();
                model.SelectedPage = 1;
                model.SelectedPage = 0;
                Assert(calls == 2, "returning home after the throttle interval did not refresh");
                pending.SetResult(new LauncherAnnouncement(Remote.AnnouncementId, "新标题", "新摘要", "新正文", Remote.PublishedAt));
                RunTask(model.AnnouncementTask);
                Assert(interaction.Title == Remote.Title && interaction.Text == Remote.BodyMarkdown, "an open reader was changed by a refresh");
                model.SelectedPage = 1;
                now += TimeSpan.FromSeconds(59);
                model.SelectedPage = 0;
                Assert(calls == 2, "rapid home navigation bypassed the throttle");
                now += TimeSpan.FromSeconds(1);
                model.SelectedPage = 1;
                model.SelectedPage = 0;
                RunTask(model.AnnouncementTask);
                Assert(calls == 3, "announcement refresh did not resume at the throttle boundary");
            }
        }

        private static void FailuresUseBuiltIn(string directory)
        {
            foreach (var failure in new Exception[] { null, new HttpRequestException("fixture offline"),
                new InvalidDataException("fixture invalid response"), new TaskCanceledException("fixture timeout") })
            {
                var now = DateTime.UtcNow;
                int calls = 0;
                var interaction = new FakeDesktopInteraction();
                using (var model = Create(directory, interaction, token =>
                {
                    calls++;
                    return calls == 1 || calls == 3 ? Task.FromResult(Remote) : failure == null
                        ? Task.FromResult<LauncherAnnouncement>(null) : Task.FromException<LauncherAnnouncement>(failure);
                }, () => now))
                {
                    RunTask(model.ValidationTask);
                    model.StartAnnouncementChecks();
                    RunTask(model.AnnouncementTask);
                    RunTask(model.StartCommand.ExecuteAsync(null));
                    now += TimeSpan.FromMinutes(1);
                    RunTask(model.RefreshAnnouncementAsync());
                    AssertBuiltIn(model, interaction);
                    Assert(model.IsManagedGameRunning && model.Notices.Visible.Count == 0, "announcement failure disturbed the local game or raised a toast");
                    RunTask(model.RefreshAnnouncementAsync());
                    Assert(calls == 2, "failed requests were not throttled");
                    now += TimeSpan.FromMinutes(1);
                    RunTask(model.RefreshAnnouncementAsync());
                    Assert(model.AnnouncementTitle == Remote.Title, "announcement did not recover after a failed refresh");
                }
            }
        }

        private static void CloseIgnoresLateResponse(string directory)
        {
            var pending = new TaskCompletionSource<LauncherAnnouncement>();
            var cancellation = CancellationToken.None;
            int changes = 0;
            var model = Create(directory, new FakeDesktopInteraction(), token => { cancellation = token; return pending.Task; }, () => DateTime.UtcNow);
            RunTask(model.ValidationTask);
            model.StartAnnouncementChecks();
            model.Dispose();
            Assert(cancellation.IsCancellationRequested, "closing the launcher did not cancel the announcement request");
            model.PropertyChanged += (_, __) => changes++;
            pending.SetResult(Remote);
            RunTask(model.AnnouncementTask);
            RunTask(model.RefreshAnnouncementAsync());
            Assert(changes == 0 && model.AnnouncementTitle == LauncherAnnouncement.BuiltIn.Title, "late response updated a disposed launcher");
        }

        private static void AssertBuiltIn(MainWindowViewModel model, FakeDesktopInteraction interaction)
        {
            var fallback = LauncherAnnouncement.BuiltIn;
            Assert(model.AnnouncementTitle == fallback.Title && model.AnnouncementSummary == fallback.Summary, "fallback mixed remote and embedded fields");
            model.ShowAnnouncementCommand.Execute(null);
            Assert(interaction.Title == fallback.Title && interaction.Text == LauncherInformation.ReadDocument("ANNOUNCEMENT.md"),
                "fallback did not preserve the original embedded body");
        }

        private static MainWindowViewModel Create(string directory, FakeDesktopInteraction interaction,
            Func<CancellationToken, Task<LauncherAnnouncement>> fetch, Func<DateTime> now) =>
            new MainWindowViewModel(new FakeLauncherBridge(),
                new SettingsService(Path.Combine(directory, "remote-announcement-" + Guid.NewGuid().ToString("N"))),
                new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "玩家" },
                null,
                new FakeClientEnvironment(path => Task.FromResult(ValidDirectory(path))),
                interaction,
                announcements: new AnnouncementViewModel(fetch, now));
    }
}
