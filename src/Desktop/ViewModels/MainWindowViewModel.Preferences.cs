using System;
using T7R.Core;
using T7R.Desktop.Services;

namespace T7R.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel
    {
        private bool _minimizeToTray;
        private bool _startWithWindows;
        private bool _skipStartupAnimation;
        private DiagnosticLogLevel _selectedLogLevel;
        private string _preferenceError = string.Empty;
        private string _gameSettingsError = string.Empty;
        internal Action<bool> RegisterStartup { get; set; } = WindowsStartupService.SetEnabled;
        public string PreferenceError { get => _preferenceError; private set => SetProperty(ref _preferenceError, value); }
        public string GameSettingsError { get => _gameSettingsError; private set => SetProperty(ref _gameSettingsError, value); }
        public bool DarkTheme
        {
            get => _darkTheme;
            set
            {
                if (_disposed || _darkTheme == value) return;
                PreferenceError = string.Empty;
                try
                {
                    var settings = CreateSettings(_savedDirectory, _savedName);
                    settings.DarkTheme = value;
                    _settings.SaveLauncherSettings(settings);
                    _darkTheme = value;
                    Notices.Resolve("preferences.save-failed");
                }
                catch (Exception error)
                {
                    _log.Error("保存主题设置失败", error);
                    PreferenceError = "主题设置未保存，请重试";
                    if (!IsSettingsSelected || SettingsTabIndex != 1)
                        Notices.Publish("preferences.save-failed", PreferenceError, NoticeSeverity.Error, "查看设置",
                            () => { IsSettingsSelected = true; SettingsTabIndex = 1; });
                }
                System.Windows.Threading.Dispatcher.CurrentDispatcher.BeginInvoke(
                    System.Windows.Threading.DispatcherPriority.DataBind, new Action(() => OnPropertyChanged(nameof(DarkTheme))));
            }
        }

        public bool MinimizeToTray
        {
            get => _minimizeToTray;
            set => SavePreference(value, false);
        }
        public bool StartWithWindows
        {
            get => _startWithWindows;
            set => SavePreference(value, true);
        }

        public bool CanChangeLogLevel => !_disposed && _updateChannel != UpdateChannel.Preview;
        public DiagnosticLogLevel SelectedLogLevel
        {
            get => _selectedLogLevel;
            set
            {
                if (_disposed || _selectedLogLevel == value) return;
                if (!CanChangeLogLevel) { RestoreLogLevelSelection(); return; }
                if (!DiagnosticLogLevels.IsValid(value)) throw new ArgumentOutOfRangeException(nameof(value));
                var previous = _selectedLogLevel;
                var applied = false;
                PreferenceError = string.Empty;
                try
                {
                    var settings = CreateSettings(_savedDirectory, _savedName);
                    settings.LogLevel = DiagnosticLogLevels.Name(value);
                    _bridge.SetLogLevel(value);
                    applied = true;
                    LogService.SetMinimumLevel(value);
                    _settings.SaveLauncherSettings(settings);
                    _selectedLogLevel = value;
                    Notices.Resolve("preferences.save-failed");
                }
                catch (Exception error)
                {
                    PreferenceError = "日志等级未保存，请重试";
                    if (applied) RestoreLogLevel(previous);
                    _log.Error("保存日志等级失败", error);
                    if (!IsSettingsSelected || SettingsTabIndex != 1)
                        Notices.Publish("preferences.save-failed", PreferenceError, NoticeSeverity.Error, "查看设置",
                            () => { IsSettingsSelected = true; SettingsTabIndex = 1; });
                }
                RestoreLogLevelSelection();
            }
        }

        private void RestoreLogLevelSelection() => System.Windows.Threading.Dispatcher.CurrentDispatcher.BeginInvoke(
            System.Windows.Threading.DispatcherPriority.DataBind, new Action(() => OnPropertyChanged(nameof(SelectedLogLevel))));

        private void RestoreLogLevel(DiagnosticLogLevel previous)
        {
            // Restore each runtime sink independently when persistence fails.
            try { _bridge.SetLogLevel(previous); }
            catch (Exception restoreError) { ReportLogLevelRestoreFailure(restoreError); }
            try { LogService.SetMinimumLevel(previous); }
            catch (Exception restoreError) { ReportLogLevelRestoreFailure(restoreError); }
        }

        private void ReportLogLevelRestoreFailure(Exception error)
        {
            _log.Error("恢复日志等级失败", error);
            PreferenceError = "日志等级恢复失败，请重启启动器后重试";
        }

        public bool SkipStartupAnimation
        {
            get => _skipStartupAnimation;
            set
            {
                if (_skipStartupAnimation == value) return;
                GameSettingsError = string.Empty;
                try
                {
                    var settings = CreateSettings(_savedDirectory, _savedName);
                    settings.SkipStartupAnimation = value;
                    _settings.SaveLauncherSettings(settings);
                    _skipStartupAnimation = value;
                    Notices.Resolve("game-settings.save-failed");
                }
                catch (Exception error)
                {
                    _log.Error("保存游戏设置失败", error);
                    GameSettingsError = "游戏设置未保存，请重试";
                    if (!IsSettingsSelected || SettingsTabIndex != 2)
                        Notices.Publish("game-settings.save-failed", GameSettingsError, NoticeSeverity.Error, "查看设置",
                            () => { IsSettingsSelected = true; SettingsTabIndex = 2; });
                }
                OnPropertyChanged();
            }
        }

        private void SavePreference(bool value, bool isStartup)
        {
            var previous = isStartup ? _startWithWindows : _minimizeToTray;
            if (previous == value) return;
            var registered = false;
            PreferenceError = string.Empty;
            try
            {
                if (isStartup) { RegisterStartup(value); registered = true; }
                var settings = CreateSettings(_savedDirectory, _savedName);
                if (isStartup) settings.StartWithWindows = value;
                else settings.MinimizeToTray = value;
                _settings.SaveLauncherSettings(settings);
                Notices.Resolve("preferences.save-failed");
                if (isStartup) _startWithWindows = value;
                else _minimizeToTray = value;
            }
            catch (Exception error)
            {
                _log.Error("保存启动器设置失败", error);
                PreferenceError = "启动器设置未保存，请重试";
                if (registered)
                {
                    try { RegisterStartup(previous); }
                    catch (Exception restoreError)
                    {
                        _log.Error("恢复登录启动项失败", restoreError);
                        _startWithWindows = value;
                        PreferenceError = "登录启动项已改变，但设置未保存；请重新切换并检查系统启动项。";
                    }
                }
                if (!IsSettingsSelected || SettingsTabIndex != 1)
                    Notices.Publish("preferences.save-failed", PreferenceError, NoticeSeverity.Error, "查看设置",
                        () => { IsSettingsSelected = true; SettingsTabIndex = 1; });
            }
            OnPropertyChanged(isStartup ? nameof(StartWithWindows) : nameof(MinimizeToTray));
        }
    }
}
