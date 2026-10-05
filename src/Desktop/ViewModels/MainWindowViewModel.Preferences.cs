using System;
using T7.Rekindle.Desktop.Services;

namespace T7.Rekindle.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel
    {
        private bool _minimizeToTray;
        private bool _startWithWindows;
        private bool _skipStartupAnimation;
        private string _preferenceError = string.Empty;
        private string _gameSettingsError = string.Empty;
        internal Action<bool> RegisterStartup { get; set; } = WindowsStartupService.SetEnabled;
        public string PreferenceError { get => _preferenceError; private set => SetProperty(ref _preferenceError, value); }
        public string GameSettingsError { get => _gameSettingsError; private set => SetProperty(ref _gameSettingsError, value); }
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
                    _settings.Save(settings);
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
                _settings.Save(settings);
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
