using System;
using System.Diagnostics;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;
using T7.Rekindle.Desktop.Views;

namespace T7.ManagedHarness
{
    internal static class LauncherLayoutTests
    {
        internal static void Run(string outputDirectory)
        {
            var context = System.Threading.SynchronizationContext.Current;
            System.Threading.SynchronizationContext.SetSynchronizationContext(new System.Windows.Threading.DispatcherSynchronizationContext());
            var bindingErrors = new BindingErrors();
            PresentationTraceSources.DataBindingSource.Listeners.Add(bindingErrors);
            PresentationTraceSources.DataBindingSource.Switch.Level = SourceLevels.Warning;
            var settingsDirectory = Path.Combine(Path.GetTempPath(), "T7-layout-" + Guid.NewGuid().ToString("N"));
            try
            {
                var bridge = new FakeLauncherBridge();
                var interaction = new FakeDesktopInteraction();
                using (var model = new MainWindowViewModel(bridge, new SettingsService(settingsDirectory),
                    new UserSettings { ClientDirectory = @"C:\Games\刀锋铁骑", PlayerName = "Rekindler" }, null,
                    path => System.Threading.Tasks.Task.FromResult(path.Length == 0 || path.Contains("Missing")
                        ? new ClientDirectoryResult("", "", "请选择游戏根目录，目录中应包含 Bin、Data 和 vfs。")
                        : LauncherTests.ValidDirectory(path)), interaction))
                {
                    LauncherTests.RunTask(model.ValidationTask);
                    var window = new MainWindow { DataContext = model };
                    var root = (FrameworkElement)window.Content;
                    var pages = (Grid)((ScrollViewer)window.FindName("PageScroll")).Content;
                    var home = (LaunchPage)pages.Children[0];
                    var settings = (GameSettingsPage)pages.Children[1];
                    var directoryInput = (TextBox)settings.FindName("DirectoryInput");
                    foreach (var scale in new[] { 1.0, 1.25, 1.5, 2.0 })
                        Render(root, window, outputDirectory, "home-" + (int)(scale * 100), 884, 621, scale);
                    LauncherTests.Assert(home.Visibility == Visibility.Visible && settings.Visibility == Visibility.Collapsed,
                        "game settings appeared on the launch page");
                    model.IsSettingsSelected = true;
                    foreach (var scale in new[] { 1.0, 1.25, 1.5, 2.0 })
                    {
                        Render(root, window, outputDirectory, "settings-" + (int)(scale * 100), 884, 621, scale);
                        Render(root, window, outputDirectory, "settings-compact-" + (int)(scale * 100), 784, 561, scale);
                    }
                    LauncherTests.Assert(settings.Visibility == Visibility.Visible && home.Visibility == Visibility.Collapsed
                        && directoryInput.Text == model.ClientDirectory && !directoryInput.IsReadOnly, "settings page binding or visibility is invalid");
                    var directorySymbol = (TextBlock)settings.FindName("DirectoryStatusSymbol");
                    AssertBrush(directorySymbol, "SuccessBrush");
                    model.ClientDirectory = @"C:\Games\Missing";
                    LauncherTests.RunTask(model.ValidationTask);
                    Render(root, window, outputDirectory, "settings-invalid", 784, 561);
                    AssertBrush(directorySymbol, "DangerBrush");
                    LauncherTests.Assert(directorySymbol.Text == "!" && !model.CanStart, "invalid directory lost its text indicator");
                    directoryInput.SetCurrentValue(TextBox.TextProperty, @"C:\Games\T7");
                    LauncherTests.RunTask(model.ValidationTask);
                    LauncherTests.Assert(model.ClientDirectory == @"C:\Games\T7" && model.CanStart, "settings input did not update shared configuration");
                    model.SelectedPage = 1;
                    foreach (var scale in new[] { 1.0, 1.25, 1.5, 2.0 })
                    {
                        Render(root, window, outputDirectory, "about-" + (int)(scale * 100), 884, 621, scale);
                        Render(root, window, outputDirectory, "about-compact-" + (int)(scale * 100), 784, 561, scale);
                    }
                    model.About.CopyRepositoryCommand.Execute(null);
                    Render(root, window, outputDirectory, "about-copy-feedback", 784, 561);
                    var footerFeedback = (TextBlock)window.FindName("FooterFeedback");
                    LauncherTests.Assert(footerFeedback.Text == model.About.Feedback, "copy feedback is outside the fixed footer");
                    AssertWithin(footerFeedback, root, 784, 561);
                    model.SelectedPage = 0;
                    LauncherTests.Pump();
                    LauncherTests.Assert(footerFeedback.Text == model.SettingsFeedback, "about feedback leaked into the launch page");
                    bridge.Snapshot = new SessionSnapshot { State = SessionState.StartingRuntime };
                    model.Refresh();
                    Render(root, window, outputDirectory, "starting", 884, 621);
                    bridge.Snapshot = new SessionSnapshot { State = SessionState.Running };
                    model.Refresh();
                    Render(root, window, outputDirectory, "running", 884, 621);
                    model.IsSettingsSelected = true;
                    Render(root, window, outputDirectory, "settings-running", 884, 621);
                    LauncherTests.Assert(directoryInput.IsReadOnly, "running game directory remained editable in settings");
                    model.ShowHomeCommand.Execute(null);
                    bridge.Snapshot = new SessionSnapshot { State = SessionState.Failed, ErrorCode = 1003, CleanupComplete = true };
                    model.Refresh();
                    Render(root, window, outputDirectory, "failed", 884, 621);
                    foreach (var scale in new[] { 1.0, 1.25, 1.5, 2.0 })
                        Render(root, window, outputDirectory, "compact-" + (int)(scale * 100), 784, 561, scale);
                    bridge.Snapshot = new SessionSnapshot { State = SessionState.Idle, CleanupComplete = true };
                    model.Refresh();
                    model.ClientDirectory = "";
                    model.PlayerName = "";
                    model.LogsExpanded = false;
                    LauncherTests.RunTask(model.ValidationTask);
                    Render(root, window, outputDirectory, "first-run", 884, 621);
                    model.IsSettingsSelected = true;
                    Render(root, window, outputDirectory, "settings-first-run", 884, 621);
                    AssertBrush(directorySymbol, "MutedTextBrush");
                    interaction.DirectoryError = new IOException("目录选择器暂不可用。");
                    model.BrowseCommand.Execute(null);
                    Render(root, window, outputDirectory, "settings-dialog-error", 784, 561);
                    AssertBrush((TextBlock)settings.FindName("NoticeBanner"), "DangerBrush");
                    model.CopyLogsCommand.Execute(null);
                    model.ShowHomeCommand.Execute(null);
                    Render(root, window, outputDirectory, "log-copy-feedback", 784, 561);
                    AssertBrush((TextBlock)home.FindName("NoticeBanner"), "PrimaryBrush");
                    window.DataContext = null;
                }
                TestFirstRunLayout(settingsDirectory, outputDirectory);
                TestDirectorySearchLayout(settingsDirectory, outputDirectory);
                TestUpdateLayout(outputDirectory);
                TestUpdateProgressLayout(outputDirectory);
                TestTextDialogLayout(outputDirectory);
                TestMarkdownDialogLayout(outputDirectory);
                LauncherTests.Assert(bindingErrors.Messages.Length == 0, "WPF binding error: " + bindingErrors.Messages);
            }
            finally
            {
                PresentationTraceSources.DataBindingSource.Listeners.Remove(bindingErrors);
                System.Threading.SynchronizationContext.SetSynchronizationContext(context);
                if (Directory.Exists(settingsDirectory)) Directory.Delete(settingsDirectory, true);
            }
        }

        private static void TestFirstRunLayout(string settingsDirectory, string outputDirectory)
        {
            using (var model = new MainWindowViewModel(new FakeLauncherBridge(), new SettingsService(settingsDirectory), new UserSettings(), null,
                path => System.Threading.Tasks.Task.FromResult(new ClientDirectoryResult("", "", "请选择游戏根目录。")), new FakeDesktopInteraction()))
            {
                LauncherTests.RunTask(model.ValidationTask);
                var window = new MainWindow { DataContext = model };
                var root = (FrameworkElement)window.Content;
                var pages = (Grid)((ScrollViewer)window.FindName("PageScroll")).Content;
                var input = (TextBox)((LaunchPage)pages.Children[0]).FindName("NameInput");
                foreach (var scale in new[] { 1.0, 1.25, 1.5, 2.0 })
                    Render(root, window, outputDirectory, "first-run-fresh-" + (int)(scale * 100), 884, 621, scale);
                LauncherTests.Assert(!model.CanStart && Equals(input.BorderBrush, Application.Current.Resources["ControlBorderBrush"]),
                    "fresh empty name must remain neutral while launch is disabled");
                model.PlayerName = " ";
                Render(root, window, outputDirectory, "first-run-edited", 784, 561);
                LauncherTests.Assert(!model.CanStart && Equals(input.BorderBrush, Application.Current.Resources["DangerBrush"]),
                    "edited empty name must show an error without enabling launch");
                window.DataContext = null;
            }
        }

        private static void TestDirectorySearchLayout(string settingsDirectory, string outputDirectory)
        {
            var pending = new System.Threading.Tasks.TaskCompletionSource<ClientDirectoryResult>();
            var interaction = new FakeDesktopInteraction { DirectorySelection = @"C:\Games" };
            using (var model = new MainWindowViewModel(new FakeLauncherBridge(), new SettingsService(settingsDirectory),
                new UserSettings { ClientDirectory = @"C:\Games\Selected", PlayerName = "Player" }, null,
                path => System.Threading.Tasks.Task.FromResult(LauncherTests.ValidDirectory(path)), interaction,
                (path, token) => pending.Task))
            {
                LauncherTests.RunTask(model.ValidationTask);
                model.IsSettingsSelected = true;
                var window = new MainWindow { DataContext = model };
                var root = (FrameworkElement)window.Content;
                var pages = (Grid)((ScrollViewer)window.FindName("PageScroll")).Content;
                var input = (TextBox)((GameSettingsPage)pages.Children[1]).FindName("DirectoryInput");
                model.BrowseCommand.Execute(null);
                Render(root, window, outputDirectory, "settings-searching", 784, 561);
                LauncherTests.Assert(input.Text == @"C:\Games" && !input.IsReadOnly && !model.CanStart,
                    "search progress locked manual input or displayed the previous path");
                pending.SetResult(new ClientDirectoryResult(@"C:\Games\T7", @"C:\Games\T7\Bin", "已自动定位游戏目录。已找到 Bin\\TieJiClient.exe"));
                LauncherTests.RunTask(model.ValidationTask);
                foreach (var scale in new[] { 1.0, 1.25, 1.5, 2.0 })
                    Render(root, window, outputDirectory, "settings-located-" + (int)(scale * 100), 784, 561, scale);
                LauncherTests.Assert(input.Text == @"C:\Games\T7" && model.CanStart, "located root was not echoed into the directory input");
                window.DataContext = null;
            }
        }

        private static void TestUpdateLayout(string outputDirectory)
        {
            var info = new LauncherUpdateInfo
            {
                CurrentVersion = "v0.1.0", TargetVersion = "v0.2.0", IsNewVersion = true,
                Summary = "• 改进启动流程。\n• 完善诊断信息。", DownloadAddress = LauncherInformation.DownloadAddress
            };
            var update = new UpdateDialog(info);
            var root = (FrameworkElement)update.Content;
            foreach (var scale in new[] { 1.0, 1.25, 1.5, 2.0 })
            {
                Render(root, null, outputDirectory, "update-dialog-" + (int)(scale * 100), 544, 381, scale);
                AssertWithin((Button)update.FindName("LaterButton"), root, 544, 381);
                AssertWithin((Button)update.FindName("DownloadButton"), root, 544, 381);
            }
            AssertLineSpacing((TextBox)update.FindName("SummaryText"));
            var longUpdate = new UpdateDialog(new LauncherUpdateInfo
            {
                CurrentVersion = info.CurrentVersion,
                TargetVersion = info.TargetVersion,
                IsNewVersion = true,
                DownloadAddress = info.DownloadAddress,
                Summary = string.Join("\n", new string[80]).Replace("\n", "更新条目：长内容应滚动阅读。\n")
            });
            root = (FrameworkElement)longUpdate.Content;
            Render(root, null, outputDirectory, "update-dialog-long", 464, 361);
            var scroll = (ScrollViewer)longUpdate.FindName("UpdateScroll");
            LauncherTests.Assert(scroll.ScrollableHeight > 0, "long update summary is not scrollable");
            AssertWithin((Button)longUpdate.FindName("LaterButton"), root, 464, 361);
            AssertWithin((Button)longUpdate.FindName("DownloadButton"), root, 464, 361);
            var current = new UpdateDialog(new LauncherUpdateInfo { CurrentVersion = info.CurrentVersion, TargetVersion = info.CurrentVersion,
                Summary = "当前已是最新版本。", IsNewVersion = false });
            Render((FrameworkElement)current.Content, null, outputDirectory, "update-dialog-current", 464, 361);
            LauncherTests.Assert(((Button)current.FindName("DownloadButton")).Visibility == Visibility.Collapsed
                && ((TextBlock)current.FindName("Heading")).Text.Contains("最新"), "up-to-date dialog still offers a new version");
            var unpublished = new UpdateDialog(new LauncherUpdateInfo
            {
                CurrentVersion = info.CurrentVersion, TargetVersion = "未发布", HasPublishedRelease = false,
                Summary = "GitHub Releases 暂无正式版本。\n开发构建请查看项目仓库的 Actions 页面。", DownloadAddress = info.DownloadAddress
            });
            foreach (var scale in new[] { 1.0, 1.25, 1.5, 2.0 })
            {
                Render((FrameworkElement)unpublished.Content, null, outputDirectory, "update-dialog-unpublished-" + (int)(scale * 100), 464, 361, scale);
                AssertWithin((Button)unpublished.FindName("DownloadButton"), (FrameworkElement)unpublished.Content, 464, 361);
            }
            LauncherTests.Assert(((TextBlock)unpublished.FindName("Heading")).Text == "暂无正式发布版本"
                && ((Button)unpublished.FindName("DownloadButton")).Visibility == Visibility.Visible,
                "unpublished release dialog reported an up-to-date version or lost the release page link");
        }

        private static void TestUpdateProgressLayout(string outputDirectory)
        {
            var attempt = 0;
            var info = UpdateDialogTests.Info();
            info.Installer = new LauncherUpdateAsset(info.Installer.DownloadAddress, info.Installer.FallbackAddress,
                16 * 1024 * 1024, info.Installer.Sha256, "R2");
            using (var model = new UpdateDialogViewModel(info, (asset, progress, token) =>
            {
                attempt++;
                if (attempt == 2) return System.Threading.Tasks.Task.FromException<string>(new IOException("磁盘写入失败，请检查剩余空间。"));
                if (attempt == 3) return System.Threading.Tasks.Task.FromResult("verified-installer.exe");
                var pending = new System.Threading.Tasks.TaskCompletionSource<string>();
                token.Register(() => pending.TrySetCanceled());
                progress.Report(new UpdateDownloadProgress(asset.Size / 2, asset.Size, "GitHub", "R2 下载失败，已切换到 GitHub。"));
                return pending.Task;
            }, path => System.Threading.Tasks.Task.FromResult(false), address => { }))
            {
                var dialog = new UpdateDialog(info, model);
                var root = (FrameworkElement)dialog.Content;
                var operation = model.PrimaryCommand.ExecuteAsync(null);
                LauncherTests.Pump();
                foreach (var scale in new[] { 1.0, 1.25, 1.5, 2.0 })
                {
                    Render(root, null, outputDirectory, "update-downloading-" + (int)(scale * 100), 464, 381, scale);
                    AssertWithin((Button)dialog.FindName("CancelDownloadButton"), root, 464, 381);
                    AssertWithin((Button)dialog.FindName("DownloadButton"), root, 464, 381);
                }
                LauncherTests.Assert(((ProgressBar)dialog.FindName("DownloadProgress")).Value > 0
                    && ((Button)dialog.FindName("CancelDownloadButton")).IsEnabled, "download controls were not bound");
                LauncherTests.RunTask(model.CancelAndWaitAsync());
                LauncherTests.RunTask(operation);
                foreach (var state in new[] { "cancelled", "failed", "completed" })
                {
                    if (state != "cancelled") LauncherTests.RunTask(model.PrimaryCommand.ExecuteAsync(null));
                    foreach (var scale in new[] { 1.0, 1.25, 1.5, 2.0 })
                        Render(root, null, outputDirectory, "update-" + state + "-" + (int)(scale * 100), 464, 381, scale);
                }
                LauncherTests.Assert(((Button)dialog.FindName("DownloadButton")).Content.ToString() == "立即安装",
                    "completed download lost its explicit installation action");
                dialog.Close();
            }
            using (var model = new UpdateDialogViewModel(info, (asset, progress, token) =>
            {
                var pending = new System.Threading.Tasks.TaskCompletionSource<string>();
                token.Register(() => pending.TrySetCanceled());
                return pending.Task;
            }, path => System.Threading.Tasks.Task.FromResult(false), address => { }))
            {
                var dialog = new UpdateDialog(info, model);
                var closed = false;
                dialog.Closed += (_, __) => closed = true;
                var operation = model.PrimaryCommand.ExecuteAsync(null);
                dialog.Close();
                LauncherTests.RunTask(operation);
                LauncherTests.Pump();
                LauncherTests.Assert(closed && !model.IsDownloading && !model.HasDownloadedInstaller,
                    "closing the update window did not cancel and await the active download");
            }
        }

        private static void TestTextDialogLayout(string outputDirectory)
        {
            var text = string.Join("\n", new string[80]).Replace("\n", "启动器说明：支持中文、English 和版本号 v0.1.0。\n");
            var dialog = new TextDialog("开源软件说明", text);
            var root = (FrameworkElement)dialog.Content;
            foreach (var scale in new[] { 1.0, 1.25, 1.5, 2.0 })
            {
                Render(root, null, outputDirectory, "text-dialog-" + (int)(scale * 100), 644, 441, scale);
                AssertWithin((Button)dialog.FindName("CloseButton"), root, 644, 441);
                Render(root, null, outputDirectory, "text-dialog-compact-" + (int)(scale * 100), 464, 281, scale);
                AssertWithin((Button)dialog.FindName("CloseButton"), root, 464, 281);
            }
            var document = (TextBox)dialog.FindName("DocumentText");
            AssertLineSpacing(document);
            LauncherTests.Assert(document.ExtentHeight > document.ViewportHeight, "long document is not scrollable");
            var address = new TextDialog("下载地址", LauncherInformation.DownloadAddress);
            root = (FrameworkElement)address.Content;
            LauncherTests.Assert(address.Height == 260 && address.MinHeight == 220, "single address uses the full document window");
            Render(root, null, outputDirectory, "address-dialog", 544, 221);
            AssertWithin((Button)address.FindName("CloseButton"), root, 544, 221);
            Render(root, null, outputDirectory, "address-dialog-compact", 464, 181);
            AssertWithin((Button)address.FindName("CloseButton"), root, 464, 181);
        }

        private static void TestMarkdownDialogLayout(string outputDirectory)
        {
            foreach (var name in new[] { "CHANGELOG", "THANKS" })
            {
                var dialog = new TextDialog(name == "CHANGELOG" ? "版本日志" : "特别感谢", LauncherInformation.ReadDocument(name + ".md"), true);
                var root = (FrameworkElement)dialog.Content;
                var viewer = (FlowDocumentScrollViewer)dialog.FindName("MarkdownViewer");
                foreach (var scale in new[] { 1.0, 1.25, 1.5, 2.0 })
                {
                    Render(root, null, outputDirectory, "markdown-" + name.ToLowerInvariant() + "-" + (int)(scale * 100), 644, 441, scale);
                    AssertWithin((Button)dialog.FindName("CloseButton"), root, 644, 441);
                    Render(root, null, outputDirectory, "markdown-" + name.ToLowerInvariant() + "-compact-" + (int)(scale * 100), 464, 281, scale);
                    AssertWithin((Button)dialog.FindName("CloseButton"), root, 464, 281);
                }
                LauncherTests.Assert(Equals(viewer.Document.FontFamily, Application.Current.Resources["UiFontFamily"])
                    && Equals(viewer.Document.Foreground, Application.Current.Resources["TextBrush"]), "Markdown ignored the current font or theme");
                viewer.Selection.Select(viewer.Document.ContentStart, viewer.Document.ContentEnd);
                LauncherTests.Assert(viewer.Selection.Text.Length > 0 && !viewer.Selection.Text.Contains("# ")
                    && !viewer.Selection.Text.Contains("`"), "rendered Markdown was not selectable without source markers");
            }
            var longText = "# 长文档\n\n" + string.Join("\n", new string[80]).Replace("\n", "- 启动器说明：支持中文和 English。\n");
            var longDialog = new TextDialog("版本日志", longText, true);
            var content = (FrameworkElement)longDialog.Content;
            Render(content, null, outputDirectory, "markdown-long", 464, 281);
            var longViewer = (FlowDocumentScrollViewer)longDialog.FindName("MarkdownViewer");
            var scroll = longViewer.Template.FindName("PART_ContentHost", longViewer) as ScrollViewer;
            LauncherTests.Assert(scroll != null && scroll.ScrollableHeight > 0, "long Markdown document was not scrollable");
            AssertWithin((Button)longDialog.FindName("CloseButton"), content, 464, 281);
        }

        private static void AssertLineSpacing(TextBox text)
        {
            var firstLine = text.GetRectFromCharacterIndex(0);
            var secondLine = text.GetRectFromCharacterIndex(text.Text.IndexOf('\n') + 1);
            LauncherTests.Assert(secondLine.Top - firstLine.Top >= 21, "document lines are too tightly spaced");
        }

        private static void AssertTypography(DependencyObject element)
        {
            var family = element is TextBlock text ? text.FontFamily : (element as Control)?.FontFamily;
            if (family != null)
            {
                LauncherTests.Assert(Equals(family, Application.Current.Resources["UiFontFamily"])
                    || Equals(family, Application.Current.Resources["CodeFontFamily"]), "inconsistent font family: " + family);
                LauncherTests.Assert(TextOptions.GetTextFormattingMode(element) == TextFormattingMode.Display,
                    "text formatting did not inherit the window setting");
            }
            for (var index = 0; index < VisualTreeHelper.GetChildrenCount(element); index++)
                AssertTypography(VisualTreeHelper.GetChild(element, index));
        }

        private static void AssertBrush(TextBlock text, string key) =>
            LauncherTests.Assert(Equals(text.Foreground, Application.Current.Resources[key]), "semantic foreground mismatch: " + text.Name + " / " + key);

        private static void AssertWithin(FrameworkElement element, FrameworkElement root, double width, double height)
        {
            var position = element.TranslatePoint(new Point(), root);
            LauncherTests.Assert(element.ActualHeight > 0 && position.X >= 0 && position.Y >= 0
                && position.X + element.ActualWidth <= width + 1 && position.Y + element.ActualHeight <= height + 1,
                "element escaped the client viewport: " + element.Name);
        }

        private static void Render(FrameworkElement root, MainWindow window, string outputDirectory,
            string name, double width, double height, double scale = 1)
        {
            VisualTreeHelper.SetRootDpi(root, new DpiScale(scale, scale));
            root.Measure(new Size(width, height));
            root.Arrange(new Rect(0, 0, width, height));
            root.UpdateLayout();
            LauncherTests.Pump();
            AssertTypography(root);
            if (window != null)
            {
                var button = (Button)window.FindName("LaunchButton");
                var position = button.TranslatePoint(new Point(), root);
                LauncherTests.Assert(button.ActualHeight >= 42 && position.Y + button.ActualHeight <= height + 1,
                    "fixed launch action escaped viewport: " + name);
                LauncherTests.Assert(((ScrollViewer)window.FindName("PageScroll")).ActualHeight > 0, "content viewport collapsed");
                var caption = FindText(button);
                LauncherTests.Assert(caption != null && Equals(caption.Foreground, button.Foreground), "button caption lost semantic foreground");
            }
            if (outputDirectory == null) return;
            Directory.CreateDirectory(outputDirectory);
            var bitmap = new RenderTargetBitmap((int)Math.Ceiling(width * scale), (int)Math.Ceiling(height * scale),
                96 * scale, 96 * scale, PixelFormats.Pbgra32);
            bitmap.Render(root);
            var background = new DrawingVisual();
            using (var drawing = background.RenderOpen())
            {
                drawing.DrawRectangle(((Panel)root).Background, null, new Rect(0, 0, width, height));
                drawing.DrawImage(bitmap, new Rect(0, 0, width, height));
            }
            var composite = new RenderTargetBitmap(bitmap.PixelWidth, bitmap.PixelHeight, 96 * scale, 96 * scale, PixelFormats.Pbgra32);
            composite.Render(background);
            var encoder = new PngBitmapEncoder();
            encoder.Frames.Add(BitmapFrame.Create(composite));
            using (var file = File.Create(Path.Combine(outputDirectory, name + ".png"))) encoder.Save(file);
        }

        private sealed class BindingErrors : TraceListener
        {
            public string Messages { get; private set; } = string.Empty;
            public override void Write(string message) { Messages += message; }
            public override void WriteLine(string message) { Messages += message + Environment.NewLine; }
        }

        private static TextBlock FindText(DependencyObject root)
        {
            if (root is TextBlock text) return text;
            for (var index = 0; index < VisualTreeHelper.GetChildrenCount(root); index++)
            {
                var found = FindText(VisualTreeHelper.GetChild(root, index));
                if (found != null) return found;
            }
            return null;
        }
    }
}



