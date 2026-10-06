using System;
using System.ComponentModel;
using System.Threading.Tasks;
using System.Windows.Input;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using T7.Rekindle.Desktop.Services;

namespace T7.Rekindle.Desktop.ViewModels
{
    public sealed class AboutViewModel : ObservableObject, IDisposable
    {
        private readonly IDesktopInteraction _interaction;
        private readonly Func<Task<LauncherUpdateInfo>> _checkUpdate;
        private readonly Func<LauncherUpdateInfo, UpdateDownloadViewModel> _createDownload;
        private UpdateDownloadViewModel _updateDownload;
        private bool _disposed;
        private string _feedback = string.Empty;
        private string _updateStatus = "尚未检查更新";
        private LauncherUpdateInfo _lastUpdate;
        private DateTime? _lastCheck;
        public event Action<string, NoticeSeverity, string, Action> NoticeRaised;
        public event Action UpdateFinished;
        private string _updateError = string.Empty;
        private bool _isCheckingUpdate;

        public AboutViewModel(IDesktopInteraction interaction)
            : this(interaction, LauncherInformation.CheckUpdateAsync) { }

        internal AboutViewModel(IDesktopInteraction interaction, Func<Task<LauncherUpdateInfo>> checkUpdate,
            Func<LauncherUpdateInfo, UpdateDownloadViewModel> createDownload = null)
        {
            _interaction = interaction;
            _checkUpdate = checkUpdate;
            _createDownload = createDownload ?? (info => new UpdateDownloadViewModel(info,
                LauncherInformation.DownloadInstallerAsync, DesktopInteraction.InstallUpdateAsync, _interaction.OpenAddress));
            ShowContactCommand = new RelayCommand(() => OpenAddress(ContactAddress), () => HasContactAddress);
            CheckUpdateCommand = new AsyncRelayCommand(CheckUpdateAsync, () => !_disposed && !IsCheckingUpdate && !IsUpdating);
            CopyHashCommand = new RelayCommand(() => Copy(LauncherInformation.CommitHash, "完整 hash 已复制。"),
                () => LauncherInformation.CommitHash.Length != 0);
            ShowChangelogCommand = new RelayCommand(() => ShowDocument("版本日志", "CHANGELOG.md"));
            ShowLicensesCommand = new RelayCommand(() => Run(() => _interaction.ShowText("开源软件说明",
                "项目自身许可证\n\n" + LauncherInformation.ReadDocument("LICENSE") + "\n\n"
                + LauncherInformation.ReadDocument("THIRD-PARTY.txt") + "\n\n"
                + LauncherInformation.ReadDocument("THIRD-PARTY-NOTICES.txt"))));
            ShowThanksCommand = new RelayCommand(() => ShowDocument("特别感谢", "THANKS.md"));
            ShowEnvironmentInfoCommand = new RelayCommand(() => Run(_interaction.ShowEnvironmentInfo));
            ShowRepositoryCommand = new RelayCommand(() => OpenAddress(RepositoryAddress));
            ShowDownloadCommand = new RelayCommand(() => OpenAddress(LauncherInformation.DownloadAddress));
            ShowClientDownloadCommand = new RelayCommand(() => OpenAddress(ClientDownloadAddress));
            ShowBuildsCommand = new RelayCommand(() => OpenAddress(LauncherInformation.BuildsAddress));
            ShowIssuesCommand = new RelayCommand(() => OpenAddress(LauncherInformation.IssuesAddress));
            CopyRepositoryCommand = new RelayCommand(() => Copy(RepositoryAddress, "仓库地址已复制。"));
        }

        public string ContactAddress => LauncherInformation.ContactAddress;
        public bool HasContactAddress => !string.IsNullOrWhiteSpace(ContactAddress);
        public bool HasIssuesAddress => !string.IsNullOrWhiteSpace(IssuesAddress);
        public string ContactLabel => HasContactAddress ? "项目主页" : "未配置联系方式";
        public bool HasNewUpdate => _lastUpdate?.IsNewVersion == true && !IsCheckingUpdate && !UpdateFailed;
        public bool HasUpdateReminder => _lastUpdate?.IsNewVersion == true;
        public string UpdateReminderVersion => HasUpdateReminder ? _lastUpdate.TargetVersion : string.Empty;
        public string VersionCapsuleHint => "当前版本 " + Version
            + (HasUpdateReminder ? "\n发现新版本 " + UpdateReminderVersion
                + (UpdateFailed ? "（本次检查失败，保留上次检查结果）" : string.Empty) : string.Empty)
            + "\n打开更新页面";
        public bool UpdateFailed => UpdateError.Length != 0;
        public string LastCheckText => !_lastCheck.HasValue ? "尚未检查" : _lastCheck.Value.ToString("yyyy-MM-dd HH:mm") + (UpdateFailed ? " · 失败" : "");
        public string UpdateDescription => IsCheckingUpdate ? "正在获取正式发布信息" : UpdateFailed ? "检查失败，不影响本地启动。请稍后重试。"
            : _lastUpdate == null ? "点击检查更新，获取最新发布信息" : HasNewUpdate ? "可用版本 " + _lastUpdate.TargetVersion
            : !_lastUpdate.HasPublishedRelease ? "当前没有可用的正式发布版本" : _lastUpdate.IsCurrentVersionAhead ? "当前构建高于最新正式发布版" : "当前已使用最新的启动器版本";
        public string UpdateIcon => IsCheckingUpdate ? "loader" : UpdateFailed ? "alert-triangle" : _lastUpdate == null ? "info"
            : HasNewUpdate ? "download" : !_lastUpdate.HasPublishedRelease ? "info" : "check-circle";
        public string UpdateVersion => IsCheckingUpdate || UpdateFailed || _lastUpdate?.HasPublishedRelease != true
            ? string.Empty : _lastUpdate.TargetVersion;
        public string UpdateSummary => IsCheckingUpdate ? "正在获取更新日志…" : UpdateFailed ? "获取更新日志失败，请稍后重试。"
            : _lastUpdate == null ? "检查更新后显示版本更新日志。" : !_lastUpdate.HasPublishedRelease ? "暂无正式发布版本。"
            : string.IsNullOrWhiteSpace(_lastUpdate.Summary) ? "该版本未填写更新说明。" : _lastUpdate.Summary;
        public UpdateDownloadViewModel UpdateDownload => _updateDownload;
        public bool IsUpdating => UpdateDownload?.IsDownloading == true || UpdateDownload?.IsInstalling == true;
        public RelayCommand ShowContactCommand { get; }
        public string ProjectName => LauncherInformation.ProjectName;
        public string ProjectDescription => LauncherInformation.ProjectDescription;
        public string ProjectStatus => "当前处于开发阶段，完整人机对战与局域网联机尚未完成。";
        public string Version => LauncherInformation.Version;
        public string ShortHash => LauncherInformation.ShortHash;
        public string BuildDescription => LauncherInformation.CommitHash.Length == 0 ? "构建信息未提供" : "提交 " + ShortHash;
        public string HashDescription => LauncherInformation.CommitHash.Length == 0 ? "构建未记录 Git 提交" : LauncherInformation.CommitHash;
        public string RepositoryAddress => LauncherInformation.RepositoryAddress;
        public string RepositoryAddressDisplay => string.IsNullOrWhiteSpace(RepositoryAddress) ? "项目仓库地址待配置" : RepositoryAddress;
        public bool HasRepositoryAddress => !string.IsNullOrWhiteSpace(RepositoryAddress);
        public string DownloadAddress => LauncherInformation.DownloadAddress;
        public string ClientDownloadAddress => LauncherInformation.ClientDownloadAddress;
        public string BuildsAddress => LauncherInformation.BuildsAddress;
        public string IssuesAddress => LauncherInformation.IssuesAddress;
        public string Feedback
        {
            get => _feedback;
            private set { if (SetProperty(ref _feedback, value)) OnPropertyChanged(nameof(HasFeedback)); }
        }
        public bool HasFeedback => Feedback.Length != 0;
        public string UpdateStatus { get => _updateStatus; private set => SetProperty(ref _updateStatus, value); }
        public string UpdateError { get => _updateError; private set => SetProperty(ref _updateError, value); }
        public bool IsCheckingUpdate
        {
            get => _isCheckingUpdate;
            private set
            {
                if (!SetProperty(ref _isCheckingUpdate, value)) return;
                UpdateActionPresentation();
                CheckUpdateCommand?.NotifyCanExecuteChanged();
            }
        }
        private bool HasUpdateAction => HasNewUpdate && UpdateDownload != null;
        public string UpdateButtonText => IsCheckingUpdate ? "正在检查…" : !HasUpdateAction ? "检查更新"
            : UpdateDownload.IsDownloading ? UpdateDownload.PauseActionText : UpdateDownload.PrimaryActionText;
        public string UpdateButtonIcon => IsCheckingUpdate ? "loader" : !HasUpdateAction ? "refresh"
            : UpdateDownload.IsInstalling ? "loader" : UpdateDownload.IsPaused ? "play"
            : UpdateDownload.HasDownloadedInstaller ? "check-circle" : "download";
        public ICommand UpdateActionCommand => !HasUpdateAction ? CheckUpdateCommand
            : UpdateDownload.IsDownloading ? (ICommand)UpdateDownload.PauseDownloadCommand : UpdateDownload.PrimaryCommand;
        public IAsyncRelayCommand CheckUpdateCommand { get; }
        public RelayCommand CopyHashCommand { get; }
        public RelayCommand ShowChangelogCommand { get; }
        public RelayCommand ShowLicensesCommand { get; }
        public RelayCommand ShowThanksCommand { get; }
        public RelayCommand ShowEnvironmentInfoCommand { get; }
        public RelayCommand ShowRepositoryCommand { get; }
        public RelayCommand ShowDownloadCommand { get; }
        public RelayCommand ShowClientDownloadCommand { get; }
        public RelayCommand ShowBuildsCommand { get; }
        public RelayCommand ShowIssuesCommand { get; }
        public RelayCommand CopyRepositoryCommand { get; }

        private async Task CheckUpdateAsync()
        {
            if (_disposed || IsCheckingUpdate || IsUpdating) return;
            IsCheckingUpdate = true;
            Feedback = UpdateError = string.Empty;
            UpdateStatus = "正在检查更新…";
            UpdatePresentation();
            try
            {
                var update = await _checkUpdate();
                if (_disposed) return;
                _lastUpdate = update;
                SetUpdateDownload(update);
                UpdateStatus = _lastUpdate.StatusText;
                if (!_lastUpdate.HasPublishedRelease)
                    NoticeRaised?.Invoke("暂无正式发布版本", NoticeSeverity.Info, null, null);
            }
            catch (Exception error)
            {
                UpdateStatus = "检查更新失败";
                UpdateError = error.Message;
            }
            finally
            {
                _lastCheck = DateTime.Now;
                IsCheckingUpdate = false;
                UpdatePresentation();
                if (!_disposed) UpdateFinished?.Invoke();
            }
        }

        private void UpdatePresentation()
        {
            foreach (var name in new[] { nameof(LastCheckText), nameof(UpdateFailed), nameof(UpdateDescription), nameof(UpdateIcon),
                nameof(HasNewUpdate), nameof(UpdateVersion), nameof(UpdateSummary), nameof(HasUpdateReminder),
                nameof(UpdateReminderVersion), nameof(VersionCapsuleHint) }) OnPropertyChanged(name);
            UpdateActionPresentation();
        }

        private void UpdateActionPresentation()
        {
            foreach (var name in new[] { nameof(UpdateButtonText), nameof(UpdateButtonIcon), nameof(UpdateActionCommand) })
                OnPropertyChanged(name);
        }

        private void SetUpdateDownload(LauncherUpdateInfo info)
        {
            if (info.IsNewVersion && UpdateDownload != null && UpdateDownload.Info.TargetVersion == info.TargetVersion
                && UpdateDownload.Info.Installer?.Sha256 == info.Installer?.Sha256
                && UpdateDownload.Info.Installer?.DownloadAddress == info.Installer?.DownloadAddress
                && UpdateDownload.Info.DownloadAddress == info.DownloadAddress) return;
            if (_updateDownload != null)
            {
                _updateDownload.PropertyChanged -= OnDownloadChanged;
                _updateDownload.Dispose();
            }
            _updateDownload = info.IsNewVersion ? _createDownload(info) : null;
            if (_updateDownload != null) _updateDownload.PropertyChanged += OnDownloadChanged;
            OnPropertyChanged(nameof(UpdateDownload));
            OnPropertyChanged(nameof(IsUpdating));
        }

        private void OnDownloadChanged(object sender, PropertyChangedEventArgs e)
        {
            if (e.PropertyName == nameof(UpdateDownloadViewModel.PrimaryActionText)
                || e.PropertyName == nameof(UpdateDownloadViewModel.PauseActionText)) UpdateActionPresentation();
            if (e.PropertyName != nameof(UpdateDownloadViewModel.IsDownloading)
                && e.PropertyName != nameof(UpdateDownloadViewModel.IsInstalling)) return;
            OnPropertyChanged(nameof(IsUpdating));
            CheckUpdateCommand.NotifyCanExecuteChanged();
        }

        internal Task CancelUpdateDownloadAsync() => UpdateDownload?.CancelAndWaitAsync() ?? Task.CompletedTask;

        public void Dispose()
        {
            _disposed = true;
            if (_updateDownload != null)
            {
                _updateDownload.PropertyChanged -= OnDownloadChanged;
                _updateDownload.Dispose();
            }
            CheckUpdateCommand.NotifyCanExecuteChanged();
        }

        private void Copy(string text, string confirmation) => Run(() =>
        {
            _interaction.CopyText(text);
            Feedback = confirmation;
            NoticeRaised?.Invoke(confirmation, NoticeSeverity.Success, null, null);
        });
        private void ShowDocument(string title, string resource) => Run(() => _interaction.ShowMarkdown(title, LauncherInformation.ReadDocument(resource)));
        private void OpenAddress(string address)
        {
            try { _interaction.OpenAddress(address); }
            catch (Exception error)
            {
                Feedback = "打开浏览器失败：" + error.Message;
                NoticeRaised?.Invoke("打开浏览器失败", NoticeSeverity.Warning, "复制地址", () => Copy(address, "地址已复制"));
            }
        }
        private void Run(Action action)
        {
            try { action(); }
            catch (Exception error) { Feedback = "操作失败：" + error.Message; NoticeRaised?.Invoke("操作未完成，请重试", NoticeSeverity.Error, null, null); }
        }
    }
}
