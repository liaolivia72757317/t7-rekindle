using System;
using System.Collections.Generic;
using System.Linq;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop.Services;

namespace T7.Rekindle.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel
    {
        private string _selectedOutputDeviceId = string.Empty;
        private Guid _activeOutputDevice;
        private string _outputDeviceError = string.Empty;
        private bool _outputDeviceConfirmed;
        public IReadOnlyList<OutputDeviceOption> OutputDevices { get; private set; }
        public string OutputDeviceError { get => _outputDeviceError; private set => SetProperty(ref _outputDeviceError, value); }
        public bool CanChangeOutputDevice => !_operationActive && !_disposed
            && (_snapshot.State == SessionState.Idle || _snapshot.State == SessionState.Running
                || (_snapshot.State == SessionState.Failed && _snapshot.CleanupComplete));
        public string OutputDeviceStatus => _snapshot.State != SessionState.Running
            ? "自动保存，下次启动游戏生效。"
            : ParseOutputDevice(SelectedOutputDeviceId) != _activeOutputDevice
                ? "已保存，将于下次启动生效；" + (_outputDeviceConfirmed ? "本次使用：" : "本次启动选择：") + OutputDeviceName(_activeOutputDevice)
                : _outputDeviceConfirmed ? "本次输出设备已确认：" + OutputDeviceName(_activeOutputDevice)
                : _graphicsState == GraphicsSyncState.Failed ? "输出设备尚未确认，请查看日志。" : "等待游戏确认输出设备…";
        public string SelectedOutputDeviceId
        {
            get => _selectedOutputDeviceId;
            set
            {
                if (value == null || value == _selectedOutputDeviceId || !CanChangeOutputDevice) return;
                if (!OutputDevices.Any(device => device.Id == value)) return;
                try
                {
                    var settings = CreateSettings(_savedDirectory, _savedName);
                    settings.OutputDeviceId = value;
                    _settings.Save(settings);
                    _selectedOutputDeviceId = value;
                    OutputDeviceError = string.Empty;
                    if (_gameSettingsMode != GameSettingsMode.Memory) RefreshGraphicsResolutions();
                }
                catch (Exception error)
                {
                    _log.Error("保存输出设备失败", error);
                    OutputDeviceError = "输出设备未保存，请重试。";
                }
                OnPropertyChanged();
                OnPropertyChanged(nameof(OutputDeviceStatus));
            }
        }

        private void InitializeOutputDevices(Func<IReadOnlyList<OutputDeviceOption>> readDevices)
        {
            var devices = new List<OutputDeviceOption> { new OutputDeviceOption(Guid.Empty, "系统默认") };
            try { devices.AddRange(readDevices()); }
            catch (Exception error)
            {
                _log.Error("读取输出设备失败", error);
                OutputDeviceError = "输出设备列表读取失败，请检查显卡驱动后重新打开启动器。";
            }
            if (!devices.Any(device => device.Id == _selectedOutputDeviceId))
            {
                devices.Add(new OutputDeviceOption(ParseOutputDevice(_selectedOutputDeviceId), "已保存的输出设备（当前未检测到）"));
                OutputDeviceError = "所选输出设备未连接或驱动已变化，请重新选择。";
            }
            OutputDevices = devices.ToArray();
        }

        private static Guid ParseOutputDevice(string id) => string.IsNullOrEmpty(id) ? Guid.Empty : Guid.Parse(id);
        private string OutputDeviceName(Guid id) => OutputDevices?.FirstOrDefault(device => ParseOutputDevice(device.Id) == id)?.Name ?? "未检测到的设备";
        private void NotifyOutputDevice()
        {
            if (_snapshot.State != SessionState.Running) _outputDeviceConfirmed = false;
            else if (_graphicsState == GraphicsSyncState.Ready) _outputDeviceConfirmed = true;
            OnPropertyChanged(nameof(CanChangeOutputDevice));
            OnPropertyChanged(nameof(OutputDeviceStatus));
        }
    }
}
