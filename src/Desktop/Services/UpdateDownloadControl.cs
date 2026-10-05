using System.Threading;
using System.Threading.Tasks;

namespace T7.Rekindle.Desktop.Services
{
    internal sealed class UpdateDownloadControl
    {
        private readonly object _sync = new object();
        private TaskCompletionSource<bool> _resume;

        internal bool IsPaused { get { lock (_sync) return _resume != null; } }

        internal void Pause()
        {
            lock (_sync)
                if (_resume == null) _resume = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        }

        internal void Resume()
        {
            TaskCompletionSource<bool> resume;
            lock (_sync) { resume = _resume; _resume = null; }
            resume?.TrySetResult(true);
        }

        internal async Task WaitWhilePausedAsync(CancellationToken cancellation)
        {
            while (true)
            {
                cancellation.ThrowIfCancellationRequested();
                Task resume;
                lock (_sync) resume = _resume?.Task;
                if (resume == null) return;
                var cancelled = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                using (cancellation.Register(() => cancelled.TrySetCanceled()))
                    await (await Task.WhenAny(resume, cancelled.Task).ConfigureAwait(false)).ConfigureAwait(false);
            }
        }
    }
}
