using System;
using System.Threading;
using System.Threading.Tasks;
using T7R.Battle;

namespace T7R.BattleHarness
{
    internal static class BattleRearmCoordinatorTests
    {
        internal static async Task Run()
        {
            var coordinator = new BattleRearmCoordinator();
            var release = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var queries = 0;
            var builds = 0;
            var first = coordinator.TryRearmAsync(() => { queries++; return true; },
                async token => { builds++; await release.Task; }, CancellationToken.None);
            Program.Check(queries == 1 && builds == 1, "first rebuild entered");
            var second = coordinator.TryRearmAsync(() => { queries++; return false; },
                token => { builds++; return Task.CompletedTask; }, CancellationToken.None);
            Program.Check(!second.IsCompleted && queries == 1, "room query waits for profile-triggered rebuild");
            release.SetResult(true);
            await Task.WhenAll(first, second);
            Program.Check(queries == 2 && builds == 1, "room return checked after rebuilding, not before it");

            release = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            first = coordinator.TryRearmAsync(() => true, token => release.Task, CancellationToken.None);
            var stopped = false;
            var stop = coordinator.StopAsync(() => stopped = true);
            Program.Check(!stop.IsCompleted && !stopped, "stop drains an in-flight rebuild");
            release.SetResult(true);
            await Task.WhenAll(first, stop);
            await coordinator.TryRearmAsync(() => throw new Exception("query after stop"),
                token => throw new Exception("rebuild after stop"), CancellationToken.None);
            Program.Check(stopped, "stopped coordinator never queries a disposed gateway");

            coordinator = new BattleRearmCoordinator();
            Program.Throws<InvalidOperationException>(() => coordinator.TryRearmAsync(() => true,
                token => throw new InvalidOperationException("rebuild failure"), CancellationToken.None).GetAwaiter().GetResult());
            var retried = false;
            await coordinator.TryRearmAsync(() => true, token => { retried = true; return Task.CompletedTask; }, CancellationToken.None);
            Program.Check(retried, "failed rebuild releases the transaction gate");
            Program.Throws<OperationCanceledException>(() => coordinator.TryRearmAsync(
                () => throw new Exception("cancelled query"), token => Task.CompletedTask,
                new CancellationToken(true)).GetAwaiter().GetResult());
        }
    }
}
