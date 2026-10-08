using System;
using System.IO;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop.Services;

namespace T7.Rekindle.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel
    {
        private enum GameSettingsMode { Unavailable, File, Memory }
        private readonly Func<string, bool> _isClientRunning;
        private GameSettingsMode _gameSettingsMode;
        private string _fileSettingsDirectory = string.Empty;
        private string _fileSettingsError = string.Empty;
        private DateTime _nextFileSettingsRead;

        private bool CanReadGameSettingsFile => !_disposed && !_operationActive && !_isValidating && !_directoryHasDraft
            && _directoryResult.IsValid && (_snapshot.State == SessionState.Idle
                || (_snapshot.State == SessionState.Failed && _snapshot.CleanupComplete));
        private bool CanEditGameSettings => _gameSettingsMode == GameSettingsMode.File
            ? CanReadGameSettingsFile && string.Equals(_fileSettingsDirectory, _directoryResult.Directory, StringComparison.OrdinalIgnoreCase)
            : _gameSettingsMode == GameSettingsMode.Memory && !_disposed && !_operationActive && _snapshot.State == SessionState.Running;

        internal void RefreshGameSettings(bool forceFileRead = false)
        {
            if (_snapshot.State == SessionState.Running)
            {
                SetGameSettingsMode(GameSettingsMode.Memory);
                RefreshGraphics();
                RefreshAudio();
                return;
            }
            if (!CanReadGameSettingsFile)
            {
                SetGameSettingsMode(GameSettingsMode.Unavailable);
                SetGameSettingsUnavailable(_isValidating ? "正在检查游戏目录…"
                    : _directoryHasDraft ? "请先完成游戏目录编辑。"
                    : !_directoryResult.IsValid ? "请选择有效游戏目录后编辑配置文件。"
                    : "游戏正在启动或结束，设置编辑暂时暂停。");
                return;
            }
            SetGameSettingsMode(GameSettingsMode.File, _directoryResult.Directory);
            if (!forceFileRead && DateTime.UtcNow < _nextFileSettingsRead) return;
            _nextFileSettingsRead = DateTime.UtcNow.AddSeconds(1);
            try
            {
                if (_isClientRunning(_fileSettingsDirectory))
                {
                    SetGameSettingsUnavailable("检测到游戏正在运行，文件编辑已暂停；通过本启动器启动后使用内存同步。");
                    return;
                }
                var current = GameSettingsFileService.Read(_fileSettingsDirectory);
                RefreshFileGraphics(current.Graphics);
                RefreshFileAudio(current.Audio);
                _fileSettingsError = string.Empty;
            }
            catch (Exception error)
            {
                var missing = error is FileNotFoundException || error is DirectoryNotFoundException;
                var message = missing
                    ? "未找到 Data/UserData/UserData.cfg，请先启动一次游戏生成配置。" : error.Message;
                if (!missing && _fileSettingsError != message) _log.Error("读取游戏配置文件失败", error);
                _fileSettingsError = message;
                _graphicsState = GraphicsSyncState.Failed; _audioState = AudioSyncState.Failed;
                GraphicsStatus = AudioStatus = "配置文件暂不可读。";
                GraphicsError = AudioError = message;
            }
            NotifyGraphics();
            NotifyAudio();
        }

        private void SetGameSettingsMode(GameSettingsMode mode, string directory = "")
        {
            if (_gameSettingsMode == mode && string.Equals(_fileSettingsDirectory, directory, StringComparison.OrdinalIgnoreCase)) return;
            _gameSettingsMode = mode; _fileSettingsDirectory = directory;
            _nextFileSettingsRead = DateTime.MinValue; _fileSettingsError = string.Empty;
            _graphicsSnapshot = null; _audioSnapshot = null;
            _submittedGraphics = null; _submittedAudio = null;
            _graphicsState = GraphicsSyncState.Unavailable; _audioState = AudioSyncState.Unavailable;
            GraphicsError = AudioError = string.Empty;
            if (mode != GameSettingsMode.Unavailable) RefreshGraphicsResolutions();
        }

        private void SetGameSettingsUnavailable(string message)
        {
            _graphicsState = GraphicsSyncState.Unavailable; _audioState = AudioSyncState.Unavailable;
            GraphicsStatus = AudioStatus = message;
            GraphicsError = AudioError = string.Empty;
            NotifyGraphics(); NotifyAudio();
        }

        private void RefreshFileGraphics(GraphicsSettingsValues current)
        {
            var first = _graphicsSnapshot == null;
            var changed = first || !_graphicsSnapshot.Values.Equals(current);
            if (changed)
            {
                var discardedDraft = HasGraphicsChanges;
                AcceptFileGraphics(current, first ? "已读取配置文件；保存后下次启动游戏生效。"
                    : discardedDraft ? "配置文件已变化，已同步最新画面设置；未保存修改已撤销。" : "已同步配置文件中的画面设置。");
            }
            else if (_graphicsState != GraphicsSyncState.Ready)
            {
                _graphicsState = GraphicsSyncState.Ready; GraphicsError = string.Empty;
                GraphicsStatus = HasGraphicsChanges ? "有未保存的修改；保存后下次启动游戏生效。" : "已读取配置文件；保存后下次启动游戏生效。";
            }
        }

        private void RefreshFileAudio(AudioSettingsValues current)
        {
            var first = _audioSnapshot == null;
            var changed = first || !_audioSnapshot.Values.Equals(current);
            if (changed)
            {
                var discardedDraft = HasAudioChanges;
                AcceptFileAudio(current, first ? "已读取配置文件；保存后下次启动游戏生效。"
                    : discardedDraft ? "配置文件已变化，已同步最新声音设置；未保存修改已撤销。" : "已同步配置文件中的声音设置。");
            }
            else if (_audioState != AudioSyncState.Ready)
            {
                _audioState = AudioSyncState.Ready; AudioError = string.Empty;
                AudioStatus = HasAudioChanges ? "有未保存的修改；保存后下次启动游戏生效。" : "已读取配置文件；保存后下次启动游戏生效。";
            }
        }

        private void EnsureGameSettingsFileWritable()
        {
            if (_gameSettingsMode != GameSettingsMode.File || !CanEditGameSettings)
                throw new InvalidOperationException("游戏状态或目录已变化，请等待设置重新读取。");
            if (_isClientRunning(_fileSettingsDirectory))
                throw new InvalidOperationException("游戏已经启动，文件保存已暂停；请使用运行时同步。");
        }

        private void ApplyFileGraphics(GraphicsSettingsValues values)
        {
            EnsureGameSettingsFileWritable();
            var saved = GameSettingsFileService.SaveGraphics(_fileSettingsDirectory, _graphicsSnapshot.Values, values, out var current);
            AcceptFileGraphics(current.Graphics, saved ? "画面设置已保存到配置文件，下次启动游戏生效。"
                : "配置文件已变化，已同步最新值；本次修改未覆盖文件。");
            _nextFileSettingsRead = DateTime.MinValue;
        }

        private void ApplyFileAudio(AudioSettingsValues values)
        {
            EnsureGameSettingsFileWritable();
            var saved = GameSettingsFileService.SaveAudio(_fileSettingsDirectory, _audioSnapshot.Values, values, out var current);
            AcceptFileAudio(current.Audio, saved ? "声音设置已保存到配置文件，下次启动游戏生效。"
                : "配置文件已变化，已同步最新值；本次修改未覆盖文件。");
            _nextFileSettingsRead = DateTime.MinValue;
        }

        private void AcceptFileGraphics(GraphicsSettingsValues values, string status)
        {
            SetGraphicsDraft(values);
            _graphicsSnapshot = new GraphicsSettingsSnapshot(0, GraphicsSyncState.Ready, values);
            _graphicsState = GraphicsSyncState.Ready;
            GraphicsError = string.Empty; GraphicsStatus = status;
        }

        private void AcceptFileAudio(AudioSettingsValues values, string status)
        {
            SetAudioDraft(values);
            _audioSnapshot = new AudioSettingsSnapshot(0, AudioSyncState.Ready, values);
            _audioState = AudioSyncState.Ready;
            AudioError = string.Empty; AudioStatus = status;
        }
    }
}
