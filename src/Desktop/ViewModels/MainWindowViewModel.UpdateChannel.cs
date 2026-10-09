using System;
using System.ComponentModel;
using System.Windows.Threading;
using T7.Rekindle.Desktop.Services;

namespace T7.Rekindle.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel
    {
        private UpdateChannel _updateChannel;
        public bool CanChangeUpdateChannel => !_disposed && !About.IsCheckingUpdate && !About.IsUpdating;
        public UpdateChannel SelectedUpdateChannel
        {
            get => _updateChannel;
            set
            {
                if (_updateChannel == value) return;
                if (!CanChangeUpdateChannel) { RestoreUpdateChannelSelection(); return; }
                if (value == UpdateChannel.Preview)
                {
                    var confirmed = _interaction.Confirm("切换到预览版？",
                        "预览版包含未经充分验证的更改，可能出现崩溃、功能异常或兼容性问题。建议先备份重要配置与游戏数据。\n确认后将保存预览渠道并立即检查更新。",
                        "切换到预览版");
                    // 弹窗打开期间，后台可能开始检查更新。
                    if (!confirmed || !CanChangeUpdateChannel) { RestoreUpdateChannelSelection(); return; }
                }
                PreferenceError = string.Empty;
                try
                {
                    _settings.SaveUpdateChannel(value);
                }
                catch (Exception error)
                {
                    _log.Error("保存更新渠道失败", error);
                    PreferenceError = "更新渠道未保存，请重试";
                    RestoreUpdateChannelSelection();
                    return;
                }
                _updateChannel = value;
                About.ResetUpdateChannel(value);
                foreach (var identity in _announcedUpdateVersions) Notices.Resolve("update.available." + identity);
                Notices.Resolve("update.failed");
                Notices.Resolve("暂无正式发布版本");
                Notices.Resolve("暂无预览构建");
                OnPropertyChanged();
                TriggerUpdateCheck();
                var channelName = value == UpdateChannel.Preview ? "预览版" : "正式版";
                if (_interaction.Confirm("前往更新页？",
                    "更新渠道已切换为" + channelName + "。\n取消将留在当前页面，不影响渠道设置和更新检查。",
                    "前往更新页")) IsUpdateSelected = true;
            }
        }

        private void RestoreUpdateChannelSelection()
        {
            // 等待本次双向绑定写入结束，再恢复下拉框的实际渠道。
            Dispatcher.CurrentDispatcher.BeginInvoke(DispatcherPriority.DataBind,
                new Action(() => OnPropertyChanged(nameof(SelectedUpdateChannel))));
        }

        private void OnUpdateActivityChanged(object sender, PropertyChangedEventArgs args)
        {
            if (args.PropertyName == nameof(AboutViewModel.IsCheckingUpdate) || args.PropertyName == nameof(AboutViewModel.IsUpdating))
                OnPropertyChanged(nameof(CanChangeUpdateChannel));
        }
    }
}
