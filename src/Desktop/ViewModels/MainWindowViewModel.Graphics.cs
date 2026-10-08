using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using CommunityToolkit.Mvvm.Input;
using T7.Rekindle.Core;

namespace T7.Rekindle.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel
    {
        private GraphicsSettingsSnapshot _graphicsSnapshot;
        private GraphicsSyncState _graphicsState;
        private GraphicsSettingsValues _submittedGraphics;
        private readonly Func<Guid, IReadOnlyList<string>> _readGraphicsResolutions;
        private string[] _availableGraphicsResolutions = Array.Empty<string>();
        private string _graphicsResolutionsError = string.Empty;
        private bool _readingGraphics;
        private string _graphicsResolution = "1440x900";
        private int _graphicsQuality = 4, _graphicsViewDistance = 128, _graphicsSwoosh;
        private bool _graphicsFullScreen, _graphicsVerticalSync, _graphicsFog, _graphicsRagDoll = true, _graphicsFrameLimit = true;
        private string _graphicsStatus = "正在读取画面设置…";
        private string _graphicsError = string.Empty;

        public ObservableCollection<string> GraphicsResolutions { get; } = new ObservableCollection<string>();
        public string[] GraphicsSwooshLabels { get; } = { "全部开启", "仅开启本人", "关闭" };
        public IRelayCommand ApplyGraphicsCommand { get; private set; }
        public IRelayCommand ResetGraphicsCommand { get; private set; }
        public IRelayCommand RevertGraphicsCommand { get; private set; }
        public bool CanEditGraphics => CanEditGameSettings && (_graphicsState == GraphicsSyncState.Ready || _graphicsState == GraphicsSyncState.Conflict);
        public bool HasGraphicsChanges => _graphicsSnapshot?.Values != null && !_graphicsSnapshot.Values.Equals(GraphicsDraft());
        public bool CanApplyGraphics => CanEditGraphics && HasGraphicsChanges && GraphicsDraft()?.IsValid == true;
        public string GraphicsStatus { get => _graphicsStatus; private set => SetProperty(ref _graphicsStatus, value); }
        public string GraphicsError { get => _graphicsError.Length == 0 ? _graphicsResolutionsError : _graphicsError; private set => SetProperty(ref _graphicsError, value); }
        public string GraphicsResolution { get => _graphicsResolution; set { if (SetProperty(ref _graphicsResolution, value)) GraphicsEdited(); } }
        public int GraphicsQuality { get => _graphicsQuality; set { if (SetProperty(ref _graphicsQuality, value)) GraphicsEdited(); } }
        public int GraphicsViewDistance { get => _graphicsViewDistance; set { if (SetProperty(ref _graphicsViewDistance, value)) GraphicsEdited(); } }
        public int GraphicsSwoosh { get => _graphicsSwoosh; set { if (SetProperty(ref _graphicsSwoosh, value)) GraphicsEdited(); } }
        public bool GraphicsFullScreen { get => _graphicsFullScreen; set { if (SetProperty(ref _graphicsFullScreen, value)) GraphicsEdited(); } }
        public bool GraphicsVerticalSync { get => _graphicsVerticalSync; set { if (SetProperty(ref _graphicsVerticalSync, value)) GraphicsEdited(); } }
        public bool GraphicsFog { get => _graphicsFog; set { if (SetProperty(ref _graphicsFog, value)) GraphicsEdited(); } }
        public bool GraphicsRagDoll { get => _graphicsRagDoll; set { if (SetProperty(ref _graphicsRagDoll, value)) GraphicsEdited(); } }
        public bool GraphicsFrameLimit { get => _graphicsFrameLimit; set { if (SetProperty(ref _graphicsFrameLimit, value)) GraphicsEdited(); } }

        private void InitializeGraphics()
        {
            ApplyGraphicsCommand = new RelayCommand(ApplyGraphics, () => CanApplyGraphics);
            ResetGraphicsCommand = new RelayCommand(() => { SetGraphicsDraft(new GraphicsSettingsValues()); GraphicsEdited(); }, () => CanEditGraphics);
            RevertGraphicsCommand = new RelayCommand(() =>
            {
                SetGraphicsDraft(_graphicsSnapshot.Values); GraphicsError = string.Empty;
                GraphicsStatus = _gameSettingsMode == GameSettingsMode.File ? "已还原为配置文件中的画面设置。" : "已还原为游戏当前设置。"; NotifyGraphics();
            }, () => CanEditGraphics && HasGraphicsChanges);
        }
        private GraphicsSettingsValues GraphicsDraft()
        {
            var parts = (_graphicsResolution ?? string.Empty).Split('x');
            if (parts.Length != 2 || !uint.TryParse(parts[0], out var width) || !uint.TryParse(parts[1], out var height)) return null;
            return new GraphicsSettingsValues(width, height, GraphicsFullScreen, (uint)GraphicsQuality, GraphicsVerticalSync,
                GraphicsFog, (uint)GraphicsViewDistance, GraphicsRagDoll, GraphicsFrameLimit, (uint)GraphicsSwoosh);
        }
        private void GraphicsEdited()
        {
            if (_readingGraphics) return;
            GraphicsError = string.Empty;
            GraphicsStatus = _gameSettingsMode == GameSettingsMode.File
                ? (HasGraphicsChanges ? "有未保存的修改；保存后下次启动游戏生效。" : "与配置文件中的画面设置一致。")
                : (HasGraphicsChanges ? "有未保存的修改；保存后立即应用到游戏。" : "与游戏设置一致。");
            NotifyGraphics();
        }
        private void SetGraphicsDraft(GraphicsSettingsValues v)
        {
            _readingGraphics = true;
            try
            {
                SetGraphicsResolutionOptions(v.Resolution); GraphicsFullScreen = v.FullScreen;
                GraphicsQuality = (int)v.Quality; GraphicsVerticalSync = v.VerticalSync; GraphicsFog = v.Fog;
                GraphicsViewDistance = (int)v.ViewDistance; GraphicsRagDoll = v.RagDoll;
                GraphicsFrameLimit = v.FrameLimit; GraphicsSwoosh = (int)v.Swoosh;
            }
            finally { _readingGraphics = false; }
        }
        private void RefreshGraphicsResolutions()
        {
            try
            {
                var options = _readGraphicsResolutions(_gameSettingsMode == GameSettingsMode.Memory
                    ? _activeOutputDevice : ParseOutputDevice(SelectedOutputDeviceId));
                if (options.Count == 0) throw new InvalidOperationException("未读取到分辨率选项。");
                _availableGraphicsResolutions = options.ToArray();
                SetProperty(ref _graphicsResolutionsError, string.Empty, nameof(GraphicsError));
            }
            catch (Exception error)
            {
                _availableGraphicsResolutions = Array.Empty<string>();
                SetProperty(ref _graphicsResolutionsError, "分辨率列表读取失败，暂时仅保留当前值；其他设置仍可保存。", nameof(GraphicsError));
                _log.Error("读取游戏分辨率列表失败", error);
            }
            SetGraphicsResolutionOptions(GraphicsResolution);
        }
        private void SetGraphicsResolutionOptions(string resolution)
        {
            var options = _availableGraphicsResolutions.Contains(resolution)
                ? _availableGraphicsResolutions : _availableGraphicsResolutions.Concat(new[] { resolution }).ToArray();
            var reading = _readingGraphics;
            _readingGraphics = true;
            try
            {
                if (!GraphicsResolutions.SequenceEqual(options))
                {
                    GraphicsResolutions.Clear();
                    foreach (var option in options) GraphicsResolutions.Add(option);
                }
                GraphicsResolution = resolution;
            }
            finally { _readingGraphics = reading; }
        }
        private void ApplyGraphics()
        {
            if (!CanApplyGraphics) return;
            try
            {
                var values = GraphicsDraft();
                if (_gameSettingsMode == GameSettingsMode.File) ApplyFileGraphics(values);
                else
                {
                    _bridge.ApplyGraphicsSettings(_graphicsSnapshot.Revision, values);
                    _submittedGraphics = values; _graphicsState = GraphicsSyncState.Applying;
                    GraphicsError = string.Empty; GraphicsStatus = "正在应用，等待游戏确认…";
                }
            }
            catch (Exception error)
            {
                GraphicsError = error.Message; _log.Error("提交画面设置失败", error);
            }
            NotifyGraphics();
        }
        internal void RefreshGraphics()
        {
            try
            {
                var next = _snapshot.State == SessionState.Running
                    ? _bridge.GetGraphicsSettings() : new GraphicsSettingsSnapshot(0, GraphicsSyncState.Unavailable, null);
                var previousState = _graphicsState;
                _graphicsState = next.State;
                if (next.State == GraphicsSyncState.Unavailable)
                {
                    _graphicsSnapshot = null; _submittedGraphics = null;
                    GraphicsStatus = "游戏已启动，正在等待画面设置就绪…";
                    GraphicsError = string.Empty;
                }
                else if (next.State == GraphicsSyncState.Failed)
                {
                    _submittedGraphics = null;
                    GraphicsError = "画面设置同步中断，请重启游戏后重试；详情见日志。";
                }
                else if (next.State != GraphicsSyncState.Applying && next.Values != null)
                {
                    var changed = _graphicsSnapshot == null || next.Revision != _graphicsSnapshot.Revision;
                    var completed = _submittedGraphics != null;
                    if (changed || completed || (next.State == GraphicsSyncState.Conflict && previousState != next.State))
                    {
                        SetGraphicsDraft(next.Values);
                        GraphicsStatus = next.State == GraphicsSyncState.Conflict
                            ? "游戏内设置已变化，已同步最新值；本次修改未覆盖游戏。"
                            : completed ? (_submittedGraphics.Equals(next.Values) ? "已应用到游戏并回读确认。" : "已同步游戏实际采用的设置。")
                            : _graphicsSnapshot == null ? "已读取游戏设置；游戏内的修改会自动同步。" : "已同步游戏内的修改。";
                        GraphicsError = string.Empty;
                    }
                    _graphicsSnapshot = next; _submittedGraphics = null;
                }
            }
            catch (Exception error)
            {
                if (_graphicsState != GraphicsSyncState.Failed) _log.Error("读取画面设置失败", error);
                _graphicsState = GraphicsSyncState.Failed;
                GraphicsError = "读取画面设置失败，请查看日志。";
            }
            NotifyGraphics();
        }
        private void NotifyGraphics()
        {
            NotifyOutputDevice();
            OnPropertyChanged(nameof(CanEditGraphics)); OnPropertyChanged(nameof(CanApplyGraphics));
            OnPropertyChanged(nameof(HasGraphicsChanges));
            ApplyGraphicsCommand?.NotifyCanExecuteChanged(); ResetGraphicsCommand?.NotifyCanExecuteChanged();
            RevertGraphicsCommand?.NotifyCanExecuteChanged();
        }
    }
}
