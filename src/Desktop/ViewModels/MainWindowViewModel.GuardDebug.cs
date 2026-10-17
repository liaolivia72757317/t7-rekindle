using System;

namespace T7R.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel
    {
        private bool _showGuardDebug;
        public bool ShowGuardDebug
        {
            get => _showGuardDebug;
            set
            {
                if (_disposed || _showGuardDebug == value) return;
                var previous = _showGuardDebug;
                var applied = false;
                PreferenceError = string.Empty;
                try
                {
                    var settings = CreateSettings(_savedDirectory, _savedName);
                    settings.ShowGuardDebug = value;
                    _bridge.SetGuardDebug(value);
                    applied = true;
                    _settings.SaveLauncherSettings(settings);
                    _showGuardDebug = value;
                    Notices.Resolve("preferences.save-failed");
                }
                catch (Exception error)
                {
                    PreferenceError = "格挡调试设置未保存，请重试";
                    if (applied)
                    {
                        try { _bridge.SetGuardDebug(previous); }
                        catch (Exception restoreError)
                        {
                            _log.Error("恢复格挡调试显示失败", restoreError);
                            PreferenceError = "格挡调试显示恢复失败，请重启启动器后重试";
                        }
                    }
                    _log.Error("保存格挡调试设置失败", error);
                    if (!IsSettingsSelected || SettingsTabIndex != 1)
                        Notices.Publish("preferences.save-failed", PreferenceError, NoticeSeverity.Error, "查看设置",
                            () => { IsSettingsSelected = true; SettingsTabIndex = 1; });
                }
                System.Windows.Threading.Dispatcher.CurrentDispatcher.BeginInvoke(
                    System.Windows.Threading.DispatcherPriority.DataBind, new Action(() => OnPropertyChanged(nameof(ShowGuardDebug))));
            }
        }
    }
}
