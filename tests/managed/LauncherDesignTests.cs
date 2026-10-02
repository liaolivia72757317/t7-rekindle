using System;
using System.IO;
using System.Reflection;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;
using T7.Rekindle.Desktop.Views;

namespace T7.ManagedHarness
{
    internal static class LauncherDesignTests
    {
        internal static void Run(string settingsDirectory, string outputDirectory)
        {
            var bridge = new FakeLauncherBridge();
            var interaction = new FakeDesktopInteraction();
            using (var model = new MainWindowViewModel(bridge, new SettingsService(settingsDirectory),
                new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "重燃玩家" }, null,
                path => Task.FromResult(path.Length == 0
                    ? new ClientDirectoryResult("", "", "请选择游戏根目录。") : LauncherTests.ValidDirectory(path)),
                interaction))
            {
                LauncherTests.RunTask(model.ValidationTask);
                var window = new MainWindow { DataContext = model };
                var root = (FrameworkElement)window.Content;
                var scroll = (ScrollViewer)window.FindName("PageScroll");
                var home = (LaunchPage)((Grid)scroll.Content).Children[0];
                var button = (Button)window.FindName("LaunchButton");
                foreach (var scale in new[] { 1.0, 1.25, 1.5, 2.0 })
                {
                    Render(root, window, outputDirectory, "design-ready-" + (int)(scale * 100), 984, 704, scale);
                    AssertBounds((FrameworkElement)window.FindName("BrandLogo"), root, 32, 14, 368, 118, false);
                    AssertBounds((FrameworkElement)window.FindName("UpdateButton"), root, 766, 22, 186, 46);
                    AssertBounds((FrameworkElement)home.FindName("NameLabel"), root, 34, 237, 200, 26);
                    AssertBounds((FrameworkElement)home.FindName("NameInput"), root, 34, 266, 358, 44);
                    AssertBounds((FrameworkElement)home.FindName("NameHint"), root, 412, 276, 340, 24);
                    AssertBounds((FrameworkElement)home.FindName("StatusCard"), root, 34, 331, 916, 140);
                    AssertBounds((FrameworkElement)window.FindName("ActionFooter"), root, 0, 612, 984, 92);
                    AssertBounds(button, root, 744, 630, 208, 56);
                    LauncherTests.Assert(!button.IsDefault && button.IsEnabled,
                        "Enter from an input must not implicitly launch the game");
                    Render(root, window, outputDirectory, "design-compact-" + (int)(scale * 100), 840, 620, scale);
                    var hint = (TextBlock)home.FindName("NameHint");
                    LauncherTests.Assert(Grid.GetRow(hint) == 1 && scroll.ScrollableWidth == 0,
                        "compact name guidance did not wrap below the input");
                    AssertBounds(button, root, 600, 546, 208, 56);
                }
                Render(root, window, outputDirectory, "design-short-work-area", 840, 480);
                AssertBounds(button, root, 600, 406, 208, 56);
                LauncherTests.Assert(scroll.ScrollableHeight > 0, "short work area lost body scrolling");
                scroll.ScrollToTop();
                var offline = new AboutViewModel(interaction,
                    () => Task.FromException<LauncherUpdateInfo>(new IOException("网络离线，请稍后重试。")));
                var updateArea = (StackPanel)((Button)window.FindName("UpdateButton")).Parent;
                updateArea.DataContext = new { About = offline };
                LauncherTests.RunTask(offline.CheckUpdateCommand.ExecuteAsync(null));
                Render(root, window, outputDirectory, "design-update-offline", 984, 704);
                LauncherTests.Assert(offline.UpdateStatus.Contains("失败") && model.CanStart && button.IsEnabled,
                    "offline update disabled a valid local client");
                updateArea.ClearValue(FrameworkElement.DataContextProperty);
                model.PlayerName = "😀";
                Render(root, window, outputDirectory, "design-invalid-name", 984, 704);
                LauncherTests.Assert(!button.IsEnabled && model.StatusText == "请检查玩家名称", "invalid name still allows launch");
                model.PlayerName = "重燃玩家";
                model.ClientDirectory = "";
                LauncherTests.RunTask(model.ValidationTask);
                Render(root, window, outputDirectory, "design-missing-client", 984, 704);
                LauncherTests.Assert(button.IsEnabled && model.MainActionText == "前往游戏设置", "missing client lost its setup action");
                LauncherTests.RunTask(model.MainActionCommand.ExecuteAsync(null));
                LauncherTests.Assert(model.IsSettingsSelected && bridge.StartCount == 0, "setup action started a game");
                model.ClientDirectory = @"C:\Games\T7";
                LauncherTests.RunTask(model.ValidationTask);
                model.IsHomeSelected = true;
                bridge.PendingStart = new TaskCompletionSource<OperationSnapshot>();
                var start = model.MainActionCommand.ExecuteAsync(null);
                Render(root, window, outputDirectory, "design-launching", 984, 704);
                LauncherTests.Assert(!button.IsEnabled && !model.MainActionCommand.CanExecute(null) && bridge.StartCount == 1,
                    "busy launcher action remained enabled");
                bridge.Snapshot = new SessionSnapshot { State = SessionState.Failed, CleanupComplete = true };
                bridge.PendingStart.SetResult(new OperationSnapshot { Status = OperationStatus.Failed, Error = "客户端启动失败。" });
                LauncherTests.RunTask(start);
                Render(root, window, outputDirectory, "design-launch-error", 984, 704);
                LauncherTests.Assert(button.IsEnabled && model.MainActionText == "重试启动", "launch failure lost the retry action");
                interaction.LogDirectoryError = new IOException("日志目录访问失败，请检查权限。");
                model.OpenLogsCommand.Execute(null);
                Render(root, window, outputDirectory, "design-log-error", 984, 704);
                LauncherTests.Assert(model.IsNoticeError && model.NoticeText.Contains("打开日志目录失败") && model.CanStart,
                    "log directory failure was hidden or disabled launch");
                TestArtworkFallback(window, root, outputDirectory);
                window.DataContext = null;
            }
            TestPendingValidation(settingsDirectory, outputDirectory);
            TestSaveFailure(settingsDirectory, outputDirectory);
        }

        private static void TestPendingValidation(string settingsDirectory, string outputDirectory)
        {
            var pending = new TaskCompletionSource<ClientDirectoryResult>();
            using (var model = new MainWindowViewModel(new FakeLauncherBridge(), new SettingsService(settingsDirectory),
                new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "重燃玩家" }, null,
                path => pending.Task, new FakeDesktopInteraction()))
            {
                var window = new MainWindow { DataContext = model };
                Render((FrameworkElement)window.Content, window, outputDirectory, "design-validating", 984, 704);
                LauncherTests.Assert(!model.MainActionCommand.CanExecute(null), "validation did not lock the primary action");
                model.PlayerName = "😀";
                pending.SetResult(LauncherTests.ValidDirectory(@"C:\Games\T7"));
                LauncherTests.RunTask(model.ValidationTask);
                LauncherTests.Assert(!model.CanStart && model.StatusText == "请检查玩家名称", "late validation ignored the latest name");
                window.DataContext = null;
            }
        }

        private static void TestSaveFailure(string settingsDirectory, string outputDirectory)
        {
            var blocked = Path.Combine(settingsDirectory, "design-save-blocked");
            File.WriteAllText(blocked, "fixture");
            using (var model = new MainWindowViewModel(new FakeLauncherBridge(), new SettingsService(blocked),
                new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "玩家" }, null,
                path => Task.FromResult(LauncherTests.ValidDirectory(path)), new FakeDesktopInteraction()))
            {
                LauncherTests.RunTask(model.ValidationTask);
                model.PlayerName = "重燃玩家";
                var window = new MainWindow { DataContext = model };
                Render((FrameworkElement)window.Content, window, outputDirectory, "design-save-error", 984, 704);
                LauncherTests.Assert(model.StatusText == "配置保存失败" && model.MainActionText == "重试保存" && !model.CanStart,
                    "save error was rendered as ready");
                window.DataContext = null;
            }
        }

        private static void TestArtworkFallback(MainWindow window, FrameworkElement root, string outputDirectory)
        {
            foreach (var handler in new[] { "OnBrandLogoFailed", "OnSceneArtworkFailed" })
                typeof(MainWindow).GetMethod(handler, BindingFlags.Instance | BindingFlags.NonPublic)
                    .Invoke(window, new object[] { window, null });
            Render(root, window, outputDirectory, "design-artwork-fallback", 984, 704);
            LauncherTests.Assert(((Image)window.FindName("BrandLogo")).Visibility == Visibility.Collapsed
                && ((TextBlock)window.FindName("BrandFallback")).Visibility == Visibility.Visible
                && ((Image)window.FindName("SceneArtwork")).Visibility == Visibility.Collapsed
                && ((Button)window.FindName("LaunchButton")).IsEnabled, "artwork failure disabled the launcher");
        }

        private static void AssertBounds(FrameworkElement element, FrameworkElement root,
            double x, double y, double width, double height, bool includeHidden = true)
        {
            if (!includeHidden && element.Visibility != Visibility.Visible) return;
            var position = element.TranslatePoint(new Point(), root);
            LauncherTests.Assert(Math.Abs(position.X - x) <= 2 && Math.Abs(position.Y - y) <= 2
                && Math.Abs(element.ActualWidth - width) <= 2 && Math.Abs(element.ActualHeight - height) <= 2,
                $"design bounds differ: {element.Name} = {position.X},{position.Y},{element.ActualWidth},{element.ActualHeight}");
        }

        private static void Render(FrameworkElement root, MainWindow window, string outputDirectory,
            string name, double width, double height, double scale = 1) =>
            LauncherLayoutTests.Render(root, window, outputDirectory, name, width, height, scale);
    }
}
