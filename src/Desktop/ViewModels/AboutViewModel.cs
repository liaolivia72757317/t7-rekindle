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
        private bool _isCheckingUpdate;

        public AboutViewModel(IDesktopInteraction interaction)
            : this(interaction, LauncherInformation.CheckSampleUpdateAsync) { }

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
            ShowRepositoryCommand = new RelayCommand(() => ShowAddress("GitHub", RepositoryAddress));
            CopyRepositoryCommand = new RelayCommand(() => Copy(RepositoryAddress, "仓库地址已复制。"));
        }

        public string Version => LauncherInformation.Version;
        public string ShortHash => LauncherInformation.ShortHash;
        public string HashDescription => LauncherInformation.CommitHash.Length == 0 ? "构建未记录 Git 提交" : LauncherInformation.CommitHash;
        public string RepositoryAddress => LauncherInformation.RepositoryAddress;
        public string RepositoryAddressDisplay => string.IsNullOrWhiteSpace(RepositoryAddress) ? "项目仓库地址待配置" : RepositoryAddress;
        public bool HasRepositoryAddress => !string.IsNullOrWhiteSpace(RepositoryAddress);
        public string Feedback
        {
            get => _feedback;
            private set { if (SetProperty(ref _feedback, value)) OnPropertyChanged(nameof(HasFeedback)); }
        }
        public bool HasFeedback => Feedback.Length != 0;
        public string UpdateStatus { get => _updateStatus; private set => SetProperty(ref _updateStatus, value); }
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
        public RelayCommand CopyRepositoryCommand { get; }

        private async Task CheckUpdateAsync()
        {
            IsCheckingUpdate = true;
            UpdateStatus = "正在检查更新…";
            try
            {
                await Task.Yield();
                var result = await _checkUpdate();
                IsCheckingUpdate = false;
                UpdateStatus = result.IsNewVersion ? "发现新版本" : "已是最新版本";
                _interaction.ShowUpdate(result);
            }
            catch (Exception error)
            {
                UpdateStatus = "检查更新失败；不影响本地启动。";
                Feedback = error.Message;
            }
            finally { IsCheckingUpdate = false; }
        }

        private void Copy(string text, string confirmation) => Run(() => { _interaction.CopyText(text); Feedback = confirmation; });
        private void ShowDocument(string title, string resource) => Run(() => _interaction.ShowMarkdown(title, LauncherInformation.ReadDocument(resource)));
        private void ShowAddress(string title, string address) => Run(() => _interaction.ShowText(title, address));
        private void Run(Action action)
        {
            try { action(); }
            catch (Exception error) { Feedback = "操作失败：" + error.Message; }
        }
    }
}
