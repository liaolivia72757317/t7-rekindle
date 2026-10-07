using System;
using System.IO;
using System.Text;
using Newtonsoft.Json;

namespace T7.Rekindle.Desktop.Services
{
    public sealed partial class SettingsService
    {
        // Older launchers reject unknown settings.json fields, including during a channel downgrade.
        public UpdateChannel LoadUpdateChannel(out string warning)
        {
            warning = string.Empty;
            var path = Path.Combine(_directory, "update-settings.json");
            var channel = TryReadChannel(path);
            if (channel.HasValue) return channel.Value;
            var backup = TryReadChannel(path + ".bak");
            if (File.Exists(path) || File.Exists(path + ".bak"))
                warning = backup.HasValue ? "更新渠道设置无效，已恢复备份。" : "更新渠道设置无效，已使用正式渠道。";
            return backup ?? UpdateChannel.Stable;
        }

        public void SaveUpdateChannel(UpdateChannel channel)
        {
            if (channel != UpdateChannel.Stable && channel != UpdateChannel.Preview)
                throw new ArgumentOutOfRangeException(nameof(channel));
            WriteJson(Path.Combine(_directory, "update-settings.json"), JsonConvert.SerializeObject(
                new { schemaVersion = 1, channel = channel == UpdateChannel.Stable ? "stable" : "preview" },
                Formatting.Indented) + Environment.NewLine);
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
