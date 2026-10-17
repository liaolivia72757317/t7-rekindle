using CommunityToolkit.Mvvm.Input;

namespace T7R.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel
    {
        public IRelayCommand ApplyAudioCommand => GameSettings.ApplyAudioCommand;
        public IRelayCommand ResetAudioCommand => GameSettings.ResetAudioCommand;
        public IRelayCommand RevertAudioCommand => GameSettings.RevertAudioCommand;
        public bool CanEditAudio => GameSettings.CanEditAudio;
        public bool HasAudioChanges => GameSettings.HasAudioChanges;
        public bool CanApplyAudio => GameSettings.CanApplyAudio;
        public string AudioStatus => GameSettings.AudioStatus;
        public string AudioError => GameSettings.AudioError;
        public bool AudioMusicEnabled { get => GameSettings.AudioMusicEnabled; set => GameSettings.AudioMusicEnabled = value; }
        public bool AudioEffectsEnabled { get => GameSettings.AudioEffectsEnabled; set => GameSettings.AudioEffectsEnabled = value; }
        public double AudioMusicVolume { get => GameSettings.AudioMusicVolume; set => GameSettings.AudioMusicVolume = value; }
        public double AudioEffectsVolume { get => GameSettings.AudioEffectsVolume; set => GameSettings.AudioEffectsVolume = value; }
        internal void RefreshAudio() => GameSettings.RefreshAudio();
    }
}
