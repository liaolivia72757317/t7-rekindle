using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
using T7.Rekindle.Desktop.Services;

namespace T7.ManagedHarness
{
    internal static class ClientDirectorySearchTests
    {
        internal static void Run(string directory)
        {
            var container = Path.Combine(directory, "directory-search");
            var game = CreateClient(Path.Combine(container, "level-one", "level-two", "game"));
            var incomplete = Path.Combine(container, "incomplete");
            Directory.CreateDirectory(Path.Combine(incomplete, "Bin"));
            Directory.CreateDirectory(Path.Combine(incomplete, "Data"));
            Directory.CreateDirectory(Path.Combine(incomplete, "vfs"));
            File.WriteAllText(Path.Combine(incomplete, "Bin", "TieJiClient.exe"), "fixture");
            var outside = CreateClient(Path.Combine(directory, "outside-search"));

            var found = ClientDirectoryService.Locate(container, CancellationToken.None);
            LauncherTests.Assert(found.IsValid && found.Root == game,
                "ancestor search did not find the complete descendant client");
            LauncherTests.Assert(ClientDirectoryService.Locate(game, CancellationToken.None).Root == game,
                "direct root selection changed the chosen installation");
            LauncherTests.Assert(ClientDirectoryService.Locate(Path.Combine(game, "Bin"), CancellationToken.None).Root == game,
                "direct Bin selection was not normalized");
            LauncherTests.Assert(!ClientDirectoryService.Locate(incomplete, CancellationToken.None).IsValid,
                "incomplete client was accepted by directory search");
            TestLinkedDirectoryIsSkipped(container, outside, game);

            CreateClient(Path.Combine(game, "nested-installation"));
            LauncherTests.Assert(ClientDirectoryService.Locate(game, CancellationToken.None).Root == game,
                "search descended into an already valid installation");
            var nearer = CreateClient(Path.Combine(container, "z-game"));
            LauncherTests.Assert(ClientDirectoryService.Locate(container, CancellationToken.None).Root == nearer,
                "search did not prefer the closest installation");
            var first = CreateClient(Path.Combine(container, "a-game"));
            LauncherTests.Assert(ClientDirectoryService.Locate(container, CancellationToken.None).Root == first,
                "same-depth installations were not searched in directory-name order");

            var legacyParent = Path.Combine(directory, "legacy-search");
            var legacyClient = Path.Combine(legacyParent, "Client");
            Directory.CreateDirectory(legacyClient);
            Directory.CreateDirectory(Path.Combine(legacyParent, "Data"));
            Directory.CreateDirectory(Path.Combine(legacyParent, "vfs"));
            File.WriteAllText(Path.Combine(legacyClient, "TieJiClient.exe"), "fixture");
            File.WriteAllText(Path.Combine(legacyClient, "ProtocalHandler.dll"), "fixture");
            LauncherTests.Assert(ClientDirectoryService.Locate(legacyParent, CancellationToken.None).Root == legacyClient,
                "search lost support for a client program directory with resources in its parent");

            var missing = ClientDirectoryService.Locate(Path.Combine(directory, "missing-search"), CancellationToken.None);
            LauncherTests.Assert(!missing.IsValid && missing.Message.Length != 0,
                "missing directory was accepted or lost its diagnostic message");
            foreach (var invalid in new[] { "", "relative\\game", container + "\0" })
                LauncherTests.Assert(!ClientDirectoryService.Locate(invalid, CancellationToken.None).IsValid,
                    "invalid search path was accepted");
            using (var cancellation = new CancellationTokenSource())
            {
                cancellation.Cancel();
                var cancelled = false;
                try { ClientDirectoryService.Locate(container, cancellation.Token); }
                catch (OperationCanceledException) { cancelled = true; }
                LauncherTests.Assert(cancelled, "directory search ignored cancellation");
            }
        }

        private static void TestLinkedDirectoryIsSkipped(string container, string outside, string expected)
        {
            var link = Path.Combine(container, "a-link");
            using (var process = Process.Start(new ProcessStartInfo("cmd.exe", "/d /c mklink /J \"" + link + "\" \"" + outside + "\"")
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            }))
            {
                var output = process.StandardOutput.ReadToEnd() + process.StandardError.ReadToEnd();
                process.WaitForExit();
                LauncherTests.Assert(process.ExitCode == 0, "creating directory-link fixture failed: " + output);
            }
            try
            {
                var result = ClientDirectoryService.Locate(container, CancellationToken.None);
                LauncherTests.Assert(result.Root == expected && result.Message.Contains("已跳过 1"),
                    "directory search followed a child link or hid the skipped directory");
            }
            finally { Directory.Delete(link); }
        }

        internal static string CreateClient(string root)
        {
            Directory.CreateDirectory(Path.Combine(root, "Bin"));
            Directory.CreateDirectory(Path.Combine(root, "Data"));
            Directory.CreateDirectory(Path.Combine(root, "vfs"));
            File.WriteAllText(Path.Combine(root, "Bin", "TieJiClient.exe"), "fixture");
            File.WriteAllText(Path.Combine(root, "Bin", "ProtocalHandler.dll"), "fixture");
            return root;
        }
    }
}
