using System;
using System.IO;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Imaging;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;
using T7.Rekindle.Desktop.Views;

namespace T7.ManagedHarness
{
    internal static class LauncherDesignTests
    {
        internal static void Run(string directory, string output)
        {
            VerifyWorkAreas();
            var bridge = new FakeLauncherBridge();
            var interaction = new FakeDesktopInteraction();
            using (var model = new MainWindowViewModel(bridge, new SettingsService(directory),
                new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "重燃玩家" }, null,
                path => Task.FromResult(path.Length == 0 ? new ClientDirectoryResult("", "", "请先设置游戏目录") : LauncherTests.ValidDirectory(path)), interaction))
            {
                LauncherTests.RunTask(model.ValidationTask);
                var window = new MainWindow { DataContext = model };
                var root = (FrameworkElement)window.Content;
                var home = (LaunchPage)window.FindName("HomePage");
                var action = (Button)home.FindName("LaunchButton");
                var actionIcon = (Icon)((StackPanel)action.Content).Children[0];
                var settings = (GameSettingsPage)window.FindName("SettingsPage");
                var pages = new[] { 0, 3, 4, 2, 2, 2, 1 };
                var names = new[] { "home", "multiplayer", "update", "settings", "preferences", "game-settings", "about" };
                for (var index = 0; index < pages.Length; index++)
                {
                    model.SelectedPage = pages[index];
                    model.SettingsTabIndex = index == 4 ? 1 : index == 5 ? 2 : 0;
                    foreach (var scale in new[] { 1.0, 1.25, 1.5, 1.75, 2.0 })
                    {
                        Render(window, output, names[index] + "-" + (int)(scale * 100), 1200, 900, scale);
                        VerifyShell(window, root);
                        if (pages[index] == 4)
                            LauncherTests.Assert(((ScrollViewer)((LauncherUpdatePage)window.FindName("UpdatePage")).FindName("UpdateScroll")).ScrollableHeight < 1,
                                "full-height update page scrolls outside the changelog");
                    }
                    Render(window, output, names[index] + "-compact", 1200, 640);
                    VerifyShell(window, root);
                    Render(window, output, names[index] + "-small-work-area", 928, 460);
                    VerifyShell(window, root);
                    if (pages[index] == 3)
                    {
                        var battle = (MultiplayerPage)window.FindName("RoomsPage");
                        var scroll = (ScrollViewer)battle.FindName("ConstructionScroll");
                        LauncherTests.Assert(scroll.ScrollableHeight > 0, "compact construction page cannot scroll");
                        foreach (var name in new[] { "ConstructionHomeButton", "ConstructionFeedbackButton", "ConstructionAboutButton" })
                        {
                            var button = (Button)battle.FindName(name);
                            button.BringIntoView();
                            LauncherTests.Pump();
                            var position = button.TranslatePoint(new Point(), scroll);
                            LauncherTests.Assert(position.Y >= -1 && position.Y + button.ActualHeight <= scroll.ViewportHeight + 1,
                                "construction action is not reachable in a small work area: " + name);
                        }
                    }
                }
                model.IsHomeSelected = true;
                Render(window, output, "home-ready", 1200, 900);
                LauncherTests.Assert(model.StatusText == "准备就绪" && action.IsEnabled && !action.IsDefault,
                    "ready state or explicit launch action is incorrect");
                VerifyStaticActionIcon(actionIcon);
                LauncherTests.Assert(home.FindName("NameInput") == null, "home still edits the player name");
                var longPath = @"C:\Games\" + new string('长', 220) + @"\Windows\Client";
                model.ClientDirectory = longPath;
                LauncherTests.RunTask(model.ValidationTask);
                Render(window, output, "home-long-directory", 928, 640);
                var savedDirectory = (TextBlock)home.FindName("SavedDirectory");
                LauncherTests.Assert(savedDirectory.Text == longPath && savedDirectory.TextTrimming == TextTrimming.CharacterEllipsis
                    && savedDirectory.ToolTip.ToString() == longPath, "long directory was truncated in storage or lost full access");
                model.IsSettingsSelected = true;
                model.SettingsTabIndex = 0;
                Render(window, output, "settings-long-directory", 1200, 900);
                var nameField = (TextBox)settings.FindName("NameInput");
                nameField.SetCurrentValue(TextBox.TextProperty, "改名草稿");
                LauncherTests.Pump();
                LauncherTests.Assert(model.PlayerName == "重燃玩家", "name draft was saved before editing completed");
                nameField.GetBindingExpression(TextBox.TextProperty).UpdateSource();
                LauncherTests.Assert(model.SavedPlayerName == "改名草稿" && model.SettingsFeedback.Length == 0,
                    "committed name was not saved silently");
                nameField.SetCurrentValue(TextBox.TextProperty, "");
                nameField.GetBindingExpression(TextBox.TextProperty).UpdateSource();
                Render(window, output, "settings-validation-error", 1200, 900);
                LauncherTests.Assert(model.NameFieldError.Length != 0 && model.SavedPlayerName == "改名草稿", "invalid name replaced persisted settings");
                model.PlayerName = "重燃玩家";
                model.IsHomeSelected = true;
                model.ClientDirectory = "";
                LauncherTests.RunTask(model.ValidationTask);
                Render(window, output, "home-needs-setup", 1200, 900);
                LauncherTests.Assert(action.IsEnabled && model.MainActionText == "前往设置", "missing config lost the setup action");
                model.ClientDirectory = @"C:\Games\T7";
                LauncherTests.RunTask(model.ValidationTask);
                bridge.HoldStart = true;
                var start = model.MainActionCommand.ExecuteAsync(null);
                LauncherTests.Pump();
                Render(window, output, "home-launching", 1200, 900);
                LauncherTests.Assert(!action.IsEnabled && bridge.StartCount == 1, "busy launch accepts another request");
                VerifyStaticActionIcon(actionIcon);
                model.CancelCommand.Execute(null);
                LauncherTests.RunTask(start);
                Render(window, output, "home-cancelled", 1200, 900);
                bridge.HoldStart = false;
                LauncherTests.RunTask(model.StartCommand.ExecuteAsync(null));
                Render(window, output, "home-running", 1200, 900);
                LauncherTests.Assert(!action.IsEnabled && model.StatusText == "游戏进程运行中" && model.CanStop, "managed process status is misleading");
                VerifyRunningActionIcon(actionIcon);
                model.IsSettingsSelected = true;
                LauncherTests.Pump();
                model.IsHomeSelected = true;
                LauncherTests.Pump();
                VerifyRunningActionIcon(actionIcon);
                bridge.Snapshot = new SessionSnapshot { State = SessionState.StoppingClient };
                model.Refresh();
                Render(window, output, "home-stopping", 1200, 900);
                VerifyStaticActionIcon(actionIcon);
                bridge.Snapshot = new SessionSnapshot { State = SessionState.Failed, ErrorCode = 1003, CleanupComplete = true };
                model.Refresh();
                Render(window, output, "home-failed", 1200, 900);
                LauncherTests.Assert(action.IsEnabled && model.HasSessionLog, "failure lost retry and diagnostics");
                VerifyStaticActionIcon(actionIcon);
                foreach (var severity in new[] { NoticeSeverity.Success, NoticeSeverity.Info, NoticeSeverity.Warning, NoticeSeverity.Error })
                {
                    foreach (var item in new System.Collections.Generic.List<Notice>(model.Notices.Visible)) model.Notices.Dismiss(item);
                    model.Notices.Publish("visual-" + severity, severity == NoticeSeverity.Success ? "诊断信息已复制" : severity == NoticeSeverity.Info ? "暂无正式发布版本"
                        : severity == NoticeSeverity.Warning ? "检查更新失败，不影响本地启动" : "设置未保存，请重试", severity);
                    Render(window, output, "toast-" + severity.ToString().ToLowerInvariant(), 1200, 900);
                }
                var rooms = (MultiplayerPage)window.FindName("RoomsPage");
                model.IsMultiplayerSelected = true;
                Render(window, output, "battle-construction", 1200, 900);
                var homeLink = (Button)rooms.FindName("ConstructionHomeButton");
                var feedbackLink = (Button)rooms.FindName("ConstructionFeedbackButton");
                var aboutLink = (Button)rooms.FindName("ConstructionAboutButton");
                LauncherTests.Assert(((TextBlock)rooms.FindName("ConstructionTitle")).Text == "对战 · 功能建设中"
                    && rooms.FindName("RoomTable") == null && rooms.FindName("JoinRoomButton") == null
                    && Math.Abs(homeLink.ActualWidth - feedbackLink.ActualWidth) <= 1
                    && Math.Abs(homeLink.ActualWidth - aboutLink.ActualWidth) <= 1
                    && homeLink.ActualHeight == feedbackLink.ActualHeight && homeLink.ActualHeight == aboutLink.ActualHeight
                    && ((ScrollViewer)rooms.FindName("ConstructionScroll")).ScrollableHeight < 1,
                    "construction page lost its title, equal actions, or full-height layout");
                RenderArtworkWithoutUi(window, output);
                window.DataContext = null;
                window.Close();
            }
        }

        private static void VerifyStaticActionIcon(Icon icon)
        {
            LauncherTests.Assert(!icon.RenderTransform.HasAnimatedProperties && icon.RenderTransform.Value.IsIdentity,
                "launch icon retained rotation outside the running state");
        }

        private static void VerifyRunningActionIcon(Icon icon)
        {
            var rotation = icon.RenderTransform as RotateTransform;
            LauncherTests.Assert(icon.Kind == "loader" && rotation != null && rotation.HasAnimatedProperties,
                "running game icon is not animated while the launch button is disabled");
            LauncherTests.Assert(icon.RenderTransformOrigin == new Point(0.5, 0.5), "running icon does not rotate around its center");
            LauncherTests.Assert(((FrameworkElement)icon.Parent).RenderTransform.Value.IsIdentity,
                "running animation rotates the button label with the icon");
            var trigger = (DataTrigger)icon.Style.Triggers[0];
            var storyboard = ((BeginStoryboard)trigger.EnterActions[0]).Storyboard;
            foreach (var milliseconds in new[] { 250, 500, 1250 })
            {
                storyboard.SeekAlignedToLastTick(icon, TimeSpan.FromMilliseconds(milliseconds), TimeSeekOrigin.BeginTime);
                LauncherTests.Assert(Math.Abs(rotation.Angle - milliseconds % 1000 * 0.36) < 0.1,
                    "running icon rotation is not a continuous linear loop");
            }
        }

        private static void RenderArtworkWithoutUi(MainWindow window, string output)
        {
            var artwork = (Image)window.FindName("SceneArtwork");
            if (artwork.Visibility != Visibility.Visible) return;
            var root = (Grid)window.Content;
            foreach (UIElement child in root.Children)
                if (child != artwork) child.SetCurrentValue(UIElement.VisibilityProperty, Visibility.Collapsed);
            LauncherLayoutTests.Render(root, null, output, "background-ui-hidden", 1200, 900);
        }

        private static void VerifyShell(MainWindow window, FrameworkElement root)
        {
            LauncherTests.Assert(window.ResizeMode == ResizeMode.CanMinimize, "window is resizable or maximizable");
            VerifyBrandLayout(window, root);
            VerifyHeroArtwork((LaunchPage)window.FindName("HomePage"));
            VerifyHomeSpacing((LaunchPage)window.FindName("HomePage"));
            var sidebar = (Grid)window.FindName("Sidebar");
            var version = (Button)window.FindName("VersionCapsule");
            var point = version.TranslatePoint(new Point(), sidebar);
            LauncherTests.Assert(Math.Abs(point.X + version.ActualWidth / 2 - sidebar.ActualWidth / 2) < 1,
                "version capsule is not centered in the sidebar");
            var navigation = (StackPanel)window.FindName("SidebarNavigation");
            foreach (RadioButton item in navigation.Children)
            {
                var selection = (Border)item.Template.FindName("Selection", item);
                var position = selection.TranslatePoint(new Point(), sidebar);
                var rightInset = sidebar.ActualWidth - position.X - selection.ActualWidth;
                LauncherTests.Assert(position.X >= 15 && rightInset >= 15,
                    "sidebar item background must retain 16 DIP insets on both sides: " + item.Content);
            }
            var image = (Image)window.FindName("SceneArtwork");
            var source = image.Source as BitmapSource;
            LauncherTests.Assert(source != null && source.PixelWidth == 1476 && source.PixelHeight == 1066
                && image.Stretch == Stretch.UniformToFill, "full window artwork is missing or distorted");
        }

        private static void VerifyHomeSpacing(LaunchPage home)
        {
            if (home.Visibility != Visibility.Visible) return;
            var hero = (Border)home.FindName("HeroPanel");
            var announcement = (Border)home.FindName("AnnouncementCard");
            var controls = (Grid)home.FindName("LaunchControls");
            var scroll = (ScrollViewer)home.FindName("HomeScroll");
            var heroBottom = hero.TranslatePoint(new Point(0, hero.ActualHeight), home).Y;
            var announcementTop = announcement.TranslatePoint(new Point(), home).Y;
            LauncherTests.Assert(Math.Abs(announcementTop - heroBottom - 24) <= 1,
                "home banner and announcement spacing is not 24 DIP");
            if (scroll.ScrollableHeight > 0)
            {
                scroll.ScrollToBottom();
                home.UpdateLayout();
                LauncherTests.Pump();
            }
            var announcementBottom = announcement.TranslatePoint(new Point(0, announcement.ActualHeight), home).Y;
            var controlsTop = controls.TranslatePoint(new Point(), home).Y;
            LauncherTests.Assert(Math.Abs(controlsTop - announcementBottom - 24) <= 1,
                "announcement and launch controls spacing is not 24 DIP: " + (controlsTop - announcementBottom));
            if (scroll.VerticalOffset > 0)
            {
                scroll.ScrollToTop();
                home.UpdateLayout();
                LauncherTests.Pump();
            }
        }

        private static void VerifyHeroArtwork(LaunchPage home)
        {
            var artwork = (Image)home.FindName("HeroArtwork");
            var source = artwork.Source as BitmapSource;
            var fallback = (StackPanel)home.FindName("HeroFallback");
            var visibility = (Visibility)Application.Current.Resources["LauncherArtworkVisibility"];
            LauncherTests.Assert(source != null && source.PixelWidth == 1991 && source.PixelHeight == 790
                && artwork.Stretch == Stretch.UniformToFill, "home banner resource is missing or distorted");
            LauncherTests.Assert(fallback != null && artwork.Visibility == visibility
                && fallback.Visibility == (visibility == Visibility.Visible ? Visibility.Collapsed : Visibility.Visible),
                "home banner overlaps its title or lost the theme text fallback");
            var panel = (Border)home.FindName("HeroPanel");
            LauncherTests.Assert(Math.Abs(panel.ActualHeight - panel.ActualWidth * source.PixelHeight / source.PixelWidth) < 1,
                "home banner crops the embedded title in compact layouts");
        }

        private static void VerifyBrandLayout(MainWindow window, FrameworkElement root)
        {
            var compact = root.ActualHeight < 600;
            var brandHeight = compact ? 124 : 180;
            var sidebar = (Grid)window.FindName("Sidebar");
            var brandRow = (RowDefinition)window.FindName("SidebarBrandRow");
            var navigation = (StackPanel)window.FindName("SidebarNavigation");
            var version = (Button)window.FindName("VersionCapsule");
            LauncherTests.Assert(Math.Abs(sidebar.TranslatePoint(new Point(), root).Y) < 1
                && Math.Abs(sidebar.ActualHeight - root.ActualHeight) < 1 && sidebar.ActualWidth == 216,
                "sidebar did not reclaim the left caption area");
            LauncherTests.Assert(Math.Abs(brandRow.ActualHeight - brandHeight) < 1
                && Math.Abs(navigation.TranslatePoint(new Point(), root).Y - brandHeight) < 1,
                "brand layout shifted the navigation start");
            LauncherTests.Assert(Math.Abs(version.TranslatePoint(new Point(), root).Y + version.ActualHeight / 2
                - (root.ActualHeight - (compact ? 48 : 64) / 2.0)) < 1, "brand layout shifted the version capsule");

            var logo = (Image)window.FindName("BrandLogo");
            var fallback = (TextBlock)window.FindName("BrandFallback");
            var source = logo.Source as BitmapSource;
            var visibility = (Visibility)Application.Current.Resources["LauncherArtworkVisibility"];
            LauncherTests.Assert(source != null && source.PixelWidth == 1062 && source.PixelHeight == 962
                && logo.Stretch == Stretch.Uniform, "sidebar brand resource is missing or distorted");
            LauncherTests.Assert(logo.Visibility == visibility
                && fallback.Visibility == (visibility == Visibility.Visible ? Visibility.Collapsed : Visibility.Visible),
                "sidebar brand lost the theme text fallback");
            if (visibility != Visibility.Visible) return;
            var transform = logo.RenderTransform as ScaleTransform;
            LauncherTests.Assert(transform != null && transform.ScaleX == 1.05 && transform.ScaleY == 1.05
                && logo.RenderTransformOrigin == new Point(0.5, 0.5), "sidebar brand is not enlarged uniformly by 5 percent");
            var availableWidth = sidebar.ActualWidth - logo.Margin.Left - logo.Margin.Right;
            var availableHeight = brandHeight - logo.Margin.Top - logo.Margin.Bottom;
            var scale = Math.Min(availableWidth / source.PixelWidth, availableHeight / source.PixelHeight);
            var bounds = logo.TransformToAncestor(sidebar).TransformBounds(new Rect(logo.RenderSize));
            LauncherTests.Assert(Math.Abs(bounds.Width - source.PixelWidth * scale * 1.05) < 1
                && Math.Abs(bounds.Height - source.PixelHeight * scale * 1.05) < 1
                && Math.Abs(bounds.X + bounds.Width / 2 - (logo.Margin.Left + availableWidth / 2)) < 1
                && Math.Abs(bounds.Y + bounds.Height / 2 - (logo.Margin.Top + availableHeight / 2)) < 1,
                "sidebar brand is not centered at its intended display size");
            LauncherTests.Assert(bounds.Left >= -1 && bounds.Top >= -1
                && bounds.Right <= sidebar.ActualWidth + 1 && bounds.Bottom <= brandHeight + 1,
                "enlarged sidebar brand overflows its area or overlaps navigation");
        }

        private static void VerifyWorkAreas()
        {
            LauncherTests.Assert(WindowLayoutController.CalculateSize(1920, 1032, 1, 520, 300) == new Size(520, 300),
                "dialog sizing replaced its preferred size with the main window size");
            foreach (var resolution in new[] { new Size(1920, 1080), new Size(2560, 1440) })
            foreach (var scale in new[] { 1.0, 1.25, 1.5, 1.75, 2.0 })
            {
                var areaHeight = resolution.Height - 48 * scale;
                var result = WindowLayoutController.CalculateSize(resolution.Width, areaHeight, scale);
                LauncherTests.Assert(result.Width <= 1200 && result.Height <= 900
                    && result.Width * scale <= resolution.Width - 32 * scale
                    && result.Height * scale <= areaHeight - 32 * scale
                    && result.Width % 4 == 0 && result.Height % 4 == 0, "work-area fitting exceeds the monitor");
            }
        }

        private static void Render(MainWindow window, string output, string name, double width, double height, double scale = 1)
            => LauncherLayoutTests.Render((FrameworkElement)window.Content, window, output, name, width, height, scale);
    }
}
