using System;
using System.Collections.Generic;
using System.IO;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using T7R.Core;
using T7R.Desktop;
using T7R.Desktop.Services;
using T7R.Desktop.ViewModels;
using T7R.Desktop.Views;
using static T7R.ManagedHarness.LauncherTests;

namespace T7R.ManagedHarness
{
    internal static class AboutPageLayoutTests
    {
        internal static void Run(string directory, string output)
        {
            using (var model = new MainWindowViewModel(new FakeLauncherBridge(),
                new SettingsService(Path.Combine(directory, "about-layout")),
                new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "玩家" },
                null,
                new FakeClientEnvironment(path => Task.FromResult(ValidDirectory(path))),
                new FakeDesktopInteraction()))
            {
                RunTask(model.ValidationTask);
                var window = new MainWindow { DataContext = model };
                try
                {
                    model.IsAboutSelected = true;
                    var root = (FrameworkElement)window.Content;
                    var page = (AboutPage)window.FindName("ProjectPage");
                    var content = (StackPanel)page.FindName("AboutContent");
                    var scroll = (ScrollViewer)page.FindName("AboutScroll");
                    var links = (UniformGrid)page.FindName("ProjectLinksGrid");
                    var cards = new[]
                    {
                        (Button)page.FindName("ClientDownloadButton"), (Button)page.FindName("EnvironmentInfoButton"),
                        (Button)page.FindName("LogDirectoryButton"), (Button)page.FindName("IssuesButton"),
                        (Button)page.FindName("RepositoryButton"),
                        (Button)page.FindName("LicensesButton"), (Button)page.FindName("ThanksButton")
                    };
                    Assert(content != null && scroll != null && links != null, "about page is missing its grouped layout");
                    foreach (var card in cards) Assert(card != null, "about page is missing an action card");
                    foreach (var scale in new[] { 1.0, 1.5, 2.0 })
                    foreach (var size in new[] { new Size(1200, 900), new Size(960, 720), new Size(800, 650), new Size(1200, 900) })
                    {
                        scroll.ScrollToTop();
                        var name = "about-layout-" + size.Width + "-" + (int)(scale * 100);
                        LauncherLayoutTests.Render(root, window, output, name, size.Width, size.Height, scale);
                        AssertDownloadCardStyle(cards[0], cards[1]);
                        var compact = size.Width < 1200;
                        Assert(links.Columns == (compact ? 1 : 3), "about links did not adapt to the available width");
                        for (var i = 5; i < cards.Length; i++)
                            Assert(Math.Abs(cards[i].ActualHeight - cards[4].ActualHeight) < 1
                                && Math.Abs(cards[i].ActualWidth - cards[4].ActualWidth) < 1,
                                "project link cards have inconsistent sizes");
                        Assert(Grid.GetColumnSpan(cards[0]) == (compact ? 1 : 3)
                            && Grid.GetRow(cards[0]) == 0 && Grid.GetRowSpan(cards[0]) == 1
                            && Grid.GetColumn(cards[1]) == 0 && Grid.GetRow(cards[1]) == 1
                            && Grid.GetColumn(cards[2]) == (compact ? 0 : 1)
                            && Grid.GetRow(cards[2]) == (compact ? 2 : 1)
                            && Grid.GetColumn(cards[3]) == (compact ? 0 : 2)
                            && Grid.GetRow(cards[3]) == (compact ? 3 : 1), "about support cards lost their responsive hierarchy");
                        for (var i = 0; i < cards.Length; i++)
                        {
                            var card = cards[i];
                            Assert(card.IsTabStop && card.Focusable && card.TabIndex == i + 9
                                && card.Command?.CanExecute(null) == true, "about card lost its command or keyboard order");
                            LauncherLayoutTests.AssertWithin(card, content, content.ActualWidth, content.ActualHeight);
                            foreach (var text in TextBlocks(card))
                            {
                                Assert(text.FontSize >= 14 && text.TextTrimming == TextTrimming.None, "about text was shrunk or truncated");
                                LauncherLayoutTests.AssertWithin(text, card, card.ActualWidth, card.ActualHeight);
                            }
                            var bounds = new Rect(card.TranslatePoint(new Point(), content), card.RenderSize);
                            for (var j = 0; j < i; j++)
                                Assert(!bounds.IntersectsWith(new Rect(cards[j].TranslatePoint(new Point(), content), cards[j].RenderSize)),
                                    "about cards overlap");
                        }
                        if (!compact)
                        {
                            Assert(scroll.ScrollableHeight < 1, "the default about page requires unnecessary scrolling");
                            Assert(Math.Abs(cards[0].ActualWidth - cards[1].ActualWidth - cards[2].ActualWidth - cards[3].ActualWidth - 24) < 2,
                                "download and support card edges are not aligned");
                            foreach (var card in cards) LauncherLayoutTests.AssertWithin(card, root, size.Width, size.Height);
                        }
                        else
                        {
                            cards[6].BringIntoView();
                            Pump();
                            LauncherLayoutTests.Render(root, window, output, name + "-bottom", size.Width, size.Height, scale);
                            LauncherLayoutTests.AssertWithin(cards[6], root, size.Width, size.Height);
                        }
                    }
                }
                finally
                {
                    window.DataContext = null;
                    window.Close();
                }
            }
        }

        private static void AssertDownloadCardStyle(Button download, Button reference)
        {
            Assert(download.Style == reference.Style
                && download.ReadLocalValue(Control.BackgroundProperty) == DependencyProperty.UnsetValue
                && download.ReadLocalValue(Control.BorderBrushProperty) == DependencyProperty.UnsetValue,
                "client download overrides the shared interaction colors");
            Assert(Equals(download.Background, reference.Background) && Equals(download.BorderBrush, reference.BorderBrush),
                "client download appears hovered before interaction");
            try
            {
                download.IsEnabled = false;
                Assert(Equals(download.Background, download.FindResource("DisabledBrush"))
                    && Equals(download.BorderBrush, download.FindResource("DividerBrush")),
                    "client download does not follow the shared disabled style");
            }
            finally { download.ClearValue(UIElement.IsEnabledProperty); }
            Assert(Equals(download.Background, reference.Background) && Equals(download.BorderBrush, reference.BorderBrush),
                "client download did not restore its default style");
        }

        private static IEnumerable<TextBlock> TextBlocks(DependencyObject root)
        {
            if (root is TextBlock text) yield return text;
            for (var i = 0; i < VisualTreeHelper.GetChildrenCount(root); i++)
                foreach (var child in TextBlocks(VisualTreeHelper.GetChild(root, i))) yield return child;
        }
    }
}
