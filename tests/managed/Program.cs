using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.Input;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;

namespace T7.ManagedHarness
{
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
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
                ThemeTests.Run(args[1]);
                return 0;
            }
            NativeAbiLayout.Validate();
            NativeBridgeServiceTests.Run();
            GitHubReleaseUpdateTests.Run();
            UpdateFeedTests.Run();
            ReleaseNotesTests.Run();
            UpdateDownloadTests.Run();
            UpdateDownloadViewModelTests.Run();
            LauncherTests.Run();
            ThemeTests.Run(args.Length == 2 && args[0] == "--render-ui" ? args[1] : null);
            Console.WriteLine("managed contract checks passed");
            return 0;
        }

    }
}
