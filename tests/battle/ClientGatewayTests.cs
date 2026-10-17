using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Sockets;
using System.Threading;
using System.Threading.Tasks;
using T7R.Battle;
using T7R.Core;

namespace T7R.BattleHarness
{
    internal static class ClientGatewayTests
    {
        private static string Fixture(string name) => Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "fixtures", name);
        private static byte[] Resources() => File.ReadAllBytes(Fixture("bundle-v1.json"));

        internal static async Task Run(string library)
        {
            GatewayBackpressureTests.Run(library);
            await LocalResourceTests.Run(library);
            Program.Throws<InvalidOperationException>(() => new ClientGateway(library, new byte[] { 1 }, LocalProfileTests.CreateBackend("玩家")));
            var invalidRules = System.Text.Encoding.UTF8.GetString(Resources()).Replace("\"resetValue\":2", "\"resetValue\":6");
            Program.Check(invalidRules.Contains("\"resetValue\":6"), "fixture has invalid guard reset value");
            using (var rejected = new ClientGateway(library, System.Text.Encoding.UTF8.GetBytes(invalidRules), LocalProfileTests.CreateBackend("玩家")))
            {
                Program.Throws<InvalidOperationException>(() => rejected.StartAsync(1).GetAwaiter().GetResult());
                var state = rejected.GetSnapshot();
                Program.Check(!state.IsRunning && state.Epoch == 0 && state.LoginPort == 0 && state.LogicPort == 0 && state.InstancePort == 0,
                    "semantic resource failure leaves no listener or live epoch");
                Program.Check(rejected.GetError().Length != 0, "resource preparation error remains observable");
                await rejected.StopAsync();
            }
            using (var gateway = new ClientGateway(library, Resources(), LocalProfileTests.CreateBackend("测试玩家")))
            {
                Program.Check(!gateway.GetSnapshot().IsRunning, "create starts no gateway thread");
                await gateway.StartAsync(1);
                var state = gateway.GetSnapshot();
                Program.Check(state.IsRunning && state.MapId == 1028 && state.RulesVersion == 3 && state.ResourceVersion == 7, "Rust resource preflight");
                Program.Throws<InvalidOperationException>(() => gateway.StartAsync(2).GetAwaiter().GetResult());
                using (var login = Connect(state.LoginPort)) AssertBody(login, "version");
                using (var logic = Connect(state.LogicPort))
                {
                    AssertBody(logic, "login"); AssertBody(logic, "sync");
                    foreach (var name in new[] { "heartbeat", "custom", "governor", "reconnect" })
                    {
                        var vector = Vector(name);
                        var wire = Hex(vector[2]);
                        logic.GetStream().Write(wire, 0, wire.Length);
                        AssertBody(logic, name);
                    }
                    await gateway.StopAsync();
                    Program.Check(logic.GetStream().ReadByte() == -1, "stop closes original client socket");
                }
                Program.Check(gateway.GetSnapshot().ActiveConnections == 0 && gateway.GetSnapshot().LogicPort == 0, "stop joins native tasks");
                Program.Throws<InvalidOperationException>(() => gateway.StartAsync(1).GetAwaiter().GetResult());
                await gateway.StartAsync(2);
                await gateway.StopAsync();
                const ulong largeEpoch = 9007199254740993UL;
                await gateway.StartAsync(largeEpoch);
                Program.Check(gateway.GetSnapshot().Epoch == largeEpoch, "epoch retains all u64 bits across JSON and ABI");
                using (var logic = Connect(gateway.GetSnapshot().LogicPort)) AssertBody(logic, "login");
                await gateway.StopAsync();
                await gateway.StopAsync();
                gateway.Dispose(); gateway.Dispose();
                Program.Throws<ObjectDisposedException>(() => gateway.GetSnapshot());
            }
            await CancelPending(library);
            await BackendFailure(library);
            await IndependentPlatformRequests(library);
            await ClientGatewayLoadoutTests.Run(library);
            if (string.Equals(Path.GetFileName(library), "t7r_battle_ffi.dll", StringComparison.OrdinalIgnoreCase))
            {
                await NativeBattleHostTests.Run(library);
                await ClientGatewayTitleTests.Run(library);
                await ClientGatewayBattleTests.Run(library);
            }
            Console.WriteLine("Rust gateway C# -> C ABI -> socket checks passed (shared Python wire corpus)");
        }

        private static async Task CancelPending(string library)
        {
            var backend = new WaitingBackend();
            using (var gateway = new ClientGateway(library, Resources(), backend))
            {
                await gateway.StartAsync(10);
                using (var logic = Connect(gateway.GetSnapshot().LogicPort))
                {
                    await Deadline(backend.Started.Task);
                    var elapsed = Stopwatch.StartNew();
                    await Deadline(gateway.StopAsync());
                    Program.Check(elapsed.Elapsed < TimeSpan.FromSeconds(2), "stop cancels asynchronous backend");
                    Program.Check(gateway.GetSnapshot().PendingRequests == 0, "cancel clears pending requests");
                }
                await gateway.StartAsync(11);
                Program.Check(gateway.GetSnapshot().Epoch == 11, "restart uses new epoch");
                await gateway.StopAsync();
            }
        }

        private static async Task BackendFailure(string library)
        {
            using (var gateway = new ClientGateway(library, Resources(), new FailedBackend()))
            {
                await gateway.StartAsync(1);
                using (var logic = Connect(gateway.GetSnapshot().LogicPort))
                    Program.Check(logic.GetStream().ReadByte() == -1, "backend failure sends no login success");
                Program.Check(gateway.LastBackendError.Contains("backend fixture failure"), "backend error is observable");
                Program.Check(gateway.GetSnapshot().IsRunning && !gateway.Completion.IsFaulted, "one backend request failure does not stop listeners");
                await gateway.StopAsync();
            }
        }
        private static async Task IndependentPlatformRequests(string library)
        {
            var backend = new DelayedFirstBackend();
            using (var gateway = new ClientGateway(library, Resources(), backend))
            {
                await gateway.StartAsync(1);
                using (var first = Connect(gateway.GetSnapshot().LogicPort))
                {
                    await Deadline(backend.Started.Task);
                    using (var second = Connect(gateway.GetSnapshot().LogicPort))
                    {
                        AssertBody(second, "login");
                        Program.Check(!backend.Release.Task.IsCompleted, "second connection completes while the first backend call is still waiting");
                        backend.Release.TrySetResult(true);
                        AssertBody(first, "login");
                    }
                }
                await gateway.StopAsync();
            }
        }
        private static async Task Deadline(Task task)
        {
            if (await Task.WhenAny(task, Task.Delay(5000)) != task) throw new TimeoutException("Gateway fixture deadline.");
            await task;
        }
        internal static TcpClient Connect(ushort port)
        {
            var client = new TcpClient();
            try
            {
                client.Connect("127.0.0.1", port);
                var stream = client.GetStream(); stream.ReadTimeout = 5000; stream.WriteTimeout = 5000;
                var auth = new byte[29]; auth[0] = 0x55; auth[1] = 0x0e; auth[2] = 3; auth[23] = 3;
                stream.Write(auth, 0, auth.Length);
                var response = Read(stream, 48);
                Program.Check(response.SequenceEqual(Hex("550e01000000003000000000000300207aca0fd9bcd6ec7c9f97466616e6a2826f4220bc6fb86f56960c05cd30dbe3f8")), "gateway original AUTH");
                return client;
            }
            catch { client.Dispose(); throw; }
        }
        internal static byte[] Read(Stream stream, int size)
        {
            var buffer = new byte[size]; var count = 0;
            while (count < size)
            {
                var received = stream.Read(buffer, count, size - count);
                if (received == 0) throw new EndOfStreamException();
                count += received;
            }
            return buffer;
        }
        internal static void AssertBody(TcpClient client, string name)
        {
            var vector = Vector(name);
            var stream = client.GetStream();
            var header = Read(stream, 12);
            Program.Check(header.Take(8).SequenceEqual(Hex("550e05000000000c")), "gateway plain frame header");
            var size = (header[8] << 24) | (header[9] << 16) | (header[10] << 8) | header[11];
            Program.Check(size >= 11 && size < 4096, "bounded gateway response");
            var body = Read(stream, size);
            Program.Check(((body[1] << 8) | body[2]) == int.Parse(vector[1]), "gateway command: " + name);
            Program.Check(body.Skip(11).SequenceEqual(Hex(vector[3])), "gateway response matches Python: " + name);
        }
        private static string[] Vector(string name) => File.ReadAllLines(Fixture("gateway-v1.txt"))
            .First(line => line.StartsWith(name + " ", StringComparison.Ordinal)).Split(' ');
        internal static byte[] Hex(string text) => Enumerable.Range(0, text.Length / 2)
            .Select(index => Convert.ToByte(text.Substring(index * 2, 2), 16)).ToArray();

        private abstract class IdentityBackend : IPlatformBackend
        {
            public abstract Task<PlatformIdentity> ReadIdentityAsync(CancellationToken cancellation);
            public Task<PlatformProfile> ReadProfileAsync(CancellationToken cancellation) => throw new NotSupportedException();
            public Task<PlatformProfileUpdate> SetFormationAsync(IReadOnlyList<int> formation, CancellationToken cancellation) => throw new NotSupportedException();
            public Task<PlatformProfileUpdate> SetWeaponsAsync(int heroId, IReadOnlyList<PlatformWeaponSlot> slots, CancellationToken cancellation) => throw new NotSupportedException();
        }
        private sealed class WaitingBackend : IdentityBackend
        {
            internal readonly TaskCompletionSource<bool> Started = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            public override async Task<PlatformIdentity> ReadIdentityAsync(CancellationToken cancellation)
            {
                Started.TrySetResult(true);
                await Task.Delay(Timeout.Infinite, cancellation).ConfigureAwait(false);
                throw new InvalidOperationException("Unreachable after cancellation.");
            }
        }
        private sealed class DelayedFirstBackend : IdentityBackend
        {
            private int _calls;
            internal readonly TaskCompletionSource<bool> Started = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            internal readonly TaskCompletionSource<bool> Release = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            public override async Task<PlatformIdentity> ReadIdentityAsync(CancellationToken cancellation)
            {
                if (Interlocked.Increment(ref _calls) == 1)
                {
                    Started.TrySetResult(true);
                    using (cancellation.Register(() => Release.TrySetCanceled())) await Release.Task.ConfigureAwait(false);
                }
                return new PlatformIdentity("测试玩家", 7, 100);
            }
        }
        private sealed class FailedBackend : IdentityBackend
        {
            public override Task<PlatformIdentity> ReadIdentityAsync(CancellationToken cancellation) =>
                Task.FromException<PlatformIdentity>(new IOException("backend fixture failure"));
        }
    }
}
