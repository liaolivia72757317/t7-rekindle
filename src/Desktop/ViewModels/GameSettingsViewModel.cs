using System;
using CommunityToolkit.Mvvm.ComponentModel;
using T7R.Core;
using T7R.Desktop.Services;

namespace T7R.Desktop.ViewModels
{
    internal sealed class GameSettingsContext
    {
        internal SessionSnapshot Session { get; }
        internal ClientDirectoryResult Directory { get; }
        internal bool Disposed { get; }
        internal bool OperationActive { get; }
        internal bool IsValidating { get; }
        internal bool DirectoryHasDraft { get; }
        internal GameSettingsContext(SessionSnapshot session, ClientDirectoryResult directory,
            bool disposed, bool operationActive, bool isValidating, bool directoryHasDraft)
        {
            Session = session; Directory = directory; Disposed = disposed;
            OperationActive = operationActive; IsValidating = isValidating; DirectoryHasDraft = directoryHasDraft;
        }
    }

    public sealed partial class GameSettingsViewModel : ObservableObject
    {
        private readonly INativeBridge _bridge;
        private readonly IClientEnvironment _clientEnvironment;
        private readonly LogService _log;
        private readonly Func<GameSettingsContext> _readContext;
        private readonly Action<string> _saveOutputDevice;
        private GameSettingsContext Context => _readContext();
        internal Guid SelectedDevice => ParseOutputDevice(SelectedOutputDeviceId);

        internal GameSettingsViewModel(INativeBridge bridge, IClientEnvironment clientEnvironment,
            LogService log, string outputDeviceId, Func<GameSettingsContext> readContext, Action<string> saveOutputDevice)
        {
            _bridge = bridge; _clientEnvironment = clientEnvironment; _log = log;
            _readContext = readContext; _saveOutputDevice = saveOutputDevice;
            var device = ParseOutputDevice(outputDeviceId);
            _selectedOutputDeviceId = device == Guid.Empty ? string.Empty : device.ToString("D");
            InitializeGraphics();
            InitializeOutputDevices(_clientEnvironment.ReadOutputDevices);
            InitializeAudio();
        }
        internal void PrepareLaunch(Guid outputDevice)
        {
            _activeOutputDevice = outputDevice;
            _outputDeviceConfirmed = false;
        }
        internal void RefreshAvailability() { NotifyGraphics(); NotifyAudio(); }
    }
}
