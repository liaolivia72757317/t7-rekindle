using System;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using T7.Rekindle.Desktop.Services;

namespace T7.Rekindle.Desktop.ViewModels
{
    public sealed class AboutViewModel : ObservableObject
    {
        private readonly IDesktopInteraction _interaction;
        private readonly Func<Task<LauncherUpdateInfo>> _checkUpdate;
        private string _feedback = string.Empty;
        private string _updateStatus = string.Empty;
        private string _updateError = string.Empty;
        private bool _isCheckingUpdate;

        public AboutViewModel(IDesktopInteraction interaction)
            : this(interaction, LauncherInformation.CheckUpdateAsync) { }

        internal AboutViewModel(IDesktopInteraction interaction, Func<Task<LauncherUpdateInfo>> checkUpdate)
        {
            _interaction = interaction;
            _checkUpdate = checkUpdate;
            CheckUpdateCommand = new AsyncRelayCommand(CheckUpdateAsync, () => !IsCheckingUpdate);
            CopyHashCommand = new RelayCommand(() => Copy(LauncherInformation.CommitHash, "完整 hash 已复制。"),
                () => LauncherInformation.CommitHash.Length != 0);
            ShowChangelogCommand = new RelayCommand(() => ShowDocument("版本日志", "CHANGELOG.md"));
            ShowLicensesCommand = new RelayCommand(() => Run(() => _interaction.ShowText("开源软件说明",
                "项目自身许可证\n\n" + LauncherInformation.ReadDocument("LICENSE") + "\n\n"
                + LauncherInformation.ReadDocument("THIRD-PARTY.txt") + "\n\n"
                + LauncherInformation.ReadDocument("THIRD-PARTY-NOTICES.txt"))));
            ShowThanksCommand = new RelayCommand(() => ShowDocument("特别感谢", "THANKS.md"));
            ShowRepositoryCommand = new RelayCommand(() => OpenAddress(RepositoryAddress));
            ShowDownloadCommand = new RelayCommand(() => OpenAddress(LauncherInformation.DownloadAddress));
            ShowBuildsCommand = new RelayCommand(() => OpenAddress(LauncherInformation.BuildsAddress));
            ShowIssuesCommand = new RelayCommand(() => OpenAddress(LauncherInformation.IssuesAddress));
            CopyRepositoryCommand = new RelayCommand(() => Copy(RepositoryAddress, "仓库地址已复制。"));
        }

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
                OnPropertyChanged(nameof(UpdateButtonText));
                CheckUpdateCommand?.NotifyCanExecuteChanged();
            }
        }
        public string UpdateButtonText => IsCheckingUpdate ? "正在检查…" : "检查启动器更新";
        public IAsyncRelayCommand CheckUpdateCommand { get; }
        public RelayCommand CopyHashCommand { get; }
        public RelayCommand ShowChangelogCommand { get; }
        public RelayCommand ShowLicensesCommand { get; }
        public RelayCommand ShowThanksCommand { get; }
        public RelayCommand ShowRepositoryCommand { get; }
        public RelayCommand ShowDownloadCommand { get; }
        public RelayCommand ShowBuildsCommand { get; }
        public RelayCommand ShowIssuesCommand { get; }
        public RelayCommand CopyRepositoryCommand { get; }

        private async Task CheckUpdateAsync()
        {
            IsCheckingUpdate = true;
            Feedback = string.Empty;
            UpdateError = string.Empty;
            UpdateStatus = "正在检查更新…";
            try
            {
                await Task.Yield();
                var result = await _checkUpdate();
                IsCheckingUpdate = false;
                UpdateStatus = result.StatusText;
                _interaction.ShowUpdate(result);
            }
            catch (Exception error)
            {
                UpdateStatus = "检查更新失败；不影响本地启动。";
                UpdateError = error.Message;
                Feedback = UpdateError;
            }
            finally { IsCheckingUpdate = false; }
        }

        private void Copy(string text, string confirmation) => Run(() => { _interaction.CopyText(text); Feedback = confirmation; });
        private void ShowDocument(string title, string resource) => Run(() => _interaction.ShowMarkdown(title, LauncherInformation.ReadDocument(resource)));
        private void OpenAddress(string address) => Run(() => _interaction.OpenAddress(address));
        private void Run(Action action)
        {
            try { action(); }
            catch (Exception error) { Feedback = "操作失败：" + error.Message; }
        }
    }
}
