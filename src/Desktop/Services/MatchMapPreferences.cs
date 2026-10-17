using System;
using System.IO;
using System.Linq;
using System.Xml;

namespace T7R.Desktop.Services
{
    internal sealed class MatchMapPreferences
    {
        private readonly string _clientDirectory;
        private readonly SettingsService _settings;
        private uint[] _saved;
        private DateTime _nextCapture;
        private string _lastError;

        internal MatchMapPreferences(string clientDirectory, string settingsDirectory)
        {
            _clientDirectory = clientDirectory;
            _settings = new SettingsService(settingsDirectory);
        }

        internal void Restore()
        {
            if (GameSettingsFileService.IsClientRunning(_clientDirectory))
                throw new InvalidOperationException("请先关闭已运行的游戏，再恢复地图选择。");
            var settings = _settings.Load();
            if (!_settings.CanPersistLoadedSettings) throw new InvalidDataException("启动器配置无效，请先恢复配置再启动游戏。");
            GameSettingsFileService.SaveMatchMaps(_clientDirectory, settings.SelectedMatchMapIds);
            _saved = Core.MatchMapSelection.Normalize(settings.SelectedMatchMapIds);
        }

        internal void Poll()
        {
            if (DateTime.UtcNow < _nextCapture) return;
            _nextCapture = DateTime.UtcNow.AddSeconds(1);
            Capture();
        }

        internal void Capture()
        {
            if (_saved == null) return;
            try
            {
                // The original confirmation handler writes UserData.cfg immediately.
                // Reading it avoids persisting checkbox drafts or relying on a match request.
                var selected = GameSettingsFileService.ReadMatchMaps(_clientDirectory);
                if (!_saved.SequenceEqual(selected))
                {
                    _settings.SaveMatchMaps(selected);
                    _saved = selected;
                }
                _lastError = null;
            }
            catch (Exception error) when (error is IOException || error is UnauthorizedAccessException
                || error is XmlException || error is ArgumentException)
            {
                if (_lastError != error.Message)
                    NLog.LogManager.GetLogger("MatchMaps").Warn(error, "地图选择尚未保存，将重试；保留上次有效配置。");
                _lastError = error.Message;
            }
        }
    }
}
