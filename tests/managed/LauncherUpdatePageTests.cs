using System;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Input;
using System.Windows.Media;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;
using T7.Rekindle.Desktop.Views;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class LauncherUpdatePageTests
    {
        internal static void Run(string settingsDirectory, string outputDirectory)
        {
            TestCheckStates();
            TestFeedSummary(false);
            TestFeedSummary(true);
            TestDownloadActions(outputDirectory);
            TestDownloadFallback();
            TestCloseDuringDownload(settingsDirectory, false);
            TestCloseDuringDownload(settingsDirectory, true);
        }

        private static void TestDownloadActions(string outputDirectory)
        {
            var attempt = 0;
            var installs = 0;
            var checks = 0;
            var cancelRequested = false;
            TaskCompletionSource<string> pendingDownload = null;
            var pendingInstall = new TaskCompletionSource<bool>();
            var info = UpdateDownloadViewModelTests.Info();
            info.Installer = new LauncherUpdateAsset(info.Installer.DownloadAddress, info.Installer.FallbackAddress,
                16 * 1024 * 1024, info.Installer.Sha256, "R2");
            using (var model = new AboutViewModel(new FakeDesktopInteraction(), () => { checks++; return Task.FromResult(info); },
                release => new UpdateDownloadViewModel(release, (asset, progress, token, control) =>
                {
                    attempt++;
                    if (attempt == 2) return Task.FromException<string>(new IOException("磁盘写入失败，请检查剩余空间。"));
                    if (attempt == 3) return Task.FromResult("verified-installer.exe");
                    pendingDownload = new TaskCompletionSource<string>();
                    token.Register(() => cancelRequested = true);
                    progress.Report(new UpdateDownloadProgress(asset.Size / 2, asset.Size, "GitHub", "R2 下载失败，已切换到 GitHub。"));
                    return pendingDownload.Task;
                }, path => { installs++; return pendingInstall.Task; }, address => { throw new Exception("unexpected browser"); })))
            {
                RunTask(model.CheckUpdateCommand.ExecuteAsync(null));
                var page = new LauncherUpdatePage { DataContext = new { About = model } };
                var root = CreateRoot(page);
                var action = (Button)page.FindName("UpdateButton");
                var cancel = (Button)page.FindName("CancelDownloadButton");
                RenderDownload(page, root, outputDirectory, "idle");
                AssertAction(page, model.UpdateDownload.PrimaryCommand, "下载更新", "download", true);
                Assert(page.FindName("DownloadButton") == null && page.FindName("PauseDownloadButton") == null,
                    "update page retained duplicate card actions");
                Assert(ReferenceEquals(action.Parent, cancel.Parent) && cancel.Visibility == Visibility.Collapsed
                    && ReferenceEquals(cancel.Style, Application.Current.Resources[typeof(Button)]),
                    "cancellation did not share the header or use the standard button style");
                var windows = Application.Current.Windows.Count;
                action.Command.Execute(null);
                var operation = model.UpdateDownload.PrimaryCommand.ExecutionTask;
                Pump();
                Assert(attempt == 1 && model.IsUpdating && Application.Current.Windows.Count == windows,
                    "download action did not start inline or opened a window");
                Assert(((ProgressBar)page.FindName("DownloadProgress")).Value == 50 && cancel.IsEnabled,
                    "download progress or cancellation was not bound");
                AssertAction(page, model.UpdateDownload.PauseDownloadCommand, "暂停下载", "download", true);
                RenderDownload(page, root, outputDirectory, "downloading");
                action.Command.Execute(null);
                Pump();
                Assert(model.UpdateDownload.IsPaused, "header action did not pause the download");
                AssertAction(page, model.UpdateDownload.PauseDownloadCommand, "继续下载", "play", true);
                RenderDownload(page, root, outputDirectory, "paused");
                RunTask(model.CheckUpdateCommand.ExecuteAsync(null));
                Assert(checks == 1 && !model.CheckUpdateCommand.CanExecute(null) && action.IsEnabled,
                    "update check replaced an active or paused download");
                page.Visibility = Visibility.Collapsed;
                Pump();
                page.Visibility = Visibility.Visible;
                Assert(model.UpdateDownload.IsPaused && model.UpdateDownload.Percent == 50,
                    "leaving the update page discarded the download state");
                action.Command.Execute(null);
                Pump();
                Assert(!model.UpdateDownload.IsPaused, "header action did not resume the download");
                AssertAction(page, model.UpdateDownload.PauseDownloadCommand, "暂停下载", "download", true);
                cancel.Command.Execute(null);
                Pump();
                Assert(cancelRequested && !action.IsEnabled && !cancel.IsEnabled,
                    "pending cancellation retained active download controls");
                pendingDownload.SetCanceled();
                RunTask(operation);
                foreach (var state in new[] { "cancelled", "failed", "completed" })
                {
                    if (state != "cancelled")
                    {
                        action.Command.Execute(null);
                        RunTask(model.UpdateDownload.PrimaryCommand.ExecutionTask);
                    }
                    AssertAction(page, model.UpdateDownload.PrimaryCommand,
                        state == "completed" ? "立即安装" : "下载更新", state == "completed" ? "check-circle" : "download", true);
                    RenderDownload(page, root, outputDirectory, state);
                }
                Assert(installs == 0 && cancel.Visibility == Visibility.Collapsed,
                    "download did not retain explicit installation or hide inactive controls");
                var completed = model.UpdateDownload;
                RunTask(model.CheckUpdateCommand.ExecuteAsync(null));
                Assert(ReferenceEquals(completed, model.UpdateDownload) && completed.HasDownloadedInstaller,
                    "periodic check discarded a verified installer for the same version");
                action.Command.Execute(null);
                operation = completed.PrimaryCommand.ExecutionTask;
                AssertAction(page, completed.PrimaryCommand, "准备安装…", "loader", false);
                RenderDownload(page, root, outputDirectory, "installing");
                Assert(!model.CheckUpdateCommand.CanExecute(null) && cancel.Visibility == Visibility.Collapsed,
                    "installation retained update checks or download cancellation");
                pendingInstall.SetResult(false);
                RunTask(operation);
                Assert(installs == 1 && completed.HasDownloadedInstaller, "declining installation lost the downloaded file");
                AssertAction(page, completed.PrimaryCommand, "立即安装", "check-circle", true);
                info = UpdateDownloadViewModelTests.Info();
                info.TargetVersion = "v1.2.4";
                RunTask(model.CheckUpdateCommand.ExecuteAsync(null));
                Assert(!ReferenceEquals(completed, model.UpdateDownload) && !model.UpdateDownload.HasDownloadedInstaller,
                    "new release reused the previous version's installer");
                AssertAction(page, model.UpdateDownload.PrimaryCommand, "下载更新", "download", true);
                info.IsNewVersion = false;
                RunTask(model.CheckUpdateCommand.ExecuteAsync(null));
                Assert(model.UpdateDownload == null && !model.HasNewUpdate, "latest version retained an update action");
                AssertAction(page, model.CheckUpdateCommand, "检查更新", "refresh", true);
            }
        }

        private static void TestDownloadFallback()
        {
            var info = UpdateDownloadViewModelTests.Info();
            info.Installer = null;
            var interaction = new FakeDesktopInteraction();
            using (var model = new AboutViewModel(interaction, () => Task.FromResult(info)))
            {
                var page = new LauncherUpdatePage { DataContext = new { About = model } };
                RunTask(model.CheckUpdateCommand.ExecuteAsync(null));
                Assert(model.UpdateDownload.PrimaryActionText == "下载更新" && model.UpdateDownload.StatusText.Contains("发布页"),
                    "missing installer did not explain the manual download fallback");
                AssertAction(page, model.UpdateDownload.PrimaryCommand, "下载更新", "download", true);
                ((Button)page.FindName("UpdateButton")).Command.Execute(null);
                RunTask(model.UpdateDownload.PrimaryCommand.ExecutionTask);
                Assert(interaction.Text == info.DownloadAddress, "manual download lost the release page link");
                info = new LauncherUpdateInfo { TargetVersion = "v1.2.4", IsNewVersion = true, DownloadAddress = "invalid" };
                RunTask(model.CheckUpdateCommand.ExecuteAsync(null));
                Assert(!model.UpdateDownload.PrimaryCommand.CanExecute(null), "invalid download address remained enabled");
                AssertAction(page, model.UpdateDownload.PrimaryCommand, "下载更新", "download", false);
            }
        }

        private static void TestCloseDuringDownload(string settingsDirectory, bool paused)
        {
            using (var model = new MainWindowViewModel(new FakeLauncherBridge(), new SettingsService(settingsDirectory),
                new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "玩家" }, null,
                path => Task.FromResult(ValidDirectory(path)), new FakeDesktopInteraction(),
                checkUpdate: () => Task.FromResult(UpdateDownloadViewModelTests.Info()),
                createUpdateDownload: info => new UpdateDownloadViewModel(info, (asset, progress, token, control) =>
                {
                    var pending = new TaskCompletionSource<string>();
                    token.Register(() => pending.TrySetCanceled());
                    return pending.Task;
                }, path => Task.FromResult(false), address => { })))
            {
                RunTask(model.ValidationTask);
                var window = new MainWindow { DataContext = model };
                var closed = false;
                window.Closed += (_, __) => closed = true;
                RunTask(model.About.CheckUpdateCommand.ExecuteAsync(null));
                var operation = model.About.UpdateDownload.PrimaryCommand.ExecuteAsync(null);
                if (paused) model.About.UpdateDownload.PauseDownloadCommand.Execute(null);
                window.Close();
                RunTask(operation);
                Pump();
                Assert(closed && !model.About.IsUpdating && !model.About.UpdateDownload.HasDownloadedInstaller,
                    "closing the launcher did not cancel and await the download; paused=" + paused);
            }
        }

        private static Grid CreateRoot(LauncherUpdatePage page)
        {
            var root = new Grid { Background = (Brush)Application.Current.Resources["PanelBackgroundBrush"] };
            root.SetValue(TextElement.FontFamilyProperty, Application.Current.Resources["UiFontFamily"]);
            root.SetValue(TextElement.FontSizeProperty, 16.0);
            root.SetResourceReference(TextElement.ForegroundProperty, "TextBrush");
            TextOptions.SetTextFormattingMode(root, TextFormattingMode.Display);
            root.Children.Add(page);
            return root;
        }

        private static void RenderDownload(LauncherUpdatePage page, Grid root, string outputDirectory, string state)
        {
            foreach (var scale in new[] { 1.0, 1.25, 1.5, 2.0 })
            {
                LauncherLayoutTests.Render(root, null, outputDirectory, "update-page-" + state + "-" + (int)(scale * 100), 944, 796, scale);
                AssertHeaderLayout(page, root, 944, 796);
                LauncherLayoutTests.Render(root, null, outputDirectory, "update-page-" + state + "-compact-" + (int)(scale * 100), 704, 460, scale);
                AssertHeaderLayout(page, root, 704, 460);
            }
        }

        private static void AssertHeaderLayout(LauncherUpdatePage page, Grid root, double width, double height)
        {
            var action = (Button)page.FindName("UpdateButton");
            var cancel = (Button)page.FindName("CancelDownloadButton");
            var title = (FrameworkElement)page.FindName("UpdateTitle");
            LauncherLayoutTests.AssertWithin(action, root, width, height);
            var firstAction = cancel.Visibility == Visibility.Visible ? cancel : action;
            if (cancel.Visibility == Visibility.Visible)
            {
                LauncherLayoutTests.AssertWithin(cancel, root, width, height);
                Assert(cancel.TranslatePoint(new Point(cancel.ActualWidth, 0), root).X + 8
                    <= action.TranslatePoint(new Point(), root).X, "header buttons overlap");
            }
            Assert(title.TranslatePoint(new Point(title.ActualWidth, 0), root).X + 8
                <= firstAction.TranslatePoint(new Point(), root).X, "update title overlaps the header actions");
        }

        private static void AssertAction(LauncherUpdatePage page, ICommand command, string text, string icon, bool enabled)
        {
            Pump();
            var action = (Button)page.FindName("UpdateButton");
            var content = (StackPanel)action.Content;
            Assert(ReferenceEquals(action.Command, command) && action.IsEnabled == enabled,
                "header action command or availability did not match " + text);
            Assert(((TextBlock)content.Children[1]).Text == text && ((Icon)content.Children[0]).Kind == icon
                && AutomationProperties.GetName(action) == text, "header action presentation did not match " + text);
            Assert(ReferenceEquals(action.Style, Application.Current.Resources["PrimaryButton"]),
                "header action lost its primary button style");
        }

        private static void TestCheckStates()
        {
            var pending = new TaskCompletionSource<LauncherUpdateInfo>();
            var model = new AboutViewModel(new FakeDesktopInteraction(), () => pending.Task);
            var page = new LauncherUpdatePage { DataContext = new { About = model } };
            Pump();
            AssertNotes(page, "检查更新后显示版本更新日志。", "");
            AssertAction(page, model.CheckUpdateCommand, "检查更新", "refresh", true);

            ((Button)page.FindName("UpdateButton")).Command.Execute(null);
            var checking = model.CheckUpdateCommand.ExecutionTask;
            Pump();
            AssertNotes(page, "正在获取更新日志…", "");
            AssertAction(page, model.CheckUpdateCommand, "正在检查…", "loader", false);
            pending.SetResult(new LauncherUpdateInfo
            {
                TargetVersion = "v0.2.0", IsNewVersion = true,
                Summary = "## 发布说明\n\n- 远端更新条目"
            });
            RunTask(checking);
            AssertNotes(page, "远端更新条目", "v0.2.0");
            AssertAction(page, model.UpdateDownload.PrimaryCommand, "下载更新", "download", false);
            Assert(Viewer(page).Document.Blocks.LastBlock is List,
                "update summary was not rendered as Markdown");

            var reopened = new LauncherUpdatePage { DataContext = new { About = model } };
            Pump();
            AssertNotes(reopened, "远端更新条目", "v0.2.0");

            pending = new TaskCompletionSource<LauncherUpdateInfo>();
            checking = model.CheckUpdateCommand.ExecuteAsync(null);
            Pump();
            AssertNotes(page, "正在获取更新日志…", "");
            AssertAction(page, model.CheckUpdateCommand, "正在检查…", "loader", false);
            pending.SetException(new IOException("update failure fixture"));
            RunTask(checking);
            AssertNotes(page, "获取更新日志失败，请稍后重试。", "");
            AssertAction(page, model.CheckUpdateCommand, "检查更新", "refresh", true);

            pending = new TaskCompletionSource<LauncherUpdateInfo>();
            pending.SetResult(new LauncherUpdateInfo { HasPublishedRelease = false, TargetVersion = "未发布" });
            RunTask(model.CheckUpdateCommand.ExecuteAsync(null));
            AssertNotes(page, "暂无正式发布版本。", "");
            AssertAction(page, model.CheckUpdateCommand, "检查更新", "refresh", true);

            pending = new TaskCompletionSource<LauncherUpdateInfo>();
            pending.SetResult(new LauncherUpdateInfo { TargetVersion = "v0.3.0", Summary = "  " });
            RunTask(model.CheckUpdateCommand.ExecuteAsync(null));
            AssertNotes(page, "该版本未填写更新说明。", "v0.3.0");
            AssertAction(page, model.CheckUpdateCommand, "检查更新", "refresh", true);

            page.DataContext = new { About = new AboutViewModel(new FakeDesktopInteraction(), () => pending.Task) };
            Pump();
            AssertNotes(page, "检查更新后显示版本更新日志。", "");
        }

        private static void TestFeedSummary(bool fallback)
        {
            using (var handler = new UpdateResponseHandler((request, token) =>
            {
                if (request.RequestUri.Host == "api.github.com")
                {
                    var release = UpdateFixtures.Release();
                    release["body"] = "- GitHub 发布条目";
                    return Task.FromResult(UpdateFixtures.Json(release));
                }
                if (fallback) return Task.FromResult(new HttpResponseMessage(HttpStatusCode.ServiceUnavailable));
                var manifest = UpdateFixtures.Manifest();
                manifest["summary"] = "- 清单发布条目";
                return Task.FromResult(UpdateFixtures.Json(manifest));
            }))
            using (var client = new HttpClient(handler))
            {
                var service = new ReleaseUpdateService(client, UpdateFixtures.Repository, UpdateFixtures.Mirror);
                foreach (var current in new[] { "v1.0.0", "v1.2.3", "v2.0.0" })
                {
                    var model = new AboutViewModel(new FakeDesktopInteraction(), () => service.CheckAsync(current));
                    var page = new LauncherUpdatePage { DataContext = new { About = model } };
                    RunTask(model.CheckUpdateCommand.ExecuteAsync(null));
                    AssertNotes(page, fallback ? "GitHub 发布条目" : "清单发布条目", "v1.2.3");
                }
            }
        }

        private static FlowDocumentScrollViewer Viewer(LauncherUpdatePage page) =>
            (FlowDocumentScrollViewer)page.FindName("ChangelogViewer");

        private static void AssertNotes(LauncherUpdatePage page, string expected, string version)
        {
            var document = Viewer(page).Document;
            var text = document == null ? "" : new TextRange(document.ContentStart, document.ContentEnd).Text;
            Assert(text.Contains(expected) && !text.Contains("启动器版本日志") && !text.Contains("0.1.0"),
                "update page did not render the current check result: " + text);
            Assert(((TextBlock)page.FindName("ChangelogVersion")).Text == version,
                "update log version did not match the fetched release");
        }
    }
}
