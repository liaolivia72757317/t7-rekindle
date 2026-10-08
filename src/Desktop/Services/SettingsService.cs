using System;
using System.IO;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Newtonsoft.Json.Serialization;
using T7.Rekindle.Core;

namespace T7.Rekindle.Desktop.Services
{
    public sealed partial class SettingsService
    {
        private readonly string _directory;
        private readonly string _path;
        private readonly string _backupPath;

        public string LastWarning { get; private set; } = string.Empty;

        public SettingsService()
            : this(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "T7-Rekindle"))
        {
        }

        // A directory overload keeps persistence tests isolated without
        // changing the product's fixed LocalAppData location.
        public SettingsService(string directory)
        {
            if (string.IsNullOrWhiteSpace(directory)) throw new ArgumentException("设置目录不能为空。", nameof(directory));
            _directory = Path.GetFullPath(directory);
            _path = Path.Combine(_directory, "settings.json");
            _backupPath = _path + ".bak";
        }

        public UserSettings Load()
        {
            LastWarning = string.Empty;
            var settings = TryRead(_path, out var hasUpdateChannel);
            if (settings == null)
            {
                if (File.Exists(_path))
                {
                    LastWarning = "设置文件无效，已保留原文件并尝试恢复备份。";
                }

                settings = TryRead(_backupPath, out hasUpdateChannel);
                if (settings != null)
                {
                    LastWarning += "已从 settings.json.bak 恢复。";
                }
                else if (File.Exists(_backupPath))
                {
                    LastWarning += "备份也无效，请重新选择客户端目录。";
                }
            }

            var canMigrate = settings != null || (!File.Exists(_path) && !File.Exists(_backupPath));
            settings = settings ?? new UserSettings();
            if (!hasUpdateChannel)
            {
                MigrateUpdateChannel(settings, canMigrate);
            }
            return settings;
        }

        public void Save(UserSettings settings)
        {
            if (!SettingsSchema.IsValid(settings))
            {
                throw new InvalidDataException("设置不符合当前 schema。");
            }

            var json = JsonConvert.SerializeObject(settings, Formatting.Indented, new JsonSerializerSettings
            {
                TypeNameHandling = TypeNameHandling.None,
                MissingMemberHandling = MissingMemberHandling.Error,
                ContractResolver = new CamelCasePropertyNamesContractResolver()
            }) + Environment.NewLine;
            WriteJson(_path, json);
        }

        private void WriteJson(string path, string json)
        {
            Directory.CreateDirectory(_directory);
            // CreateNew and same-directory replacement keep each write atomic.
            var temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
            var bytes = new System.Text.UTF8Encoding(false).GetBytes(json);
            var committed = false;
            try
            {
                using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    stream.Write(bytes, 0, bytes.Length);
                    stream.Flush(true);
                }

                if (File.Exists(path))
                {
                    File.Replace(temporary, path, path + ".bak", true);
                }
                else
                {
                    File.Move(temporary, path);
                }
                committed = true;
            }
            finally
            {
                if (!committed)
                {
                    try
                    {
                        if (File.Exists(temporary)) File.Delete(temporary);
                    }
                    catch (Exception cleanupError) when (cleanupError is IOException || cleanupError is UnauthorizedAccessException)
                    {
                        // Preserve the original write/replace failure; a
                        // uniquely named temp file cannot affect the next
                        // atomic save.
                    }
                }
            }
        }

        private static UserSettings TryRead(string path, out bool hasUpdateChannel)
        {
            hasUpdateChannel = false;
            try
            {
                if (!File.Exists(path))
                {
                    return null;
                }

                var serializerSettings = new JsonSerializerSettings
                {
                    TypeNameHandling = TypeNameHandling.None,
                    DateParseHandling = DateParseHandling.None,
                    MissingMemberHandling = MissingMemberHandling.Error,
                    CheckAdditionalContent = true,
                    ContractResolver = new CamelCasePropertyNamesContractResolver()
                };
                var value = JsonConvert.DeserializeObject<JObject>(File.ReadAllText(path, Encoding.UTF8), serializerSettings);
                if (value == null) return null;
                hasUpdateChannel = value.Property("updateChannel", StringComparison.OrdinalIgnoreCase) != null;
                var settings = value.ToObject<UserSettings>(JsonSerializer.Create(serializerSettings));
                return SettingsSchema.IsValid(settings) ? settings : null;
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException || ex is JsonException)
            {
                return null;
            }
        }
    }
}
