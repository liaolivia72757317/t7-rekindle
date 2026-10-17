using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using T7R.Battle;
using T7R.Core;

namespace T7R.BattleHarness
{
    internal static class NativeBattleHostTests
    {
        internal static async Task Run(string library)
        {
            await StopDuringRearm(library, false);
            await StopDuringRearm(library, true);
            Console.WriteLine("Headless host stop/dispose drains profile rearming and ignores late callbacks");
        }

        private static async Task StopDuringRearm(string library, bool dispose)
        {
            var root = Path.Combine(Path.GetTempPath(), "t7r-host-stop-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            try
            {
                var backend = new PausedBackend(Path.Combine(root, "profile.json"));
                var gateway = new ClientGateway(library, LocalProfileTests.Resources, backend);
                var host = new NativeBattleHost(root, root, root);
                Field(typeof(NativeBattleHost), "_gateway").SetValue(host, gateway);
                Field(typeof(NativeBattleHost), "_cancellation").SetValue(host, new CancellationTokenSource());
                Field(typeof(NativeBattleHost), "_monitor").SetValue(host, Task.CompletedTask);
                var rearm = (Func<ClientGateway, bool, CancellationToken, Task>)Delegate.CreateDelegate(
                    typeof(Func<ClientGateway, bool, CancellationToken, Task>), host,
                    typeof(NativeBattleHost).GetMethod("RearmAsync", BindingFlags.Instance | BindingFlags.NonPublic));
                gateway.SetProfileChangedHandler(cancellation => rearm(gateway, false, cancellation));
                await gateway.StartAsync(1);
                await gateway.StartLocalBattleAsync(1, 1, 1);
                using (var logic = ClientGatewayTests.Connect(gateway.GetSnapshot().LogicPort))
                {
                    ClientGatewayTests.AssertBody(logic, "login");
                    ClientGatewayTests.AssertBody(logic, "sync");
                    backend.Pause = true;
                    var row = File.ReadAllLines(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "fixtures", "loadout-v1.txt"))
                        .Single(line => line.StartsWith("weapons-change ", StringComparison.Ordinal)).Split(' ');
                    var request = ClientGatewayTests.Hex(row[3]);
                    logic.GetStream().Write(request, 0, request.Length);
                    await Deadline(backend.Started.Task);

                    var stopped = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                    var thread = new Thread(() =>
                    {
                        try { if (dispose) host.Dispose(); else host.Stop(); stopped.SetResult(true); }
                        catch (Exception error) { stopped.SetException(error); }
                    }) { IsBackground = true };
                    thread.Start();
                    var sync = Field(typeof(NativeBattleHost), "_sync").GetValue(host);
                    Program.Check(SpinWait.SpinUntil(() =>
                    {
                        if (Monitor.TryEnter(sync)) { Monitor.Exit(sync); return false; }
                        return (thread.ThreadState & ThreadState.WaitSleepJoin) != 0;
                    }, 5000), "stop reached the in-flight rebuild barrier");
                    var lifecycle = (SemaphoreSlim)Field(typeof(ClientGateway), "_lifecycle").GetValue(gateway);
                    var lifecycleAvailable = lifecycle.CurrentCount == 1;
                    backend.Release.TrySetResult(true);
                    Program.Check(lifecycleAvailable, "host stop must not hold gateway lifecycle while a profile rebuild is in flight");
                    await Deadline(stopped.Task);
                    await Deadline(rearm(gateway, false, CancellationToken.None));
                    if (!dispose) Program.Check(host.GetSnapshot().State == BattleHostState.Stopped, "host stopped after rebuilding");
                    host.Dispose();
                }
            }
            finally
            {
                var resolved = Path.GetFullPath(root);
                if (Path.GetDirectoryName(resolved) != Path.GetTempPath().TrimEnd(Path.DirectorySeparatorChar))
                    throw new InvalidOperationException("Unexpected host fixture cleanup path.");
                Directory.Delete(resolved, true);
            }
        }

        private static FieldInfo Field(Type type, string name) => type.GetField(name, BindingFlags.Instance | BindingFlags.NonPublic);

        private static async Task Deadline(Task task)
        {
            if (await Task.WhenAny(task, Task.Delay(5000)) != task) throw new TimeoutException("Host stop fixture deadline.");
            await task;
        }

        private sealed class PausedBackend : IPlatformBackend
        {
            private readonly LocalPlatformBackend _inner;
            internal bool Pause;
            internal readonly TaskCompletionSource<bool> Started = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            internal readonly TaskCompletionSource<bool> Release = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            internal PausedBackend(string path) { _inner = new LocalPlatformBackend("测试玩家", LocalProfileTests.Resources, path); }
            public Task<PlatformIdentity> ReadIdentityAsync(CancellationToken cancellation) => _inner.ReadIdentityAsync(cancellation);
            public async Task<PlatformProfile> ReadProfileAsync(CancellationToken cancellation)
            {
                if (Pause) { Started.TrySetResult(true); await Release.Task.ConfigureAwait(false); }
                return await _inner.ReadProfileAsync(cancellation).ConfigureAwait(false);
            }
            public Task<PlatformProfileUpdate> SetFormationAsync(IReadOnlyList<int> formation, CancellationToken cancellation) =>
                _inner.SetFormationAsync(formation, cancellation);
            public Task<PlatformProfileUpdate> SetWeaponsAsync(int heroId, IReadOnlyList<PlatformWeaponSlot> slots, CancellationToken cancellation) =>
                _inner.SetWeaponsAsync(heroId, slots, cancellation);
        }
    }
}
