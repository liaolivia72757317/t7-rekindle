using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using T7.Rekindle.Desktop.Services;

namespace T7.Rekindle.Desktop.ViewModels
{
    public sealed class UpdateDownloadViewModel : ObservableObject, IDisposable
    {
        private readonly Func<LauncherUpdateAsset, IProgress<UpdateDownloadProgress>, CancellationToken, UpdateDownloadControl, Task<string>> _download;
        private readonly Func<string, Task<bool>> _install;
        private readonly Action<string> _openAddress;
        private CancellationTokenSource _cancellation;
        private Task _downloadTask;
        private string _installerPath;
        private string _statusText;
        private string _errorText = string.Empty;
        private string _progressText = string.Empty;
        private string _sourceNotice;
        private double _percent;
        private bool _hasProgress;
        private bool _isDownloading;
        private bool _isInstalling;
        private bool _cancelRequested;
        private bool _disposed;
        private UpdateDownloadControl _downloadControl;
        private readonly LauncherHistoryEntry _historyEntry;
        internal Func<bool> CanStartOperation { get; set; } = () => true;

        internal UpdateDownloadViewModel(LauncherUpdateInfo info,
            Func<LauncherUpdateAsset, IProgress<UpdateDownloadProgress>, CancellationToken, UpdateDownloadControl, Task<string>> download,
            Func<string, Task<bool>> install, Action<string> openAddress, LauncherHistoryEntry historyEntry = null)
        {
            _historyEntry = historyEntry;
            Info = info ?? throw new ArgumentNullException(nameof(info));
            _download = download ?? throw new ArgumentNullException(nameof(download));
            _install = install ?? throw new ArgumentNullException(nameof(install));
            _openAddress = openAddress ?? throw new ArgumentNullException(nameof(openAddress));
            _sourceNotice = info.SourceNotice ?? string.Empty;
            _statusText = info.Installer == null && info.IsNewVersion
                ? "该版本缺少可校验的安装包，请从发布页手动下载。" : string.Empty;
            PrimaryCommand = new AsyncRelayCommand(ActAsync, () => !_disposed && HasPrimaryAction && !IsDownloading && !IsInstalling && CanStartOperation());
            PauseDownloadCommand = new RelayCommand(TogglePause, () => IsDownloading && !_cancelRequested && !_disposed);
            CancelDownloadCommand = new RelayCommand(CancelDownload, () => IsDownloading && !_cancelRequested);
        }

        public LauncherUpdateInfo Info { get; }
        public bool IsDownloading => _isDownloading;
        public bool IsPaused => _downloadControl?.IsPaused == true;
        public string PauseActionText => IsPaused ? "继续下载" : "暂停下载";
        public bool IsInstalling => _isInstalling;
        public bool CanClose => !IsInstalling;
        public bool HasDownloadedInstaller => !string.IsNullOrEmpty(_installerPath);
        public bool HasPrimaryAction => _historyEntry != null ? _historyEntry.CanRollback && CanDownloadInstaller
            : Info.IsNewVersion && (CanDownloadInstaller || Info.HasDownloadAddress);
        private bool CanDownloadInstaller => (_historyEntry?.CanRollback ?? Info.IsNewVersion) && Info.Installer != null;
        public string PrimaryActionText => IsInstalling ? "准备安装…" : IsDownloading ? (IsPaused ? "已暂停" : "下载中…")
            : HasDownloadedInstaller ? (_historyEntry == null ? "立即安装" : "立即回退")
            : _historyEntry == null ? "下载更新" : "下载此版本";
        public string StatusText
        {
            get => _statusText;
            private set { if (SetProperty(ref _statusText, value)) OnPropertyChanged(nameof(HasStatusText)); }
        }
        public bool HasStatusText => !string.IsNullOrEmpty(StatusText);
        public string ErrorText
        {
            get => _errorText;
            private set { if (SetProperty(ref _errorText, value)) OnPropertyChanged(nameof(HasError)); }
        }
        public bool HasError => ErrorText.Length != 0;
        public string SourceNotice
        {
            get => _sourceNotice;
            private set { if (SetProperty(ref _sourceNotice, value)) OnPropertyChanged(nameof(HasSourceNotice)); }
        }
        public bool HasSourceNotice => SourceNotice.Length != 0;
        public string ProgressText { get => _progressText; private set => SetProperty(ref _progressText, value); }
        public double Percent { get => _percent; private set => SetProperty(ref _percent, value); }
        public bool HasProgress { get => _hasProgress; private set => SetProperty(ref _hasProgress, value); }
        public IAsyncRelayCommand PrimaryCommand { get; }
        public RelayCommand PauseDownloadCommand { get; }
        public RelayCommand CancelDownloadCommand { get; }

        private async Task ActAsync()
        {
            if (_disposed || !HasPrimaryAction || IsDownloading || IsInstalling || !CanStartOperation()) return;
            ErrorText = string.Empty;
            if (!CanDownloadInstaller)
            {
                try { _openAddress(Info.DownloadAddress); }
                catch (Exception error) { ErrorText = "打开发布页失败：" + error.Message; }
                return;
            }
            if (!HasDownloadedInstaller)
            {
                _downloadTask = DownloadAsync();
                await _downloadTask.ConfigureAwait(true);
                return;
            }
            _isInstalling = true;
            StatusText = "正在确认退出并准备安装…";
            RefreshActions();
            try
            {
                StatusText = await _install(_installerPath).ConfigureAwait(true)
                    ? "安装程序已启动。" : "安装已取消，安装包已保留。";
            }
            catch (Exception error)
            {
                if (error is FileNotFoundException) _installerPath = null;
                StatusText = "安装未完成，启动器保持打开。";
                ErrorText = error.Message;
            }
            finally { _isInstalling = false; RefreshActions(); }
        }

        private async Task DownloadAsync()
        {
            _isDownloading = true;
            _cancelRequested = false;
            _downloadControl = new UpdateDownloadControl();
            HasProgress = true;
            Percent = 0;
            ProgressText = "正在连接下载源…";
            StatusText = "正在下载安装包，可暂停或取消。";
            SourceNotice = Info.SourceNotice ?? string.Empty;
            using (var cancellation = new CancellationTokenSource())
            {
                _cancellation = cancellation;
                RefreshActions();
                var progress = new Progress<UpdateDownloadProgress>(value =>
                {
                    if (_cancellation != cancellation || cancellation.IsCancellationRequested || IsPaused) return;
                    Percent = value.Percent;
                    ProgressText = string.Format("{0} · {1:F1}% · {2:F1} / {3:F1} MiB", value.Source, value.Percent,
                        value.BytesReceived / 1048576.0, value.TotalBytes / 1048576.0);
                    if (!string.IsNullOrEmpty(value.Notice)) SourceNotice = value.Notice;
                });
                try
                {
                    _installerPath = await _download(Info.Installer, progress, cancellation.Token, _downloadControl).ConfigureAwait(true);
                    Percent = 100;
                    ProgressText = string.Format("100% · {0:F1} MiB · SHA-256 校验通过", Info.Installer.Size / 1048576.0);
                    StatusText = "下载完成。点击“" + (_historyEntry == null ? "立即安装" : "立即回退") + "”后将确认退出当前启动器。";
                }
                catch (OperationCanceledException) when (cancellation.IsCancellationRequested)
                {
                    ProgressText = string.Format("下载已取消 · {0:F1}%", Percent);
                    StatusText = "下载已取消，重新下载将从头开始。";
                }
                catch (Exception error)
                {
                    ProgressText = string.Format("下载已停止 · {0:F1}%", Percent);
                    StatusText = "下载失败，请重试。";
                    ErrorText = error.Message;
                }
                finally
                {
                    _cancellation = null;
                    _downloadControl = null;
                    _isDownloading = false;
                    RefreshActions();
                }
            }
        }

        private void TogglePause()
        {
            if (!IsDownloading || _cancelRequested || _disposed) return;
            if (IsPaused) _downloadControl.Resume();
            else _downloadControl.Pause();
            StatusText = IsPaused ? "下载已暂停，点击“继续下载”后继续。" : "正在下载安装包，可暂停或取消。";
            RefreshActions();
        }

        private void CancelDownload()
        {
            if (_cancellation == null || _cancelRequested) return;
            _cancelRequested = true;
            StatusText = "正在取消下载…";
            _cancellation.Cancel();
            RefreshActions();
        }

        internal async Task CancelAndWaitAsync()
        {
            CancelDownload();
            var pending = _downloadTask;
            if (pending != null) await pending.ConfigureAwait(true);
        }

        private void RefreshActions()
        {
            foreach (var name in new[] { nameof(IsDownloading), nameof(IsPaused), nameof(PauseActionText), nameof(IsInstalling), nameof(CanClose),
                nameof(HasDownloadedInstaller), nameof(PrimaryActionText), nameof(HasPrimaryAction) })
                OnPropertyChanged(name);
            PrimaryCommand.NotifyCanExecuteChanged();
            PauseDownloadCommand.NotifyCanExecuteChanged();
            CancelDownloadCommand.NotifyCanExecuteChanged();
        }

        internal void RefreshAvailability() => PrimaryCommand.NotifyCanExecuteChanged();

        public void Dispose()
        {
            _disposed = true;
            CancelDownload();
            RefreshActions();
        }
    }
}
