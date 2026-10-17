using System;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using T7R.Battle;

namespace T7R.BattleHarness
{
    internal static class LocalResourceTests
    {
        internal static async Task Run(string library)
        {
            Program.Throws<ArgumentException>(() => new LocalResourceOptions("relative", "relative", 1, 1, "fixture"));
            var root = Path.Combine(Path.GetTempPath(), "t7r-resource-test-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            try
            {
                var client = Path.Combine(root, "client");
                var bin = Directory.CreateDirectory(Path.Combine(client, "bin")).FullName;
                var vfs = Directory.CreateDirectory(Path.Combine(client, "vfs")).FullName;
                var cache = Path.Combine(root, "cache");
                var archive = Path.Combine(vfs, "data1.vfs");
                var original = File.ReadAllBytes(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "fixtures", "client-cache-v1.vfs"));
                File.WriteAllBytes(archive, original);
                var options = new LocalResourceOptions(bin, cache, 1, 1, "fixture");
                using (var first = await ClientGateway.CreateLocalAsync(library, options, LocalProfileTests.CreateBackend("玩家")))
                {
                    Program.Check(!first.WasResourceCacheHit && first.GetSnapshot().Epoch == 0, "cache built in-process without listeners");
                    await first.StartAsync(1);
                    Program.Check(first.GetSnapshot().IsRunning, "cached resource gateway starts");
                    await first.StopAsync();
                }
                await ClientGateway.ValidateLocalResourcesAsync(library, options);
                using (var second = await ClientGateway.CreateLocalAsync(library, options, LocalProfileTests.CreateBackend("玩家")))
                    Program.Check(second.WasResourceCacheHit, "unchanged resources reuse cache");
                if (string.Equals(Path.GetFileName(library), "t7r_battle_ffi.dll", StringComparison.OrdinalIgnoreCase))
                {
                    var profile = Path.Combine(root, "offline-profile.json");
                    using (var profiled = await ClientGateway.CreateLocalProfiledAsync(library, options, "测试玩家", profile))
                    {
                        Program.Check(profiled.WasResourceCacheHit, "profiled gateway reuses the validated Rust bundle");
                        await profiled.StartAsync(1);
                        using (var logic = ClientGatewayTests.Connect(profiled.GetSnapshot().LogicPort))
                            ClientGatewayTests.AssertBody(logic, "login");
                        await profiled.StopAsync();
                        Program.Check(!File.Exists(profile), "default local profile is read without writing");
                    }
                }
                var stamp = File.GetLastWriteTimeUtc(archive);
                var changed = (byte[])original.Clone();
                var text = Encoding.ASCII.GetString(changed);
                var index = text.IndexOf("Fixture00", StringComparison.Ordinal);
                Program.Check(index >= 0, "synthetic source contains expected name");
                Array.Copy(Encoding.ASCII.GetBytes("Changed00"), 0, changed, index, 9);
                File.WriteAllBytes(archive, changed);
                File.SetLastWriteTimeUtc(archive, stamp);
                using (var rebuilt = await ClientGateway.CreateLocalAsync(library, options, LocalProfileTests.CreateBackend("玩家")))
                    Program.Check(!rebuilt.WasResourceCacheHit, "same-size same-timestamp source changes invalidate cache");
                File.WriteAllText(Path.Combine(vfs, "update.vfs"), "overlay");
                try
                {
                    await ClientGateway.ValidateLocalResourcesAsync(library, options);
                    throw new Exception("Expected overlay rejection.");
                }
                catch (InvalidOperationException error)
                { Program.Check(error.Message.Contains("unsupported_overlay"), "resource error propagates across ABI"); }
                Program.Check(Convert.ToBase64String(File.ReadAllBytes(archive)) == Convert.ToBase64String(changed), "native resource load is read-only");
            }
            finally
            {
                var resolved = Path.GetFullPath(root);
                if (Path.GetDirectoryName(resolved) != Path.GetTempPath().TrimEnd(Path.DirectorySeparatorChar))
                    throw new InvalidOperationException("Unexpected fixture cleanup path.");
                Directory.Delete(resolved, true);
            }
            Console.WriteLine("Rust local resource cache C# -> C ABI checks passed");
        }
    }
}
