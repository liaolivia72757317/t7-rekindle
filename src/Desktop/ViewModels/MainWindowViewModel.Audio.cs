using System;
using CommunityToolkit.Mvvm.Input;
using T7.Rekindle.Core;

namespace T7.Rekindle.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel
    {
        private AudioSettingsSnapshot _audioSnapshot;
        private AudioSyncState _audioState;
        private AudioSettingsValues _submittedAudio;
        private bool _readingAudio, _audioMusicEnabled = true, _audioEffectsEnabled = true;
        private double _audioMusicVolume = 100, _audioEffectsVolume = 100;
        private string _audioStatus = "正在读取声音设置…", _audioError = string.Empty;
        public IRelayCommand ApplyAudioCommand { get; private set; }
        public IRelayCommand ResetAudioCommand { get; private set; }
        public IRelayCommand RevertAudioCommand { get; private set; }
        public bool CanEditAudio => CanEditGameSettings && (_audioState == AudioSyncState.Ready || _audioState == AudioSyncState.Conflict);
        public bool HasAudioChanges => _audioSnapshot?.Values != null && !_audioSnapshot.Values.Equals(AudioDraft());
        public bool CanApplyAudio => CanEditAudio && HasAudioChanges && AudioDraft().IsValid;
        public string AudioStatus { get => _audioStatus; private set => SetProperty(ref _audioStatus, value); }
        public string AudioError { get => _audioError; private set => SetProperty(ref _audioError, value); }
        public bool AudioMusicEnabled { get => _audioMusicEnabled; set { if (SetProperty(ref _audioMusicEnabled, value)) AudioEdited(); } }
        public bool AudioEffectsEnabled { get => _audioEffectsEnabled; set { if (SetProperty(ref _audioEffectsEnabled, value)) AudioEdited(); } }
        public double AudioMusicVolume { get => _audioMusicVolume; set { if (SetProperty(ref _audioMusicVolume, value)) AudioEdited(); } }
        public double AudioEffectsVolume { get => _audioEffectsVolume; set { if (SetProperty(ref _audioEffectsVolume, value)) AudioEdited(); } }
        private AudioSettingsValues AudioDraft() => new AudioSettingsValues(!AudioMusicEnabled, (float)(AudioMusicVolume / 100),
            !AudioEffectsEnabled, (float)(AudioEffectsVolume / 100));
        private void InitializeAudio()
        {
            ApplyAudioCommand = new RelayCommand(ApplyAudio, () => CanApplyAudio);
            ResetAudioCommand = new RelayCommand(() => { SetAudioDraft(new AudioSettingsValues()); AudioEdited(); }, () => CanEditAudio);
            RevertAudioCommand = new RelayCommand(() =>
            {
                SetAudioDraft(_audioSnapshot.Values); AudioError = string.Empty;
                AudioStatus = _gameSettingsMode == GameSettingsMode.File ? "已还原为配置文件中的声音设置。" : "已还原为游戏当前声音设置。"; NotifyAudio();
            }, () => CanEditAudio && HasAudioChanges);
        }
        private void AudioEdited()
        {
            if (_readingAudio) return;
            AudioError = string.Empty;
            AudioStatus = _gameSettingsMode == GameSettingsMode.File
                ? (HasAudioChanges ? "有未保存的修改；保存后下次启动游戏生效。" : "与配置文件中的声音设置一致。")
                : (HasAudioChanges ? "有未保存的修改；保存后立即应用到游戏。" : "与游戏声音设置一致。");
            NotifyAudio();
        }
        private void SetAudioDraft(AudioSettingsValues values)
        {
            _readingAudio = true;
            try
            {
                AudioMusicEnabled = !values.MusicMuted; AudioEffectsEnabled = !values.EffectsMuted;
                AudioMusicVolume = (double)values.MusicVolume * 100; AudioEffectsVolume = (double)values.EffectsVolume * 100;
            }
            finally { _readingAudio = false; }
        }
        private void ApplyAudio()
        {
            if (!CanApplyAudio) return;
            try
            {
                var values = AudioDraft();
                if (_gameSettingsMode == GameSettingsMode.File) ApplyFileAudio(values);
                else
                {
                    _bridge.ApplyAudioSettings(_audioSnapshot.Revision, values);
                    _submittedAudio = values; _audioState = AudioSyncState.Applying;
                    AudioError = string.Empty; AudioStatus = "正在应用，等待游戏确认…";
                }
            }
            catch (Exception error) { AudioError = error.Message; _log.Error("提交声音设置失败", error); }
            NotifyAudio();
        }
        internal void RefreshAudio()
        {
            try
            {
                var next = _snapshot.State == SessionState.Running
                    ? _bridge.GetAudioSettings() : new AudioSettingsSnapshot(0, AudioSyncState.Unavailable, null);
                var previousState = _audioState; _audioState = next.State;
                if (next.State == AudioSyncState.Unavailable)
                {
                    _audioSnapshot = null; _submittedAudio = null; AudioError = string.Empty;
                    AudioStatus = "游戏已启动，正在等待声音设置就绪…";
                }
                else if (next.State == AudioSyncState.Failed)
                {
                    _submittedAudio = null; AudioError = "声音设置同步中断，请重启游戏后重试；详情见日志。";
                }
                else if (next.State != AudioSyncState.Applying && next.Values != null)
                {
                    var changed = _audioSnapshot == null || next.Revision != _audioSnapshot.Revision;
                    var completed = _submittedAudio != null;
                    if (changed || completed || (next.State == AudioSyncState.Conflict && previousState != next.State))
                    {
                        SetAudioDraft(next.Values);
                        AudioStatus = next.State == AudioSyncState.Conflict ? "游戏内声音设置已变化，已同步最新值；本次修改未覆盖游戏。"
                            : completed ? (_submittedAudio.Equals(next.Values) ? "声音设置已应用并回读确认。" : "已同步游戏实际采用的声音设置。")
                            : _audioSnapshot == null ? "已读取游戏声音设置；游戏内的修改会自动同步。" : "已同步游戏内的声音修改。";
                        AudioError = string.Empty;
                    }
                    _audioSnapshot = next; _submittedAudio = null;
                }
            }
            catch (Exception error)
            {
                if (_audioState != AudioSyncState.Failed) _log.Error("读取声音设置失败", error);
                _audioState = AudioSyncState.Failed; AudioError = "读取声音设置失败，请查看日志。";
            }
            NotifyAudio();
        }
        private void NotifyAudio()
        {
            OnPropertyChanged(nameof(CanEditAudio)); OnPropertyChanged(nameof(CanApplyAudio)); OnPropertyChanged(nameof(HasAudioChanges));
            ApplyAudioCommand?.NotifyCanExecuteChanged(); ResetAudioCommand?.NotifyCanExecuteChanged(); RevertAudioCommand?.NotifyCanExecuteChanged();
        }
    }
}
