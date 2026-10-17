using System;
using System.IO;
using System.Text;
using Newtonsoft.Json;
using T7R.Core;

namespace T7R.Desktop.Services
{
    public sealed partial class SettingsService
    {
        public UpdateChannel LoadUpdateChannel(out string warning)
        {
            var settings = Load();
            warning = LastWarning;
            return settings.UpdateChannel == "preview" ? UpdateChannel.Preview : UpdateChannel.Stable;
        }

        public void SaveUpdateChannel(UpdateChannel channel)
        {
            if (channel != UpdateChannel.Stable && channel != UpdateChannel.Preview)
                throw new ArgumentOutOfRangeException(nameof(channel));
            lock (PersistenceGate)
            {
                var settings = Load();
                settings.UpdateChannel = channel == UpdateChannel.Preview ? "preview" : "stable";
                if (channel == UpdateChannel.Preview) settings.LogLevel = DiagnosticLogLevels.Name(DiagnosticLogLevel.Debug);
                Save(settings);
            }
        }

        internal void ResetUpdateChannelToStable()
        {
            lock (PersistenceGate)
            {
                var settings = Load();
                if (!CanPersistLoadedSettings) throw new InvalidDataException("设置和备份均无效，保留原文件。");
                settings.UpdateChannel = "stable";
                Save(settings);
            }
        }

        private void MigrateUpdateChannel(UserSettings settings, bool canSave)
        {
            var path = Path.Combine(_directory, "update-settings.json");
            if (!File.Exists(path) && !File.Exists(path + ".bak")) return;
            var channel = TryReadChannel(path);
            if (!channel.HasValue)
            {
                channel = TryReadChannel(path + ".bak");
                LastWarning += channel.HasValue ? "更新渠道设置无效，已恢复备份。" : "更新渠道设置无效，已使用正式渠道。";
            }
            settings.UpdateChannel = channel == UpdateChannel.Preview ? "preview" : "stable";
            if (!canSave) return;
            try
            {
                Save(settings);
            }
            catch (Exception error) when (error is IOException || error is UnauthorizedAccessException)
            {
                LastWarning += "设置迁移未保存：" + error.Message;
            }
        }

        private static UpdateChannel? TryReadChannel(string path)
        {
            try
            {
                if (!File.Exists(path)) return null;
                var settings = JsonConvert.DeserializeObject<UpdatePreferences>(File.ReadAllText(path, Encoding.UTF8),
                    new JsonSerializerSettings { TypeNameHandling = TypeNameHandling.None,
                        MissingMemberHandling = MissingMemberHandling.Error, CheckAdditionalContent = true });
                if (settings?.SchemaVersion != 1) return null;
                return settings.Channel == "stable" ? UpdateChannel.Stable
                    : settings.Channel == "preview" ? (UpdateChannel?)UpdateChannel.Preview : null;
            }
            catch (Exception error) when (error is IOException || error is UnauthorizedAccessException || error is JsonException)
            {
                return null;
            }
        }

        private sealed class UpdatePreferences
        {
            [JsonProperty("schemaVersion")] public int SchemaVersion { get; set; }
            [JsonProperty("channel")] public string Channel { get; set; }
        }
    }
}
