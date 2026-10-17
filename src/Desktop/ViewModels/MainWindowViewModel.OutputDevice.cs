using System.Collections.Generic;
using T7R.Desktop.Services;

namespace T7R.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel
    {
        public IReadOnlyList<OutputDeviceOption> OutputDevices => GameSettings.OutputDevices;
        public string OutputDeviceError => GameSettings.OutputDeviceError;
        public bool CanChangeOutputDevice => GameSettings.CanChangeOutputDevice;
        public string OutputDeviceStatus => GameSettings.OutputDeviceStatus;
        public string SelectedOutputDeviceId { get => GameSettings.SelectedOutputDeviceId; set => GameSettings.SelectedOutputDeviceId = value; }
    }
}
