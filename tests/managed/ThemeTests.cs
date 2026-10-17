using System;
using System.Windows;
using T7R.Desktop;

namespace T7R.ManagedHarness
{
    internal static class ThemeTests
    {
        internal static void Run(string renderDirectory = null)
        {
            var app = new App(false);
            app.InitializeComponent();
            app.ShutdownMode = ShutdownMode.OnExplicitShutdown;
            Program.RunGroup(nameof(ThemePreferenceTests), () => ThemePreferenceTests.Run(app, renderDirectory));
            MarkdownTests.Run();
            Program.RunGroup("Layout.Light", () => LauncherLayoutTests.Run(renderDirectory));
            var light = app.Resources["WindowBackgroundBrush"];
            foreach (var name in new[] { "Dark", "HighContrast" })
            {
                var theme = new ResourceDictionary
                {
                    Source = new Uri("/T7-Rekindle;component/Resources/Theme." + name + ".xaml", UriKind.Relative)
                };
                app.Resources.MergedDictionaries.Add(theme);
                if (!ReferenceEquals(app.Resources["WindowBackgroundBrush"], theme["WindowBackgroundBrush"]))
                    throw new InvalidOperationException(name + " theme was shadowed by default resources");
                Program.RunGroup("Layout." + name,
                    () => LauncherLayoutTests.Run(renderDirectory == null ? null : System.IO.Path.Combine(renderDirectory, name)));
                app.Resources.MergedDictionaries.Remove(theme);
                if (!ReferenceEquals(app.Resources["WindowBackgroundBrush"], light))
                    throw new InvalidOperationException("light theme was not restored");
            }
            app.Shutdown();
        }

    }
}
