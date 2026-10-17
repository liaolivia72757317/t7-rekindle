using System;
using System.ComponentModel;
using System.Windows.Threading;
using T7R.Core;
using T7R.Desktop.Services;

namespace T7R.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel
    {
        private UpdateChannel _updateChannel;
        private readonly PreviewAccessService _previewAccess;
        private readonly Func<PreviewAccessViewModel, bool> _requestPreviewAccess;
        private bool _previewExpiredPending;
        private bool _authorizationPromptOpen;
        private bool _channelTransitionActive;
        private string _previewAccessStatus;
        private bool CanStartPreviewOperation => !_previewExpiredPending && _previewAccess.IsAuthorized;
        public string PreviewAccessStatus => _previewAccessStatus;
        public bool CanChangeUpdateChannel => !_disposed && !_authorizationPromptOpen && !_channelTransitionActive
            && !About.IsCheckingUpdate && !About.IsUpdating;
        public UpdateChannel SelectedUpdateChannel
        {
            get => _updateChannel;
            set
            {
                if (_updateChannel == value) return;
                if (!CanChangeUpdateChannel) { RestoreUpdateChannelSelection(); return; }
                if (value == UpdateChannel.Preview)
                {
                    bool confirmed;
                    _authorizationPromptOpen = true;
                    OnPropertyChanged(nameof(CanChangeUpdateChannel));
                    try
                    {
                        confirmed = _previewAccess.IsAuthorized
                            ? _interaction.Confirm("切换到预览版？", PreviewAccessViewModel.RiskDescription + "\n"
                                + _previewAccess.StatusText + "\n确认后将保存预览渠道并立即检查更新。", "切换到预览版")
                            : _requestPreviewAccess(new PreviewAccessViewModel(_previewAccess, _interaction.CopyText));
                    }
                    finally { _authorizationPromptOpen = false; OnPropertyChanged(nameof(CanChangeUpdateChannel)); }
                    // 弹窗打开期间，后台可能开始检查更新。
                    RefreshPreviewAccessStatus();
                    if (!confirmed || !_previewAccess.IsAuthorized || !CanChangeUpdateChannel) { RestoreUpdateChannelSelection(); return; }
                }
                PreferenceError = string.Empty;
                var previousLogLevel = _selectedLogLevel;
                var logLevelApplied = false;
                try
                {
                    if (value == UpdateChannel.Preview && previousLogLevel != DiagnosticLogLevel.Debug)
                    {
                        _bridge.SetLogLevel(DiagnosticLogLevel.Debug);
                        logLevelApplied = true;
                        LogService.SetMinimumLevel(DiagnosticLogLevel.Debug);
                    }
                    _settings.SaveUpdateChannel(value);
                }
                catch (Exception error)
                {
                    PreferenceError = "更新渠道未保存，请重试";
                    if (logLevelApplied) RestoreLogLevel(previousLogLevel);
                    _log.Error("保存更新渠道失败", error);
                    RestoreUpdateChannelSelection();
                    return;
                }
                if (value == UpdateChannel.Preview) _selectedLogLevel = DiagnosticLogLevel.Debug;
                ApplyUpdateChannel(value);
                TriggerUpdateCheck();
                var channelName = value == UpdateChannel.Preview ? "预览版" : "正式版";
                if (_interaction.Confirm("前往更新页？",
                    "更新渠道已切换为" + channelName + "。\n取消将留在当前页面，不影响渠道设置和更新检查。",
                    "前往更新页")) IsUpdateSelected = true;
            }
        }

        private void ApplyUpdateChannel(UpdateChannel channel)
        {
            _channelTransitionActive = true;
            try
            {
                _updateChannel = channel;
                _previewExpiredPending = false;
                About.ResetUpdateChannel(channel);
                foreach (var identity in _updateChecks.AnnouncedIdentities) Notices.Resolve("update.available." + identity);
                Notices.Resolve("update.failed");
                Notices.Resolve("暂无正式发布版本");
                Notices.Resolve("暂无预览构建");
                OnPropertyChanged(nameof(SelectedUpdateChannel));
                OnPropertyChanged(nameof(SelectedLogLevel));
                OnPropertyChanged(nameof(CanChangeLogLevel));
                RefreshPreviewAccessStatus();
                About.RefreshAccess();
            }
            finally { _channelTransitionActive = false; OnPropertyChanged(nameof(CanChangeUpdateChannel)); }
        }

        internal void CheckPreviewAccess()
        {
            if (_disposed || _channelTransitionActive || About == null) return;
            if (_updateChannel == UpdateChannel.Preview && !_previewAccess.IsAuthorized) _previewExpiredPending = true;
            RefreshPreviewAccessStatus();
            if (!_previewExpiredPending) return;
            About.RefreshAccess();
            if (About.IsCheckingUpdate || About.IsUpdating || _authorizationPromptOpen) return;
            try { _settings.ResetUpdateChannelToStable(); }
            catch (Exception)
            {
                _log.Warn("预览授权失效后的正式渠道设置未保存。");
                PreferenceError = "当前已使用正式渠道，但设置未保存，下次启动将重试。";
            }
            ApplyUpdateChannel(UpdateChannel.Stable);
            Notices.Publish("preview.access.expired", "预览授权已失效，已切换为正式渠道；游戏运行不受影响。", NoticeSeverity.Warning);
            TriggerUpdateCheck();
        }

        private void RefreshPreviewAccessStatus()
        {
            var text = _previewExpiredPending ? "预览授权已失效，等待当前更新操作结束后切回正式渠道。" : _previewAccess.StatusText;
            if (_previewAccessStatus == text) return;
            _previewAccessStatus = text;
            OnPropertyChanged(nameof(PreviewAccessStatus));
        }

        private void QueuePreviewAccessCheck() => Dispatcher.CurrentDispatcher.BeginInvoke(DispatcherPriority.Background,
            new Action(CheckPreviewAccess));

        private void RestoreUpdateChannelSelection()
        {
            // 等待本次双向绑定写入结束，再恢复下拉框的实际渠道。
            Dispatcher.CurrentDispatcher.BeginInvoke(DispatcherPriority.DataBind,
                new Action(() => OnPropertyChanged(nameof(SelectedUpdateChannel))));
        }

        private void OnUpdateActivityChanged(object sender, PropertyChangedEventArgs args)
        {
            if (args.PropertyName == nameof(AboutViewModel.IsCheckingUpdate) || args.PropertyName == nameof(AboutViewModel.IsUpdating))
            {
                OnPropertyChanged(nameof(CanChangeUpdateChannel));
                if (_updateChannel == UpdateChannel.Preview && !CanStartPreviewOperation) QueuePreviewAccessCheck();
            }
        }
    }
}
