namespace T7R.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel
    {
        public GameSettingsViewModel GameSettings { get; }
        private void OnGameSettingsChanged(object sender, System.ComponentModel.PropertyChangedEventArgs args) =>
            OnPropertyChanged(args.PropertyName);
        internal void RefreshGameSettings(bool forceFileRead = false) => GameSettings.RefreshGameSettings(forceFileRead);
    }
}
