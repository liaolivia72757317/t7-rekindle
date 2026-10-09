using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using T7.Rekindle.Desktop.Services;

namespace T7.Rekindle.Desktop.ViewModels
{
    public sealed class ReleaseHistoryViewModel : ObservableObject, IDisposable
    {
        private readonly Func<UpdateChannel, CancellationToken, Task<IReadOnlyList<LauncherHistoryEntry>>> _load;
        private readonly Func<LauncherHistoryEntry, UpdateDownloadViewModel> _createDownload;
        private readonly Func<bool> _otherBusy;
        private CancellationTokenSource _cancellation;
        private bool _disposed;
        private bool _loading;
        private UpdateChannel _channel;
        private LauncherHistoryEntry _selected;
        private UpdateDownloadViewModel _download;
        private IReadOnlyList<LauncherHistoryEntry> _entries = Array.Empty<LauncherHistoryEntry>();
        private string _status = "打开历史版本后加载列表。";
        private string _error = string.Empty;

        internal ReleaseHistoryViewModel(Func<UpdateChannel, CancellationToken, Task<IReadOnlyList<LauncherHistoryEntry>>> load,
            Func<LauncherHistoryEntry, UpdateDownloadViewModel> createDownload, Func<bool> otherBusy)
        {
            _load = load;
            _createDownload = createDownload;
            _otherBusy = otherBusy;
            RefreshCommand = new AsyncRelayCommand(LoadAsync, () => CanSelect);
        }

        internal event Action ActivityChanged;
        public IReadOnlyList<LauncherHistoryEntry> Entries => _entries;
        public IAsyncRelayCommand RefreshCommand { get; }
        public bool IsLoading => _loading;
        public bool IsBusy => IsLoading || Download?.IsDownloading == true || Download?.IsInstalling == true;
        public bool CanSelect => !_disposed && !IsBusy && !_otherBusy();
        public bool HasLoaded { get; private set; }
        public string StatusText => _status;
        public string ErrorText => _error;
        public bool HasError => _error.Length != 0;
        public UpdateDownloadViewModel Download => _download;
        public bool HasSelection => SelectedEntry != null;
        public string Summary => SelectedEntry == null ? "选择一个历史版本，查看说明与设置兼容信息。"
            : string.IsNullOrWhiteSpace(SelectedEntry.Summary) ? "该版本未填写更新说明。" : SelectedEntry.Summary;
        public LauncherHistoryEntry SelectedEntry
        {
            get => _selected;
            set { if (CanSelect && (value == null || Entries.Contains(value))) Select(value); }
        }

        private void Select(LauncherHistoryEntry entry)
        {
            if (ReferenceEquals(_selected, entry)) return;
            var reuse = _selected != null && entry != null && _selected.Identity == entry.Identity
                && _selected.Installer.Sha256 == entry.Installer.Sha256 && _selected.Installer.DownloadAddress == entry.Installer.DownloadAddress
                && _selected.SettingsSchemaVersion == entry.SettingsSchemaVersion;
            _selected = entry;
            if (!reuse)
            {
                if (_download != null) { _download.PropertyChanged -= OnDownloadChanged; _download.Dispose(); }
                _download = entry?.CanRollback == true ? _createDownload(entry) : null;
                if (_download != null)
                {
                    _download.CanStartOperation = () => !_disposed && !IsLoading && !_otherBusy();
                    _download.PropertyChanged += OnDownloadChanged;
                }
                OnPropertyChanged(nameof(Download));
            }
            foreach (var name in new[] { nameof(SelectedEntry), nameof(HasSelection), nameof(Summary) }) OnPropertyChanged(name);
        }

        private async Task LoadAsync()
        {
            if (!CanSelect) return;
            _loading = true;
            _error = string.Empty;
            _status = "正在读取历史版本…";
            RefreshState();
            using (var cancellation = new CancellationTokenSource())
            {
                _cancellation = cancellation;
                try
                {
                    var entries = await _load(_channel, cancellation.Token).ConfigureAwait(true);
                    if (_disposed || cancellation.IsCancellationRequested) return;
                    _entries = entries;
                    OnPropertyChanged(nameof(Entries));
                    HasLoaded = true;
                    var previous = SelectedEntry?.Identity;
                    Select(entries.FirstOrDefault(entry => entry.Identity == previous) ?? entries.FirstOrDefault(entry => entry.CanRollback));
                    _status = entries.Count == 0 ? "暂无历史版本。仅记录支持回退协议的新发布版本。"
                        : !entries.Any(entry => entry.CanRollback) ? "暂无可回退版本。" : "选择版本并下载，校验后再确认回退。";
                }
                catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { _status = "读取已取消。"; }
                catch (Exception error)
                {
                    _error = error.Message;
                    _status = HasLoaded ? "读取历史版本失败，保留上次列表；请刷新重试。" : "读取历史版本失败，请刷新重试。";
                }
                finally { _cancellation = null; _loading = false; RefreshState(); }
            }
        }

        internal void ResetChannel(UpdateChannel channel)
        {
            if (IsBusy) throw new InvalidOperationException("历史版本操作进行中。");
            _channel = channel;
            _entries = Array.Empty<LauncherHistoryEntry>();
            HasLoaded = false;
            Select(null);
            _status = "打开历史版本后加载列表。";
            _error = string.Empty;
            OnPropertyChanged(nameof(Entries));
            RefreshState();
        }

        internal void RefreshAvailability()
        {
            OnPropertyChanged(nameof(CanSelect));
            RefreshCommand.NotifyCanExecuteChanged();
            Download?.RefreshAvailability();
        }

        private void RefreshState()
        {
            foreach (var name in new[] { nameof(IsLoading), nameof(IsBusy), nameof(StatusText), nameof(ErrorText), nameof(HasError) }) OnPropertyChanged(name);
            RefreshAvailability();
            ActivityChanged?.Invoke();
        }

        private void OnDownloadChanged(object sender, PropertyChangedEventArgs args)
        {
            if (args.PropertyName == nameof(UpdateDownloadViewModel.IsDownloading) || args.PropertyName == nameof(UpdateDownloadViewModel.IsInstalling)) RefreshState();
        }

        internal async Task CancelAndWaitAsync()
        {
            _cancellation?.Cancel();
            if (RefreshCommand.ExecutionTask != null) await RefreshCommand.ExecutionTask.ConfigureAwait(true);
            if (Download != null) await Download.CancelAndWaitAsync().ConfigureAwait(true);
        }

        public void Dispose()
        {
            _disposed = true;
            _cancellation?.Cancel();
            if (_download != null) { _download.PropertyChanged -= OnDownloadChanged; _download.Dispose(); }
            RefreshAvailability();
        }
    }
}
