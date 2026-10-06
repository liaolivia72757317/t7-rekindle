using System;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Automation.Peers;
using System.Windows.Automation.Provider;
using System.Windows.Controls;
using System.Windows.Documents;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;
using T7.Rekindle.Desktop.Views;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class AnnouncementTests
    {
        private const string Title = "关于「铁骑·重燃」";
        private const string Summary = "从大学时光里的热爱，到停服后的不舍，我开始尝试自己动手，让《刀锋铁骑》重新运行起来。"
            + "「铁骑·重燃」是一个独立、非营利的开源项目，目前仍在开发，将先推进本地人机对战，再逐步支持局域网联机。"
            + "想和大家聊聊它的起点，以及接下来的打算。";

        internal static void Run(string directory, string output)
        {
            var bridge = new FakeLauncherBridge();
            var interaction = new FakeDesktopInteraction();
            using (var model = new MainWindowViewModel(bridge, new SettingsService(Path.Combine(directory, "announcement")),
                new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "玩家" }, null,
                path => Task.FromResult(ValidDirectory(path)), interaction))
            {
                RunTask(model.ValidationTask);
                var window = new MainWindow { DataContext = model };
                try
                {
                    var root = (FrameworkElement)window.Content;
                    LauncherLayoutTests.Render(root, window, output, "home-announcement", 1200, 900);
                    var home = (LaunchPage)window.FindName("HomePage");
                    var title = home.FindName("AnnouncementTitle") as TextBlock;
                    var summary = home.FindName("AnnouncementSummary") as TextBlock;
                    var button = home.FindName("ReadAnnouncementButton") as Button;
                    Assert(title?.Text == Title && summary?.Text == Summary && button != null,
                        "home announcement did not preserve its title, summary, and full-text action");
                    Assert(summary.TextWrapping == TextWrapping.Wrap && summary.TextTrimming == TextTrimming.None,
                        "home announcement truncates the approved summary");
                    Assert(button.Focusable && button.IsTabStop && button.Command?.CanExecute(null) == true
                        && AutomationProperties.GetName(button) == "阅读公告全文",
                        "announcement action is not keyboard or screen-reader accessible");
                    var markdown = LauncherInformation.ReadDocument("ANNOUNCEMENT.md");
                    Invoke(button);
                    Assert(interaction.Title == Title && interaction.Text == markdown && interaction.IsMarkdown,
                        "announcement did not open its embedded body in the reading dialog");
                    Assert(model.IsHomeSelected && bridge.StartCount == 0 && model.About.LastCheckText == "尚未检查"
                        && model.Notices.Visible.Count == 0, "reading an announcement triggered an unrelated operation");

                    RunTask(model.StartCommand.ExecuteAsync(null));
                    Invoke(button);
                    Assert(model.IsManagedGameRunning && bridge.StartCount == 1 && model.IsHomeSelected,
                        "reading an announcement interrupted the running session");
                    interaction.MarkdownError = new IOException("fixture document failure");
                    Invoke(button);
                    Assert(model.IsNoticeError && model.NoticeText == "打开公告失败，请重试" && model.IsManagedGameRunning,
                        "announcement failure was not reported or changed the running session");
                    interaction.MarkdownError = null;
                    Invoke(button);
                    Assert(interaction.Text == markdown && model.IsManagedGameRunning, "announcement retry failed");

                    LauncherLayoutTests.Render(root, window, null, "announcement-small-work-area", 928, 460);
                    button.BringIntoView();
                    Pump();
                    var scroll = (ScrollViewer)home.FindName("HomeScroll");
                    var position = button.TranslatePoint(new Point(), scroll);
                    Assert(position.Y >= -1 && position.Y + button.ActualHeight <= scroll.ViewportHeight + 1,
                        "announcement action is not reachable in a small work area");
                    TestDialog(markdown, output);
                }
                finally
                {
                    window.DataContext = null;
                    window.Close();
                }
            }
        }

        private static void TestDialog(string markdown, string output)
        {
            var dialog = new TextDialog(Title, markdown, true);
            try
            {
                var root = (FrameworkElement)dialog.Content;
                var viewer = (FlowDocumentScrollViewer)dialog.FindName("MarkdownViewer");
                var paragraphs = viewer.Document.Blocks.OfType<Paragraph>().ToArray();
                var text = string.Join("\n\n", paragraphs.Select(paragraph =>
                    new TextRange(paragraph.ContentStart, paragraph.ContentEnd).Text.TrimEnd('\r', '\n')));
                Assert(dialog.Title == Title && paragraphs.Length == 11 && text == markdown.Replace("\r\n", "\n").Trim(),
                    "announcement rendering changed its eleven paragraphs or repeated its title");
                Assert(text.StartsWith("大家好，我是流星锤，大家也可以叫我锤锤。")
                    && text.Contains("这两年，AI 能力的进步") && text.Contains("再推进 PVE 内容。")
                    && text.Contains("我也会把这份期待落到具体的开发里，继续把项目做好。") && text.EndsWith("——流星锤"),
                    "announcement lost its introduction, development plans, closing paragraph, or signature");
                foreach (var scale in new[] { 1.0, 1.5, 2.0 })
                foreach (var size in new[] { new Size(644, 441), new Size(464, 281) })
                {
                    var name = "announcement-dialog-" + (size.Width == 644 ? "" : "compact-") + (int)(scale * 100);
                    LauncherLayoutTests.Render(root, null, output, name, size.Width, size.Height, scale);
                    LauncherLayoutTests.AssertWithin((Button)dialog.FindName("CloseButton"), root, size.Width, size.Height);
                    var scroll = viewer.Template.FindName("PART_ContentHost", viewer) as ScrollViewer;
                    Assert(scroll != null && scroll.ScrollableHeight > 0 && scroll.ScrollableWidth == 0,
                        "announcement body is not vertically scrollable or wraps outside its viewport");
                }
                viewer.Selection.Select(viewer.Document.ContentStart, viewer.Document.ContentEnd);
                Assert(viewer.IsSelectionEnabled && viewer.Selection.Text.Contains("继续把项目做好。")
                    && viewer.Selection.Text.Contains("——流星锤"),
                    "announcement body cannot be selected and copied through its signature");
                Assert(((Button)dialog.FindName("CloseButton")).IsCancel, "announcement dialog lost its Escape close action");
            }
            finally { dialog.Close(); }
        }

        private static void Invoke(Button button)
        {
            ((IInvokeProvider)new ButtonAutomationPeer(button).GetPattern(PatternInterface.Invoke)).Invoke();
            Pump();
        }
    }
}
