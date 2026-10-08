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
                LaunchControlsLayoutTests.Run(settingsDirectory, outputDirectory);
                GraphicsSettingsTests.Render(settingsDirectory, outputDirectory);
                OutputDeviceTests.Render(settingsDirectory, outputDirectory);
                AudioSettingsTests.Render(settingsDirectory, outputDirectory);
                AboutPageLayoutTests.Run(settingsDirectory, outputDirectory);
                AnnouncementTests.Run(settingsDirectory, outputDirectory);
                LauncherUpdatePageTests.Run(settingsDirectory, outputDirectory);
                UpdateReminderTests.Run(settingsDirectory, outputDirectory);
                EnvironmentInformationTests.Run(outputDirectory);
                LauncherDesignTests.Run(settingsDirectory, outputDirectory);
                LauncherToastTests.Run(settingsDirectory, outputDirectory);
                UiInteractionTests.Run(settingsDirectory, outputDirectory);
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

        internal static void AssertWithin(FrameworkElement element, FrameworkElement root, double width, double height)
        {
            var position = element.TranslatePoint(new Point(), root);
            LauncherTests.Assert(element.ActualHeight > 0 && position.X >= 0 && position.Y >= 0
                && position.X + element.ActualWidth <= width + 1 && position.Y + element.ActualHeight <= height + 1,
                "element escaped the client viewport: " + element.Name);
        }

        internal static void AssertSettingsHeader(UserControl panel, string applyButtonName)
        {
            var title = FindText(panel);
            var apply = (Button)panel.FindName(applyButtonName);
            var actions = (Panel)apply.Parent;
            var titlePosition = title.TranslatePoint(new Point(), panel);
            var centerY = titlePosition.Y + title.ActualHeight / 2;
            var previousRight = titlePosition.X + title.ActualWidth;
            foreach (Button button in actions.Children)
            {
                var position = button.TranslatePoint(new Point(), panel);
                LauncherTests.Assert(Math.Abs(position.Y + button.ActualHeight / 2 - centerY) <= 1,
                    "settings action is not aligned with its section title: " + button.Content);
                LauncherTests.Assert(position.X >= previousRight + 12,
                    "settings header title and actions overlap or lack spacing: " + button.Content);
                AssertWithin(button, panel, panel.ActualWidth, panel.ActualHeight);
                previousRight = position.X + button.ActualWidth;
            }
        }

        internal static void Render(FrameworkElement root, MainWindow window, string outputDirectory,
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
                var home = (LaunchPage)window.FindName("HomePage");
                if (home.Visibility == Visibility.Visible)
                {
                    var button = (Button)home.FindName("LaunchButton");
                    AssertWithin(button, root, width, height);
                    LauncherTests.Assert(button.ActualHeight >= 42, "launch action collapsed");
                }
                var viewport = (Grid)window.FindName("ContentViewport");
                LauncherTests.Assert(viewport.ActualHeight > 0 && viewport.ActualWidth > 0, "content viewport collapsed");
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



