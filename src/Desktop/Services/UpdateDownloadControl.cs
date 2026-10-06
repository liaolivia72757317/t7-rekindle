using System;
using System.Diagnostics;
using System.Threading;
using System.Threading.Tasks;

namespace T7.Rekindle.Desktop.Services
{
    internal sealed class UpdateDownloadControl
    {
        private readonly object _sync = new object();
        private TaskCompletionSource<bool> _resume;
        private event Action<bool> PauseChanged;

        internal bool IsPaused { get { lock (_sync) return _resume != null; } }

        internal void Pause()
        {
            lock (_sync)
            {
                if (_resume != null) return;
                _resume = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                PauseChanged?.Invoke(true);
            }
        }

        internal void Resume()
        {
            TaskCompletionSource<bool> resume;
            lock (_sync)
            {
                resume = _resume;
                if (resume == null) return;
                _resume = null;
                PauseChanged?.Invoke(false);
            }
            resume.TrySetResult(true);
        }

        internal IDisposable StartNetworkTimeout(CancellationTokenSource cancellation, TimeSpan duration) =>
            new NetworkTimeout(this, cancellation, duration);

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

        private sealed class NetworkTimeout : IDisposable
        {
            private readonly UpdateDownloadControl _control;
            private readonly CancellationTokenSource _cancellation;
            private readonly TimeSpan _duration;
            private readonly Stopwatch _elapsed = new Stopwatch();
            private readonly Timer _timer;
            private bool _disposed;

            internal NetworkTimeout(UpdateDownloadControl control, CancellationTokenSource cancellation, TimeSpan duration)
            {
                if (duration < TimeSpan.Zero && duration != Timeout.InfiniteTimeSpan)
                    throw new ArgumentOutOfRangeException(nameof(duration));
                _control = control;
                _cancellation = cancellation;
                _duration = duration;
                _timer = new Timer(OnTimeout, null, Timeout.Infinite, Timeout.Infinite);
                lock (_control._sync)
                {
                    SetPaused(_control._resume != null);
                    _control.PauseChanged += SetPaused;
                }
            }

            private void SetPaused(bool paused)
            {
                if (paused || _duration == Timeout.InfiniteTimeSpan)
                {
                    _elapsed.Stop();
                    _timer.Change(Timeout.Infinite, Timeout.Infinite);
                }
                else
                {
                    _elapsed.Start();
                    Schedule();
                }
            }

            private void Schedule()
            {
                var remaining = _duration - _elapsed.Elapsed;
                _timer.Change(TimeSpan.FromMilliseconds(Math.Max(0, Math.Ceiling(remaining.TotalMilliseconds))),
                    Timeout.InfiniteTimeSpan);
            }

            private void OnTimeout(object state)
            {
                lock (_control._sync)
                {
                    // A callback queued before Pause must recheck the active-time budget.
                    if (_disposed || !_elapsed.IsRunning) return;
                    if (_elapsed.Elapsed < _duration) Schedule();
                    else _cancellation.Cancel();
                }
            }

            public void Dispose()
            {
                lock (_control._sync)
                {
                    if (_disposed) return;
                    _disposed = true;
                    _control.PauseChanged -= SetPaused;
                    _timer.Dispose();
                }
            }
        }
    }
}
