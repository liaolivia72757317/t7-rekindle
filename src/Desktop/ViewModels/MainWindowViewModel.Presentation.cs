using System;
using System.Linq;
using CommunityToolkit.Mvvm.Input;
using T7.Rekindle.Core;

namespace T7.Rekindle.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel
    {
        private int _settingsTabIndex;
        public NoticeCenter Notices { get; } = new NoticeCenter();
        public RoomListViewModel Multiplayer { get; } = new RoomListViewModel();
        // Preserve the original page IDs for existing callers; order is defined by the sidebar.
        public bool IsMultiplayerSelected { get => SelectedPage == 3; set { if (value) SelectedPage = 3; } }
        public bool IsUpdateSelected { get => SelectedPage == 4; set { if (value) SelectedPage = 4; } }
        public int SettingsTabIndex { get => _settingsTabIndex; set => SetProperty(ref _settingsTabIndex, value); }
        public string SavedPlayerName => string.IsNullOrEmpty(_savedName) ? "未设置" : _savedName;
        public string SavedClientDirectory => string.IsNullOrEmpty(_savedDirectory) ? "未设置" : _savedDirectory;
        public string MainActionIcon => IsBusy ? "loader" : !_directoryResult.IsValid || HasPlayerNameError ? "settings" : "play";
        public string HomeStatusDetail => StatusText == "准备就绪" || _snapshot.State == SessionState.Running ? string.Empty : DiagnosticText;
        public bool IsManagedGameRunning => _snapshot.State == SessionState.Running;
        public string NameFieldError => ShowPlayerNameError ? PlayerNameError : _nameSaveError;
        public string DirectoryFieldError => HasDirectoryError ? _directoryResult.Message : _directorySaveError;
        public string DirectoryProgressText => _isValidating ? DirectoryMessage : string.Empty;
        public string AnnouncementTitle => "关于「铁骑·重燃」";
        public string AnnouncementSummary => "从大学时光里的热爱，到停服后的不舍，我开始尝试自己动手，让《刀锋铁骑》重新运行起来。"
            + "「铁骑·重燃」是一个独立、非营利的开源项目，目前仍在开发，将先推进本地人机对战，再逐步支持局域网联机。"
            + "想和大家聊聊它的起点，以及接下来的打算。";
        public RelayCommand ShowRecentNoticesCommand { get; }
        public RelayCommand ShowDiagnosticsCommand { get; }
        public RelayCommand ShowAnnouncementCommand { get; }
        public RelayCommand CopyDirectoryCommand { get; }

        private void ShowAnnouncement()
        {
            try { _interaction.ShowMarkdown(AnnouncementTitle, Services.LauncherInformation.ReadDocument("ANNOUNCEMENT.md")); }
            catch (Exception error) { ReportUiError("打开公告失败", error); }
        }

        private void ShowRecentNotices()
        {
            if (_interaction is Services.DesktopInteraction desktop) { desktop.ShowRecentNotices(Notices); return; }
            _interaction.ShowText("近期提示", Notices.History.Count == 0 ? "本次会话暂无提示。"
                : string.Join(Environment.NewLine + Environment.NewLine, Notices.History.Select(item => item.TimeText + "  " + item.Message)));
        }

        private void ShowDiagnostics()
        {
            if (_interaction is Services.DesktopInteraction desktop) { desktop.ShowDiagnostics(this); return; }
            _interaction.ShowText("启动诊断", "当前状态：" + StatusText + "\n\n"
                + DiagnosticText + "\n\n" + EndpointText + "\n\n" + NativeLogText);
        }

        private void CopyDirectory()
        {
            try { _interaction.CopyText(ClientDirectory); ShowNotice("游戏目录已复制", false); }
            catch (Exception error) { ReportUiError("复制游戏目录失败", error); }
        }
    }
}
