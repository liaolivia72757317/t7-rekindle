using System;
using System.Windows;
using T7.Rekindle.Desktop;

namespace T7.ManagedHarness
{
    internal static class ThemeTests
    {
        internal static void Run(string renderDirectory = null)
        {
            var app = new App(false);
            app.InitializeComponent();
            app.ShutdownMode = ShutdownMode.OnExplicitShutdown;
            MarkdownTests.Run();
            LauncherLayoutTests.Run(renderDirectory);
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
                LauncherLayoutTests.Run(renderDirectory == null ? null : System.IO.Path.Combine(renderDirectory, name));
                app.Resources.MergedDictionaries.Remove(theme);
                if (!ReferenceEquals(app.Resources["WindowBackgroundBrush"], light))
                    throw new InvalidOperationException("light theme was not restored");
            }
            app.Shutdown();
        }

    }
}
