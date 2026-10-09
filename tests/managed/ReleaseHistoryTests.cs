using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Media;
using System.Windows.Threading;
using Newtonsoft.Json.Linq;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;
using T7.Rekindle.Desktop.Views;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class ReleaseHistoryTests
    {
        internal static void Run()
        {
            var previous = SynchronizationContext.Current;
            SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
            try { RunTask(TestFeedAsync()); TestViewModel(); }
            finally { SynchronizationContext.SetSynchronizationContext(previous); }
        }

        private static JObject Entry(string version = "v1.0.0") => new JObject
        {
            ["version"] = version, ["publishedAt"] = "2026-10-09T01:02:03+00:00", ["summary"] = "## 历史说明\n\n- 版本内容",
            ["settingsSchemaVersion"] = 1,
            ["installer"] = new JObject { ["url"] = UpdateFixtures.Mirror + "/releases/" + Uri.EscapeDataString(version)
                + "/" + Uri.EscapeDataString(ReleaseMetadata.InstallerName(version)), ["size"] = 100, ["sha256"] = new string('a', 64) }
        };

        private static JObject Feed(params JObject[] entries) => new JObject
        { ["schemaVersion"] = 1, ["channel"] = "stable", ["entries"] = new JArray(entries) };

        private static async Task TestFeedAsync()
        {
            var feed = Feed(Entry("v1.0.0"), Entry("V2.0.0.0+build.1"), Entry("v3.0.0"));
            using (var handler = new UpdateResponseHandler((request, token) =>
            {
                Assert(request.RequestUri.AbsolutePath == "/updates/stable-history.json" && request.Headers.CacheControl.NoCache,
                    "history used a latest feed or caching");
                return Task.FromResult(UpdateFixtures.Json(feed));
            }))
            using (var client = new HttpClient(handler))
            {
                var service = new ReleaseHistoryService(client, UpdateFixtures.Repository, UpdateFixtures.Mirror, new LauncherBuild(UpdateChannel.Stable, "v2.0.0"));
                var result = await service.GetHistoryAsync(UpdateChannel.Stable, CancellationToken.None);
                Assert(result.Count == 3 && result[0].DisplayVersion == "v3.0.0" && !result[0].CanRollback
                    && !result[1].CanRollback && result[2].CanRollback, "history allowed upgrading or reinstalling");
                Assert(!result[2].ResetsSettings && result[2].Installer.FallbackAddress.StartsWith(UpdateFixtures.Repository), "stable fallback or settings schema lost");
            }
            foreach (var change in new Action<JObject>[] {
                json => json["channel"] = "preview", json => json["schemaVersion"] = 2,
                json => json["entries"][0]["settingsSchemaVersion"] = true,
                json => json["entries"][0]["settingsSchemaVersion"] = 0,
                json => json["entries"][0]["publishedAt"] = "2026-10-09T01:02:03",
                json => json["entries"][0]["installer"]["sha256"] = "bad",
                json => json["entries"][0]["installer"]["url"] = "https://foreign.example/Setup.exe",
                json => ((JArray)json["entries"]).Add(json["entries"][0].DeepClone()) })
            {
                var invalid = Feed(Entry()); change(invalid);
                using (var handler = new UpdateResponseHandler((request, token) => Task.FromResult(UpdateFixtures.Json(invalid))))
                using (var client = new HttpClient(handler))
                {
                    try { await new ReleaseHistoryService(client, UpdateFixtures.Repository, UpdateFixtures.Mirror,
                        new LauncherBuild(UpdateChannel.Stable, "v2.0.0")).GetHistoryAsync(UpdateChannel.Stable, CancellationToken.None);
                        throw new Exception("invalid history was accepted"); }
                    catch (InvalidDataException) { }
                }
            }
            foreach (var status in new[] { HttpStatusCode.NotFound, HttpStatusCode.ServiceUnavailable })
            using (var handler = new UpdateResponseHandler((request, token) => Task.FromResult(new HttpResponseMessage(status))))
            using (var client = new HttpClient(handler))
            {
                var service = new ReleaseHistoryService(client, UpdateFixtures.Repository, UpdateFixtures.Mirror, new LauncherBuild(UpdateChannel.Stable, "v2.0.0"));
                if (status == HttpStatusCode.NotFound) Assert((await service.GetHistoryAsync(UpdateChannel.Stable, CancellationToken.None)).Count == 0, "404 was not empty history");
                else
                {
                    try { await service.GetHistoryAsync(UpdateChannel.Stable, CancellationToken.None); throw new Exception("HTTP failure lost"); }
                    catch (IOException) { Assert(handler.RequestCount == 1, "history silently fell back to another source"); }
                }
            }
            var preview = Entry("v0.1.0");
            preview["build"] = new JObject { ["channel"] = "preview", ["version"] = "0.1.0", ["runId"] = 112,
                ["runNumber"] = 12, ["runAttempt"] = 1, ["commitHash"] = new string('a', 40) };
            preview["installer"]["url"] = UpdateFixtures.Mirror + "/previews/112/1/T7-Rekindle-Setup.exe";
            preview["settingsSchemaVersion"] = 2;
            var previewFeed = Feed(preview); previewFeed["channel"] = "preview";
            foreach (var current in new[] { new LauncherBuild(UpdateChannel.Preview, "v0.1.0", 112, 12, 2, new string('a', 40)),
                new LauncherBuild(UpdateChannel.Stable, "v0.0.1") })
            using (var handler = new UpdateResponseHandler((request, token) => Task.FromResult(UpdateFixtures.Json(previewFeed))))
            using (var client = new HttpClient(handler))
            {
                var entry = (await new ReleaseHistoryService(client, UpdateFixtures.Repository, UpdateFixtures.Mirror, current)
                    .GetHistoryAsync(UpdateChannel.Preview, CancellationToken.None)).Single();
                Assert(entry.CanRollback && entry.ResetsSettings && entry.DisplayVersion == "v0.1.0p12.1" && entry.Installer.FallbackAddress == null,
                    "preview identity, cross-channel rollback or settings reset lost");
                var start = UpdateInstallationService.CreateStartInfo("Setup.exe", Path.GetTempPath(), 123, entry);
                Assert(start.Arguments.Contains("/ROLLBACK=1 /RESETSETTINGS=1 /UPDATECHANNEL=preview"), "rollback installation options lost");
            }
            using (var handler = new UpdateResponseHandler((request, token) => Task.FromException<HttpResponseMessage>(new TaskCanceledException())))
            using (var client = new HttpClient(handler))
            {
                var service = new ReleaseHistoryService(client, UpdateFixtures.Repository, UpdateFixtures.Mirror, new LauncherBuild(UpdateChannel.Stable, "v2.0.0"));
                try { await service.GetHistoryAsync(UpdateChannel.Stable, CancellationToken.None); throw new Exception("timeout not reported"); }
                catch (TimeoutException) { }
                using (var cancellation = new CancellationTokenSource())
                {
                    cancellation.Cancel();
                    try { await service.GetHistoryAsync(UpdateChannel.Stable, cancellation.Token); throw new Exception("cancellation not honored"); }
                    catch (OperationCanceledException) { }
                }
            }
        }

        internal static LauncherHistoryEntry Historical(int schema = 1) => new LauncherHistoryEntry(
            new LauncherBuild(UpdateChannel.Stable, "v1.0.0"), new LauncherBuild(UpdateChannel.Stable, "v2.0.0"),
            DateTimeOffset.UtcNow, "## 版本说明\n\n- 恢复先前版本的行为。\n- 安装前保留设置备份。", schema, UpdateFixtures.Asset());

        private static void TestViewModel()
        {
            var entry = Historical(2);
            var pending = new TaskCompletionSource<string>();
            var installs = 0;
            using (var model = new AboutViewModel(new FakeDesktopInteraction(), () => Task.FromResult(UpdateDownloadViewModelTests.Info()),
                info => new UpdateDownloadViewModel(info, (asset, progress, token, control) => Task.FromResult("latest.exe"),
                    path => Task.FromResult(false), address => { }),
                (channel, token) => Task.FromResult<IReadOnlyList<LauncherHistoryEntry>>(new[] { entry }),
                value => new UpdateDownloadViewModel(value.ToInstallInfo(), (asset, progress, token, control) =>
                { token.Register(() => pending.TrySetCanceled()); return pending.Task; }, path => { installs++; return Task.FromResult(false); }, address => { }, value)))
            {
                RunTask(model.CheckUpdateCommand.ExecuteAsync(null));
                var reminder = model.UpdateReminderIdentity;
                model.IsHistorySelected = true;
                RunTask(model.History.RefreshCommand.ExecutionTask);
                var download = model.History.Download;
                Assert(download.HasPrimaryAction && !download.Info.IsNewVersion && download.PrimaryActionText == "下载此版本", "rollback faked an update");
                var task = download.PrimaryCommand.ExecuteAsync(null); Pump();
                Assert(model.IsUpdating && !model.CheckUpdateCommand.CanExecute(null) && !model.UpdateDownload.PrimaryCommand.CanExecute(null)
                    && !model.History.CanSelect, "rollback was not mutually exclusive");
                download.PauseDownloadCommand.Execute(null);
                Assert(download.IsPaused && model.IsUpdating, "paused rollback released the update gate");
                model.IsHistorySelected = false;
                Assert(download.IsDownloading && model.UpdateReminderIdentity == reminder, "navigation cancelled rollback or changed the reminder");
                download.PauseDownloadCommand.Execute(null);
                pending.SetResult("historical.exe"); RunTask(task);
                Assert(!model.IsUpdating && installs == 0 && download.PrimaryActionText == "立即回退", "download installed without confirmation");
                RunTask(download.PrimaryCommand.ExecuteAsync(null));
                Assert(installs == 1 && download.HasDownloadedInstaller, "declining installation discarded its verified package");
                RunTask(model.CheckUpdateCommand.ExecuteAsync(null));
                RunTask(model.History.RefreshCommand.ExecuteAsync(null));
                Assert(ReferenceEquals(download, model.History.Download) && model.UpdateReminderIdentity == reminder, "refresh mixed latest and historical state");
                model.ResetUpdateChannel(UpdateChannel.Preview);
                Assert(model.History.Entries.Count == 0 && model.History.Download == null && !download.PrimaryCommand.CanExecute(null), "channel change kept stale rollback actions");
            }
        }

        internal static void Render(string output)
        {
            var entry = Historical(2);
            using (var model = new AboutViewModel(new FakeDesktopInteraction(), () => Task.FromResult(new LauncherUpdateInfo()),
                loadHistory: (channel, token) => Task.FromResult<IReadOnlyList<LauncherHistoryEntry>>(new[] { entry }),
                createRollbackDownload: value => new UpdateDownloadViewModel(value.ToInstallInfo(),
                    (asset, progress, token, control) => Task.FromResult("historical.exe"), path => Task.FromResult(false), address => { }, value)))
            {
                model.IsHistorySelected = true;
                RunTask(model.History.RefreshCommand.ExecutionTask);
                var page = new LauncherUpdatePage { DataContext = new { About = model } };
                var root = new Grid { Background = (Brush)Application.Current.Resources["PanelBackgroundBrush"] };
                root.SetValue(TextElement.FontFamilyProperty, Application.Current.Resources["UiFontFamily"]);
                root.SetValue(TextElement.FontSizeProperty, 16.0);
                root.SetResourceReference(TextElement.ForegroundProperty, "TextBrush");
                TextOptions.SetTextFormattingMode(root, TextFormattingMode.Display);
                root.Children.Add(page);
                foreach (var scale in new[] { 1.0, 1.25, 1.5, 2.0 })
                {
                    LauncherLayoutTests.Render(root, null, output, "rollback-history-" + (int)(scale * 100), 944, 796, scale);
                    LauncherLayoutTests.Render(root, null, output, "rollback-history-compact-" + (int)(scale * 100), 704, 560, scale);
                }
                RunTask(model.History.Download.PrimaryCommand.ExecuteAsync(null));
                LauncherLayoutTests.Render(root, null, output, "rollback-history-ready", 704, 560);
                Assert(!((FrameworkElement)page.FindName("UpdateScroll")).IsVisible, "latest page was not hidden when browsing history");
            }
        }
    }
}
