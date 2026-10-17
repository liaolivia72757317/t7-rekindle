using System;
using System.IO;
using System.Runtime.InteropServices;
using T7R.Battle;
using T7R.Core;

namespace T7R.BattleHarness
{
    internal static class Program
    {
        private static int Main(string[] args)
        {
            try
            {
                if (args.Length == 1 && args[0] == "--bot-selection-tests")
                {
                    BotSelectionTests.Run();
                    return 0;
                }
                if (args.Length == 2 && args[0] == "--gateway-backpressure")
                {
                    GatewayBackpressureTests.Run(Path.GetFullPath(args[1]));
                    return 0;
                }
                if (args.Length == 2 && args[0] == "--host-stop-tests")
                {
                    NativeBattleHostTests.Run(Path.GetFullPath(args[1])).GetAwaiter().GetResult();
                    return 0;
                }
                Check(Marshal.SizeOf<NativeBattleHostCreateArgs>() == 80, "create ABI");
                Check(Marshal.SizeOf<NativeBattleHostSnapshot>() == 104, "snapshot ABI");
                Check(Marshal.OffsetOf<NativeBattleHostCreateArgs>("WritableRoot").ToInt32() == 56, "path ABI");
                Check(Marshal.OffsetOf<NativeBattleHostSnapshot>("Phase").ToInt32() == 40, "phase ABI");
                ActorMapTests.Run();
                AdmissionTests.Run();
                SemanticTests.Run();
                FrameTests.Run().GetAwaiter().GetResult();
                RelayTests.Run().GetAwaiter().GetResult();
                PlatformBackendTests.Run().GetAwaiter().GetResult();
                BattleRearmCoordinatorTests.Run().GetAwaiter().GetResult();
                LocalProfileTests.Run().GetAwaiter().GetResult();
                BotSelectionTests.Run();
                if (args.Length == 2 && args[0] == "--rust-gateway")
                    ClientGatewayTests.Run(Path.GetFullPath(args[1])).GetAwaiter().GetResult();
                else if (args.Length == 4 && args[0] == "--host")
                    NativeHost(Path.GetFullPath(args[1]), Path.GetFullPath(args[2]), Path.GetFullPath(args[3]));
                else if (args.Length == 1) NativeHost(Path.GetFullPath(args[0]));
                else if (args.Length != 0) throw new ArgumentException("Expected optional runtime fixture path.");
                Console.WriteLine("battle transport, three-channel relay, ABI and requested host checks passed");
                return 0;
            }
            catch (Exception error) { Console.Error.WriteLine(error); return 1; }
        }

        private static void NativeHost(string root, string resources = null, string state = null)
        {
            Throws<ArgumentException>(() => new NativeBattleHost("relative", root, root));
            resources = resources ?? Path.Combine(root, "synthetic-client", "bin");
            state = state ?? Path.Combine(root, "managed-battle-state");
            using (var host = new NativeBattleHost(root, resources, state))
            {
                Check(host.GetSnapshot().State == BattleHostState.Stopped, "create starts no runtime");
                host.Start();
                var ready = host.GetSnapshot();
                Check(ready.State == BattleHostState.Running && ready.LoginPort != 0 && ready.InstancePort != 0, "host ready");
                RelayTests.NativeAuth(ready).GetAwaiter().GetResult();
                Throws<InvalidOperationException>(host.Start);
                host.Stop();
                Check(host.GetSnapshot().InstancePort == 0, "stop closes listeners");
                host.Start();
                host.Stop();
                host.Dispose();
                host.Dispose();
                Throws<ObjectDisposedException>(() => host.GetSnapshot());
            }
            using (var broken = new NativeBattleHost(root, Path.Combine(root, "missing", "bin"), state))
            {
                Throws<InvalidOperationException>(broken.Start);
                Check(broken.GetSnapshot().State == BattleHostState.Failed && broken.GetError().Length > 0, "resource failure surfaced");
            }
        }

        internal static void Check(bool value, string message)
        {
            if (!value) throw new InvalidOperationException("Check failed: " + message);
        }
        internal static void Throws<T>(Action action) where T : Exception
        {
            try { action(); } catch (T) { return; }
            throw new InvalidOperationException("Expected " + typeof(T).Name);
        }
    }
}
