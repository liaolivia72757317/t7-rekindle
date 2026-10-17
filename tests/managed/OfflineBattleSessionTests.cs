using System;
using System.IO;
using System.Threading;
using T7R.Core;
using T7R.Desktop.Services;

namespace T7R.ManagedHarness
{
    internal static class OfflineBattleSessionTests
    {
        internal static void Run(string nativeLibrary, string rustLibrary)
        {
            var root = Path.Combine(Path.GetTempPath(), "t7r-offline-battle-" + Guid.NewGuid().ToString("N"));
            var package = Path.GetDirectoryName(Path.GetFullPath(nativeLibrary));
            var client = Path.Combine(root, "client");
            var bin = Path.Combine(client, "bin");
            Directory.CreateDirectory(bin);
            Directory.CreateDirectory(Path.Combine(client, "vfs"));
            try
            {
                var localRust = Path.Combine(package, "t7r_battle_ffi.dll");
                if (!File.Exists(localRust)) File.Copy(rustLibrary, localRust);
                File.Copy(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "fixtures", "client-cache-v1.vfs"),
                    Path.Combine(client, "vfs", "data1.vfs"));
                File.WriteAllText(Path.Combine(client, "update.lst"), "1.0.0\n");
                var bridge = new NativeBridgeService(package, Path.Combine(root, "default-local"));
                try
                {
                    for (var attempt = 0; attempt < 2; ++attempt)
                    {
                        var failed = bridge.StartAsync(bin, "测试玩家", false, CancellationToken.None).GetAwaiter().GetResult();
                        if (failed.Status != OperationStatus.Failed || bridge.GetRuntimeStatus().HasSession)
                            throw new InvalidOperationException("Default Rust startup did not roll back the synthetic client.");
                        if (!Directory.Exists(Path.Combine(root, "default-local", "battle-cache")))
                            throw new InvalidOperationException("Default startup bypassed Rust resource preparation.");
                    }
                    var session = new OfflineBattleSession(bridge, package, Path.Combine(root, "local"));
                    for (var round = 0; round < 2; ++round)
                    {
                        var result = session.StartAsync(bin, "fixture", "测试玩家", false, CancellationToken.None)
                            .GetAwaiter().GetResult();
                        if (result.Status != OperationStatus.Failed)
                            throw new InvalidOperationException("Synthetic client was unexpectedly launched.");
                        var snapshot = bridge.GetSnapshot();
                        if (snapshot.State != SessionState.Idle || snapshot.LoginPort != 0
                            || snapshot.LogicPort != 0 || snapshot.InstancePort != 0 || !snapshot.CleanupComplete)
                            throw new InvalidOperationException("Offline battle startup failure did not roll back.");
                    }
                    if (File.Exists(Path.Combine(root, "local", "player-profile.json")))
                        throw new InvalidOperationException("Read-only profile startup wrote a player profile.");
                    session.StopAsync().GetAwaiter().GetResult();
                }
                finally { bridge.Dispose(); }
            }
            finally
            {
                var resolved = Path.GetFullPath(root);
                if (Path.GetDirectoryName(resolved) != Path.GetTempPath().TrimEnd(Path.DirectorySeparatorChar))
                    throw new InvalidOperationException("Unexpected offline battle fixture cleanup path.");
                Directory.Delete(resolved, true);
            }
        }
    }
}
