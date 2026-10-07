using System;
using System.ComponentModel;
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
                if (!CanChangeUpdateChannel) { OnPropertyChanged(); return; }
                PreferenceError = string.Empty;
                try
                {
                    _settings.SaveUpdateChannel(value);
                }
                catch (Exception error)
                {
                    _log.Error("保存更新渠道失败", error);
                    PreferenceError = "更新渠道未保存，请重试";
                    OnPropertyChanged();
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
            }
        }

        private void OnUpdateActivityChanged(object sender, PropertyChangedEventArgs args)
        {
            if (args.PropertyName == nameof(AboutViewModel.IsCheckingUpdate) || args.PropertyName == nameof(AboutViewModel.IsUpdating))
                OnPropertyChanged(nameof(CanChangeUpdateChannel));
        }
    }
}
