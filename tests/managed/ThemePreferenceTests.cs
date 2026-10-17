using System;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;
using T7R.Core;
using T7R.Desktop;
using T7R.Desktop.Services;
using T7R.Desktop.ViewModels;
using T7R.Desktop.Views;
using static T7R.ManagedHarness.LauncherTests;

namespace T7R.ManagedHarness
{
    internal static class ThemePreferenceTests
    {
        internal static void Run(App app, string output = null)
        {
            var directory = Path.Combine(Path.GetTempPath(), "T7R-theme-tests-" + Guid.NewGuid().ToString("N"));
            var context = SynchronizationContext.Current;
            SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
            try
            {
                var service = new SettingsService(directory);
                service.Save(new UserSettings { PlayerName = "主题测试", DarkTheme = true });
                using (var model = CreateModel(service))
                {
                    app.InitializeTheme(model);
                    Assert(model.DarkTheme, "startup lost the saved theme preference");
                    AssertTheme(app, SystemParameters.HighContrast ? "HighContrast" : "Dark");
                    model.IsSettingsSelected = true;
                    model.SettingsTabIndex = 1;
                    var window = new MainWindow { DataContext = model };
                    TestHomeBanner(app, model, window, output);
                    var page = (GameSettingsPage)window.FindName("SettingsPage");
                    var root = (FrameworkElement)window.Content;
                    LauncherLayoutTests.Render(root, window, output, "theme-dark", 1200, 900);
                    var selector = (ComboBox)page.FindName("ThemeSelector");
                    Assert(selector != null && selector.Items.Cast<ComboBoxItem>().Select(item => (string)item.Content)
                        .SequenceEqual(new[] { "浅色", "深色" }), "theme choices are missing or not localized");
                    Assert((bool)selector.SelectedValue, "selector lost the saved preference");
                    var dictionaryCount = app.Resources.MergedDictionaries.Count;
                    selector.SelectedValue = false;
                    Pump();
                    Assert(!model.DarkTheme && !service.Load().DarkTheme, "light selection was not applied and saved");
                    AssertTheme(app, SystemParameters.HighContrast ? "HighContrast" : "Light");
                    LauncherLayoutTests.Render(root, window, output, "theme-light", 1200, 900);
                    Assert(ReferenceEquals(window.Background, app.Resources["WindowBackgroundBrush"]),
                        "existing window did not refresh its theme brush");
                    using (File.Open(Path.Combine(directory, "settings.json"), FileMode.Open, FileAccess.Read, FileShare.Read))
                    {
                        selector.SelectedValue = true;
                        Pump();
                        Assert(!model.DarkTheme && !(bool)selector.SelectedValue && !service.Load().DarkTheme
                            && model.PreferenceError.Length != 0, "save failure did not retain and restore the theme selection");
                        AssertTheme(app, SystemParameters.HighContrast ? "HighContrast" : "Light");
                    }
                    selector.SelectedValue = true;
                    Pump();
                    Assert(model.DarkTheme && service.Load().DarkTheme && model.PreferenceError.Length == 0,
                        "theme save could not retry");
                    AssertTheme(app, SystemParameters.HighContrast ? "HighContrast" : "Dark");
                    Assert(app.Resources.MergedDictionaries.Count == dictionaryCount, "theme dictionaries accumulated");
                    Assert(ReferenceEquals(window.Background, app.Resources["WindowBackgroundBrush"]),
                        "existing window did not refresh after switching back to dark");
                    model.MinimizeToTray = true;
                    model.SaveSettings();
                    Assert(service.Load().DarkTheme && service.Load().MinimizeToTray && service.Load().PlayerName == "主题测试",
                        "saving another preference lost the theme or unrelated settings");

                    foreach (var dark in new[] { false, true })
                    {
                        model.DarkTheme = dark;
                        Pump();
                        app.ApplyTheme(true);
                        AssertTheme(app, "HighContrast");
                        Assert(service.Load().DarkTheme == dark, "high contrast overwrote the saved preference");
                        LauncherLayoutTests.Render(root, window, output, "theme-high-contrast-" + dark, 900, 700);
                        app.ApplyTheme(false);
                        AssertTheme(app, dark ? "Dark" : "Light");
                    }
                }
                using (var restarted = CreateModel(service))
                {
                    app.InitializeTheme(restarted);
                    Assert(restarted.DarkTheme, "restart lost the theme preference");
                    restarted.DarkTheme = false;
                    Pump();
                    AssertTheme(app, SystemParameters.HighContrast ? "HighContrast" : "Light");
                }
            }
            finally
            {
                app.InitializeTheme(null);
                app.ApplyTheme(false);
                SynchronizationContext.SetSynchronizationContext(context);
                if (Directory.Exists(directory)) Directory.Delete(directory, true);
            }
        }

        private static MainWindowViewModel CreateModel(SettingsService service)
            => new MainWindowViewModel(new FakeLauncherBridge(),
                service,
                service.Load(),
                null,
                new FakeClientEnvironment(path => Task.FromResult(ValidDirectory(path))),
                new FakeDesktopInteraction());

        private static void TestHomeBanner(App app, MainWindowViewModel model, MainWindow window, string output)
        {
            var home = (LaunchPage)window.FindName("HomePage");
            var artwork = (Image)home.FindName("HeroArtwork");
            var fallback = (StackPanel)home.FindName("HeroFallback");
            var panel = (Border)home.FindName("HeroPanel");
            var source = artwork.Source as System.Windows.Media.Imaging.BitmapSource;
            Assert(source != null && source.PixelWidth == 1989 && source.PixelHeight == 790,
                "home banner did not load the original embedded artwork");
            model.IsHomeSelected = true;
            foreach (var theme in new[] { "Light", "Dark", "HighContrast", "Dark" })
            {
                model.DarkTheme = theme != "Light";
                Pump();
                app.ApplyTheme(theme == "HighContrast");
                LauncherLayoutTests.Render((FrameworkElement)window.Content, window, output, "home-banner-" + theme, 1200, 900);
                var visibility = theme == "HighContrast" ? Visibility.Collapsed : Visibility.Visible;
                Assert(artwork.Visibility == visibility
                    && fallback.Visibility == (visibility == Visibility.Visible ? Visibility.Collapsed : Visibility.Visible),
                    "home banner visibility or text fallback is incorrect for " + theme);
                Assert(Math.Abs(panel.ActualHeight - panel.ActualWidth * source.PixelHeight / source.PixelWidth) < 1,
                    "theme switch distorted the home banner");
                Assert((Visibility)app.Resources["LauncherArtworkVisibility"]
                    == (theme == "Light" ? Visibility.Visible : Visibility.Collapsed),
                    "home banner switch changed the sidebar or window artwork policy");
            }
            app.InitializeTheme(model);
            model.IsSettingsSelected = true;
        }

        private static void AssertTheme(App app, string expected)
        {
            var resources = app.Resources.MergedDictionaries;
            var active = resources.Where(item => item.Source != null && item.Source.OriginalString.Contains("Theme.")).ToArray();
            Assert(expected == "Light" ? active.Length == 0 : active.Length == 1
                && active[0].Source.OriginalString.EndsWith("Theme." + expected + ".xaml"), "unexpected active theme: " + expected);
            var brush = (SolidColorBrush)app.Resources["WindowBackgroundBrush"];
            Assert(brush.Color == (expected == "HighContrast" ? SystemColors.WindowColor
                : (Color)ColorConverter.ConvertFromString(expected == "Dark" ? "#202124" : "#F4F7F8")),
                "theme resources did not take effect");
        }
    }
}
