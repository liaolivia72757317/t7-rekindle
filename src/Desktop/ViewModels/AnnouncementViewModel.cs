using System;
using System.Threading;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using T7R.Desktop.Services;

namespace T7R.Desktop.ViewModels
{
    internal sealed class AnnouncementViewModel : ObservableObject, IDisposable
    {
        private static readonly TimeSpan RefreshInterval = TimeSpan.FromSeconds(60);
        private readonly CancellationTokenSource _cancellation = new CancellationTokenSource();
        private readonly AnnouncementApi _api;
        private readonly Func<CancellationToken, Task<LauncherAnnouncement>> _fetch;
        private readonly Func<DateTime> _utcNow;
        private DateTime? _lastAttemptUtc;
        private bool _disposed;
        internal bool ChecksStarted { get; private set; }
        internal Task Loading { get; private set; } = Task.CompletedTask;
        internal LauncherAnnouncement Current { get; private set; } = LauncherAnnouncement.BuiltIn;
        internal event Action<Exception> LoadFailed;

        internal AnnouncementViewModel(Func<CancellationToken, Task<LauncherAnnouncement>> fetch = null, Func<DateTime> utcNow = null)
        {
            _utcNow = utcNow ?? (() => DateTime.UtcNow);
            if (fetch != null) _fetch = fetch;
            else
            {
                _api = AnnouncementApi.CreateConfigured(LauncherInformation.AuthBaseAddress);
                _fetch = token => _api == null ? Task.FromResult<LauncherAnnouncement>(null) : _api.GetLatestAsync(token);
            }
        }
        internal void Start()
        {
            if (_disposed || ChecksStarted) return;
            ChecksStarted = true;
            _ = RefreshAsync();
        }
        internal Task RefreshAsync()
        {
            if (_disposed || !Loading.IsCompleted) return Loading;
            var now = _utcNow();
            if (_lastAttemptUtc.HasValue && now - _lastAttemptUtc.Value < RefreshInterval) return Loading;
            _lastAttemptUtc = now;
            Loading = LoadAsync(_cancellation.Token);
            return Loading;
        }
        private async Task LoadAsync(CancellationToken cancellation)
        {
            try
            {
                var value = await _fetch(cancellation);
                if (!_disposed) SetCurrent(value ?? LauncherAnnouncement.BuiltIn);
            }
            catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
            catch (Exception error)
            {
                if (_disposed) return;
                SetCurrent(LauncherAnnouncement.BuiltIn);
                LoadFailed?.Invoke(error);
            }
        }
        private void SetCurrent(LauncherAnnouncement value)
        {
            Current = value;
            OnPropertyChanged(nameof(Current));
        }
        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;
            _cancellation.Cancel();
            _api?.Dispose();
            _cancellation.Dispose();
        }
    }
}
