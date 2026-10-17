using System;
using System.Threading;
using System.Threading.Tasks;

namespace T7R.Battle
{
    // The room-return query and the rebuild are one transaction. Stop drains it
    // before the owner disposes the gateway or detaches the native sampler.
    internal sealed class BattleRearmCoordinator
    {
        private readonly SemaphoreSlim _gate = new SemaphoreSlim(1, 1);
        private bool _stopped;

        internal async Task TryRearmAsync(Func<bool> requested,
            Func<CancellationToken, Task> rebuild, CancellationToken cancellation)
        {
            await _gate.WaitAsync(cancellation).ConfigureAwait(false);
            try
            {
                cancellation.ThrowIfCancellationRequested();
                if (_stopped || !requested()) return;
                await rebuild(cancellation).ConfigureAwait(false);
            }
            finally { _gate.Release(); }
        }

        internal async Task StopAsync(Action onStopping)
        {
            await _gate.WaitAsync().ConfigureAwait(false);
            try { _stopped = true; onStopping(); }
            finally { _gate.Release(); }
        }
    }
}
