using System;
using System.IO;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;
using T7.Rekindle.Desktop.Views;

namespace T7.ManagedHarness
{
    internal static class LaunchControlsLayoutTests
    {
        internal static void Run(string directory, string output)
        {
            foreach (var size in new[] { new Size(1200, 900), new Size(928, 460) })
            foreach (var scale in new[] { 1.0, 1.25, 1.5, 1.75, 2.0 })
                VerifyStates(directory, size.Width == 1200 && scale == 1 ? output : null, size, scale);
        }

        private static void VerifyStates(string directory, string output, Size size, double scale)
        {
            var bridge = new FakeLauncherBridge();
            var interaction = new FakeDesktopInteraction();
            using (var model = new MainWindowViewModel(bridge, new SettingsService(Path.Combine(directory, "launch-controls")),
                new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "玩家" }, null,
                path => Task.FromResult(path.Length == 0
                    ? new ClientDirectoryResult("", "", "目录检查失败：" + new string('长', 160) + "\n请重新选择游戏目录。")
                    : LauncherTests.ValidDirectory(path)), interaction))
            {
                LauncherTests.RunTask(model.ValidationTask);
                var window = new MainWindow { DataContext = model };
                try
                {
                    var root = (FrameworkElement)window.Content;
                    var home = (LaunchPage)window.FindName("HomePage");
                    var controls = (Grid)home.FindName("LaunchControls");
                    var action = (Button)home.FindName("LaunchButton");
                    var elements = new FrameworkElement[]
                    {
                        controls, (Border)controls.Children[0], (Border)controls.Children[1], action,
                        (ScrollViewer)home.FindName("HomeScroll"), (Border)home.FindName("HeroPanel")
                    };
                    LauncherLayoutTests.Render(root, window, output, "launch-controls-ready", size.Width, size.Height, scale);
                    var expected = Array.ConvertAll(elements, element => Bounds(element, root));
                    Action<string> verify = state =>
                    {
                        LauncherLayoutTests.Render(root, window, output, "launch-controls-" + state, size.Width, size.Height, scale);
                        for (var index = 0; index < elements.Length; index++)
                        {
                            var actual = Bounds(elements[index], root);
                            LauncherTests.Assert(Math.Abs(actual.X - expected[index].X) <= 1
                                && Math.Abs(actual.Y - expected[index].Y) <= 1
                                && Math.Abs(actual.Width - expected[index].Width) <= 1
                                && Math.Abs(actual.Height - expected[index].Height) <= 1,
                                "launch state changed the home layout: " + state + " / " + index + " / " + size + " / " + scale
                                    + " / expected " + expected[index] + " / actual " + actual);
                        }
                        LauncherTests.Assert(Math.Abs(controls.ActualHeight - 214) <= 1, "launch cards lost their ready-state height");
                        var card = (Border)controls.Children[1];
                        var status = (Panel)((Grid)action.Parent).Children[1];
                        AssertStatusWithin(status, card);
                        LauncherTests.Assert(Bounds(status, card).Top >= Bounds(action, card).Bottom,
                            "launch status overlaps the primary action: " + state);
                        var detail = (TextBlock)status.Children[2];
                        LauncherTests.Assert(detail.Text == model.HomeStatusDetail && Equals(detail.ToolTip, model.HomeStatusDetail)
                            && detail.TextTrimming == TextTrimming.CharacterEllipsis, "status summary lost access to its full text");
                    };
                    verify("ready");
                    bridge.HoldStart = true;
                    var start = model.MainActionCommand.ExecuteAsync(null);
                    LauncherTests.Pump();
                    LauncherTests.Assert(model.ShowProgress && model.CanCancel && model.HasSessionLog, "launching fixture lacks dynamic controls");
                    verify("starting");
                    model.CancelCommand.Execute(null);
                    LauncherTests.RunTask(start);
                    verify("cancelled");
                    bridge.HoldStart = false;
                    LauncherTests.RunTask(model.StartCommand.ExecuteAsync(null));
                    verify("running");
                    bridge.Snapshot = new SessionSnapshot { State = SessionState.StoppingClient };
                    model.Refresh();
                    verify("stopping");
                    bridge.Snapshot = new SessionSnapshot { State = SessionState.FailedCleaning };
                    model.Refresh();
                    verify("cleanup-failed");
                    bridge.Snapshot = new SessionSnapshot { State = SessionState.Failed, ErrorCode = 1003, CleanupComplete = true };
                    model.Refresh();
                    verify("failed");
                    bridge.Failure = "启动失败：" + new string('长', 220) + "\n错误详情。";
                    LauncherTests.RunTask(model.StartCommand.ExecuteAsync(null));
                    verify("long-error");
                    model.ShowDiagnosticsCommand.Execute(null);
                    LauncherTests.Assert(interaction.Text.Contains(bridge.Failure), "launch diagnostics lost the full error");
                    bridge.Failure = null;
                    LauncherTests.RunTask(model.StartCommand.ExecuteAsync(null));
                    LauncherTests.RunTask(model.StopCommand.ExecuteAsync(null));
                    verify("ready-after-stop");
                    model.ClientDirectory = "";
                    LauncherTests.RunTask(model.ValidationTask);
                    verify("invalid-directory");
                }
                finally
                {
                    window.DataContext = null;
                    window.Close();
                }
            }
        }

        private static Rect Bounds(FrameworkElement element, FrameworkElement root)
            => new Rect(element.TranslatePoint(new Point(), root), element.RenderSize);

        private static void AssertStatusWithin(FrameworkElement element, Border card)
        {
            if (element.Visibility != Visibility.Visible || element.ActualHeight == 0) return;
            LauncherLayoutTests.AssertWithin(element, card, card.ActualWidth, card.ActualHeight);
            if (element is Panel panel)
                foreach (FrameworkElement child in panel.Children) AssertStatusWithin(child, card);
        }
    }
}
