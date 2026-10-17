using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Threading;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.Input;
using T7R.Core;
using T7R.Desktop.Services;
using T7R.Desktop.ViewModels;

namespace T7R.ManagedHarness
{
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            if (args.Length == 1 && args[0] == "--match-map-tests")
            {
                var directory = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "t7r-match-maps-" + Guid.NewGuid().ToString("N"));
                try
                {
                    MatchMapPreferencesTests.Run(directory);
                    GameSettingsFileTests.Run(directory);
                    SettingsMergeTests.Run(directory);
                    Console.WriteLine("team map selection, persistence, restore and settings preservation checks passed");
                    return 0;
                }
                catch (Exception error) { Console.Error.WriteLine(error); return 1; }
                finally { if (System.IO.Directory.Exists(directory)) System.IO.Directory.Delete(directory, true); }
            }
            if (args.Length == 1 && args[0] == "--title-publication-tests")
            {
                try
                {
                    NativeTitlePublicationTests.Run();
                    Console.WriteLine("native title result retention, acknowledgement, retry and epoch checks passed");
                    return 0;
                }
                catch (Exception error) { Console.Error.WriteLine(error); return 1; }
            }
            if (args.Length >= 1 && args[0] == "--theme-tests")
            {
                var app = new T7R.Desktop.App(false);
                app.InitializeComponent();
                app.ShutdownMode = System.Windows.ShutdownMode.OnExplicitShutdown;
                try
                {
                    ThemePreferenceTests.Run(app, args.Length == 2 ? args[1] : null);
                    Console.WriteLine("theme preference, persistence, live switching and high contrast checks passed");
                    return 0;
                }
                catch (Exception error) { Console.Error.WriteLine(error); return 1; }
                finally { app.Shutdown(); }
            }
            if (args.Length == 3 && args[0] == "--offline-battle-contract-tests")
            {
                try
                {
                    OfflineBattleSessionTests.Run(args[1], args[2]);
                    Console.WriteLine("offline Rust battle startup rollback checks passed");
                    return 0;
                }
                catch (Exception error) { Console.Error.WriteLine(error); return 1; }
            }
            if (args.Length == 2 && args[0] == "--native-contract-tests")
            {
                try
                {
                    NativeBridgeServiceTests.RunNativeContract(args[1]);
                    Console.WriteLine("packaged native bindings and old DLL rejection checks passed");
                    return 0;
                }
                catch (Exception error) { Console.Error.WriteLine(error); return 1; }
            }
            if (args.Length == 1 && args[0] == "--preview-key-check")
            {
                try
                {
                    new PreviewGrantVerifier(PreviewAccessService.BuiltInPublicKey);
                    Console.WriteLine("compiled preview authorization public key is valid");
                    return 0;
                }
                catch (Exception) { Console.Error.WriteLine("A valid T7R_PREVIEW_ACCESS_PUBLIC_KEY is required before publishing. Rebuild after configuring it."); return 1; }
            }
            if (args.Length >= 1 && args[0] == "--preview-access-tests")
            {
                var directory = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "T7R-preview-tests-" + Guid.NewGuid().ToString("N"));
                try
                {
                    PreviewAccessTests.Run(directory);
                    PreviewAccessWorkflowTests.Run(System.IO.Path.Combine(directory, "workflow"));
                    PreviewSigningScriptTests.Run(System.IO.Path.Combine(directory, "signing"));
                    PreviewAccessUiTests.RunStandalone(directory, args.Length == 2 ? args[1] : null);
                    Console.WriteLine("preview access protocol, persistence, migration and UI checks passed");
                    return 0;
                }
                catch (Exception error) { Console.Error.WriteLine(error); return 1; }
                finally { if (System.IO.Directory.Exists(directory)) System.IO.Directory.Delete(directory, true); }
            }
            if (args.Length >= 1 && args[0] == "--announcement-tests")
            {
                var directory = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "T7R-announcement-tests-" + Guid.NewGuid().ToString("N"));
                var app = new T7R.Desktop.App(false);
                app.InitializeComponent();
                app.ShutdownMode = System.Windows.ShutdownMode.OnExplicitShutdown;
                var previousContext = SynchronizationContext.Current;
                SynchronizationContext.SetSynchronizationContext(new System.Windows.Threading.DispatcherSynchronizationContext());
                try
                {
                    RemoteAnnouncementTests.Run(directory);
                    var output = args.Length == 2 ? args[1] : null;
                    AnnouncementTests.Run(directory, output);
                    foreach (var theme in new[] { "Dark", "HighContrast" })
                    {
                        var resources = new System.Windows.ResourceDictionary
                        {
                            Source = new Uri("/T7-Rekindle;component/Resources/Theme." + theme + ".xaml", UriKind.Relative)
                        };
                        app.Resources.MergedDictionaries.Add(resources);
                        try { AnnouncementTests.Run(System.IO.Path.Combine(directory, theme), output == null ? null : System.IO.Path.Combine(output, theme)); }
                        finally { app.Resources.MergedDictionaries.Remove(resources); }
                    }
                    Console.WriteLine("announcement API, refresh, fallback and UI checks passed");
                    return 0;
                }
                catch (Exception error) { Console.Error.WriteLine(error); return 1; }
                finally
                {
                    NLog.LogManager.Shutdown();
                    app.Shutdown();
                    SynchronizationContext.SetSynchronizationContext(previousContext);
                    if (System.IO.Directory.Exists(directory)) System.IO.Directory.Delete(directory, true);
                }
            }
            if (args.Length >= 1 && args[0] == "--log-tests")
            {
                var directory = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "T7R-log-tests-" + Guid.NewGuid().ToString("N"));
                var app = new T7R.Desktop.App(false);
                app.InitializeComponent();
                app.ShutdownMode = System.Windows.ShutdownMode.OnExplicitShutdown;
                var previousContext = SynchronizationContext.Current;
                SynchronizationContext.SetSynchronizationContext(new System.Windows.Threading.DispatcherSynchronizationContext());
                try
                {
                    var output = args.Length == 2 ? args[1] : null;
                    DiagnosticLogTests.Run(directory, output);
                    LogLevelTests.Run(directory, output);
                    SettingsMergeTests.Run(directory);
                    foreach (var theme in new[] { "Dark", "HighContrast" })
                    {
                        var resources = new System.Windows.ResourceDictionary
                        {
                            Source = new Uri("/T7-Rekindle;component/Resources/Theme." + theme + ".xaml", UriKind.Relative)
                        };
                        app.Resources.MergedDictionaries.Add(resources);
                        try { LogLevelTests.Run(System.IO.Path.Combine(directory, theme), output == null ? null : System.IO.Path.Combine(output, theme)); }
                        finally { app.Resources.MergedDictionaries.Remove(resources); }
                    }
                    Console.WriteLine("log level, persistence, forwarding and UI checks passed");
                    return 0;
                }
                catch (Exception error) { Console.Error.WriteLine(error); return 1; }
                finally
                {
                    NLog.LogManager.Shutdown();
                    app.Shutdown();
                    SynchronizationContext.SetSynchronizationContext(previousContext);
                    if (System.IO.Directory.Exists(directory)) System.IO.Directory.Delete(directory, true);
                }
            }
            if (args.Length == 2 && args[0] == "--login-ui-native")
            {
                try { LoginNativeUiTests.Run(args[1]); return 0; }
                catch (Exception error) { Console.Error.WriteLine(error); return 1; }
            }
            if (args.Length >= 1 && (args[0] == "--account-tests" || args[0] == "--title-tests"))
            {
                try
                {
                    if (args[0] == "--title-tests") AccountTests.RunTitles(); else AccountTests.Run();
                    if (args.Length == 2)
                    {
                        var app = new T7R.Desktop.App(false);
                        app.InitializeComponent();
                        app.ShutdownMode = System.Windows.ShutdownMode.OnExplicitShutdown;
                        if (args[0] == "--title-tests") AccountTests.RenderTitles(args[1]); else AccountTests.Render(args[1]);
                        foreach (var theme in new[] { "Dark", "HighContrast" })
                        {
                            var resources = new System.Windows.ResourceDictionary
                            {
                                Source = new Uri("/T7-Rekindle;component/Resources/Theme." + theme + ".xaml", UriKind.Relative)
                            };
                            app.Resources.MergedDictionaries.Add(resources);
                            try {
                                if (args[0] == "--title-tests") AccountTests.RenderTitles(System.IO.Path.Combine(args[1], theme));
                                else AccountTests.Render(System.IO.Path.Combine(args[1], theme));
                            }
                            finally { app.Resources.MergedDictionaries.Remove(resources); }
                        }
                        app.Shutdown();
                    }
                    Console.WriteLine("account checks passed");
                    return 0;
                }
                catch (Exception error) { Console.Error.WriteLine(error); return 1; }
            }
            if (args.Length > 0 && args[0] == "--rollback-tests")
            {
                try
                {
                    Console.WriteLine("Checking rollback services and state...");
                    ReleaseHistoryTests.Run();
                    Console.WriteLine("Checking rollback layout...");
                    var app = new T7R.Desktop.App(false);
                    app.InitializeComponent();
                    app.ShutdownMode = System.Windows.ShutdownMode.OnExplicitShutdown;
                    ReleaseHistoryTests.Render(args.Length == 2 ? args[1] : null);
                    app.Shutdown();
                    Console.WriteLine("rollback checks passed");
                    return 0;
                }
                catch (Exception error)
                {
                    Console.Error.WriteLine(error.GetType().FullName + ": " + error.Message);
                    Console.Error.WriteLine(error.StackTrace);
                    return 1;
                }
            }
            if (args.Length == 2 && args[0] == "--native-ui")
            {
                NativeWindowUiTests.Run(args[1]);
                return 0;
            }
            if (!SettingsSchema.IsValid(new UserSettings())) return 1;
            if (SettingsSchema.IsValid(new UserSettings { ClientDirectory = "C:\\client\r\n" })) return 2;
            if (SettingsSchema.IsValid(new UserSettings { WindowWidth = 479 })) return 3;
            if (SettingsSchema.IsValid(new UserSettings { WindowHeight = 4097 })) return 4;
            if (SettingsSchema.IsValid(new UserSettings { SchemaVersion = 2 })) return 5;
            if (NativeBridgeContract.IsUtf8PathAcceptable("C:\\client\r")) return 6;
            if (NativeBridgeContract.IsUtf8PathAcceptable(new string('界', 20000))) return 7;
            if (NativeBridgeContract.IsUtf8PathAcceptable("relative\\client")) return 8;
            if (NativeBridgeContract.IsUtf8PathAcceptable("C:\\client\uD800")) return 9;
            if (SettingsSchema.IsValid(new UserSettings { WindowWidth = double.NaN })) return 10;
            if (SettingsSchema.IsValid(new UserSettings { ClientDirectory = "relative\\client" })) return 11;
            if (NativeBridgeContract.IsUtf8PathAcceptable("C:drive-relative")) return 12;
            if (SettingsSchema.IsValid(new UserSettings { ClientDirectory = "C:drive-relative" })) return 13;
            if (NativeBridgeContract.IsUtf8PathAcceptable("\\root-relative")) return 14;
            if (SettingsSchema.IsValid(new UserSettings { ClientDirectory = "\\root-relative" })) return 15;
            if (NativeBridgeContract.IsUtf8PathAcceptable("\\\\")) return 16;
            if (SettingsSchema.IsValid(new UserSettings { ClientDirectory = "\\\\" })) return 17;
            if (args.Length == 2 && args[0] == "--render-v11")
            {
                try { ThemeTests.Run(args[1]); return 0; }
                catch (Exception error) { Console.Error.WriteLine(error); return 1; }
            }
            if (args.Length == 1 && args[0] == "--launcher-tests")
            {
                try { RunGroup(nameof(LauncherTests), LauncherTests.Run); return 0; }
                catch (Exception error) { Console.Error.WriteLine(error); return 1; }
            }
            NativeAbiLayout.Validate();
            RunGroup(nameof(HarnessProgressTests), HarnessProgressTests.Run);
            RunGroup(nameof(AccountTests), AccountTests.Run);
            RunGroup(nameof(NativeBridgeServiceTests), NativeBridgeServiceTests.Run);
            RunGroup(nameof(GitHubReleaseUpdateTests), GitHubReleaseUpdateTests.Run);
            RunGroup(nameof(UpdateFeedTests), UpdateFeedTests.Run);
            RunGroup(nameof(UpdateChannelTests), UpdateChannelTests.Run);
            RunGroup(nameof(ReleaseNotesTests), ReleaseNotesTests.Run);
            RunGroup(nameof(ReleaseHistoryTests), ReleaseHistoryTests.Run);
            RunGroup(nameof(UpdateDownloadTests), UpdateDownloadTests.Run);
            RunGroup(nameof(UpdateDownloadViewModelTests), UpdateDownloadViewModelTests.Run);
            RunGroup(nameof(LauncherTests), LauncherTests.Run);
            RunGroup(nameof(ThemeTests), () => ThemeTests.Run(args.Length == 2 && args[0] == "--render-ui" ? args[1] : null));
            Console.WriteLine("managed contract checks passed");
            return 0;
        }

        internal static void RunGroup(string name, Action run)
        {
            Console.WriteLine("[RUN] " + name);
            var elapsed = Stopwatch.StartNew();
            try { run(); }
            catch
            {
                Console.WriteLine("[FAIL] {0} ({1} ms)", name, elapsed.ElapsedMilliseconds);
                throw;
            }
            Console.WriteLine("[PASS] {0} ({1} ms)", name, elapsed.ElapsedMilliseconds);
        }

    }
}
