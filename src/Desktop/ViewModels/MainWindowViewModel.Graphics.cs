using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.Input;

namespace T7R.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel
    {
        public ObservableCollection<string> GraphicsResolutions => GameSettings.GraphicsResolutions;
        public string[] GraphicsSwooshLabels => GameSettings.GraphicsSwooshLabels;
        public IRelayCommand ApplyGraphicsCommand => GameSettings.ApplyGraphicsCommand;
        public IRelayCommand ResetGraphicsCommand => GameSettings.ResetGraphicsCommand;
        public IRelayCommand RevertGraphicsCommand => GameSettings.RevertGraphicsCommand;
        public bool CanEditGraphics => GameSettings.CanEditGraphics;
        public bool HasGraphicsChanges => GameSettings.HasGraphicsChanges;
        public bool CanApplyGraphics => GameSettings.CanApplyGraphics;
        public string GraphicsStatus => GameSettings.GraphicsStatus;
        public string GraphicsError => GameSettings.GraphicsError;
        public string GraphicsResolution { get => GameSettings.GraphicsResolution; set => GameSettings.GraphicsResolution = value; }
        public int GraphicsQuality { get => GameSettings.GraphicsQuality; set => GameSettings.GraphicsQuality = value; }
        public int GraphicsViewDistance { get => GameSettings.GraphicsViewDistance; set => GameSettings.GraphicsViewDistance = value; }
        public int GraphicsSwoosh { get => GameSettings.GraphicsSwoosh; set => GameSettings.GraphicsSwoosh = value; }
        public bool GraphicsFullScreen { get => GameSettings.GraphicsFullScreen; set => GameSettings.GraphicsFullScreen = value; }
        public bool GraphicsVerticalSync { get => GameSettings.GraphicsVerticalSync; set => GameSettings.GraphicsVerticalSync = value; }
        public bool GraphicsFog { get => GameSettings.GraphicsFog; set => GameSettings.GraphicsFog = value; }
        public bool GraphicsRagDoll { get => GameSettings.GraphicsRagDoll; set => GameSettings.GraphicsRagDoll = value; }
        public bool GraphicsFrameLimit { get => GameSettings.GraphicsFrameLimit; set => GameSettings.GraphicsFrameLimit = value; }
        internal void RefreshGraphics() => GameSettings.RefreshGraphics();
    }
}
