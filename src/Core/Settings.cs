using System;
using System.IO;
using System.Text;

namespace T7.Rekindle.Core
{
    public sealed class UserSettings
    {
        public int SchemaVersion { get; set; } = 1;
        public string ClientDirectory { get; set; } = string.Empty;
        public string PlayerName { get; set; } = string.Empty;
        public double WindowWidth { get; set; } = 1000;
        public double WindowHeight { get; set; } = 743;
        public bool DarkTheme { get; set; }
    }

    public static class SettingsSchema
    {
        public const int CurrentVersion = 1;

        public static bool IsValid(UserSettings settings)
        {
            if (settings == null || settings.SchemaVersion != CurrentVersion)
            {
                return false;
            }

            if (settings.PlayerName == null || (settings.PlayerName.Length != 0
                && PlayerNameRules.Validate(settings.PlayerName).Length != 0)) return false;

            if (settings.ClientDirectory == null || settings.ClientDirectory.IndexOf('\0') >= 0
                || settings.ClientDirectory.IndexOf('\r') >= 0 || settings.ClientDirectory.IndexOf('\n') >= 0
                || !Utf8WithinLimit(settings.ClientDirectory))
            {
                return false;
            }

            if (settings.ClientDirectory.Length != 0 && !IsFullyQualifiedPath(settings.ClientDirectory))
            {
                return false;
            }

            return settings.WindowWidth >= 480 && settings.WindowWidth <= 4096
                && settings.WindowHeight >= 320 && settings.WindowHeight <= 4096;
        }

        private static bool Utf8WithinLimit(string value)
        {
            try
            {
                return new UTF8Encoding(false, true).GetByteCount(value) <= 32768;
            }
            catch (EncoderFallbackException)
            {
                return false;
            }
        }

        private static bool IsFullyQualifiedPath(string path)
        {
            if (!Path.IsPathRooted(path))
            {
                return false;
            }

            if (path.Length >= 2 && path[0] == '\\' && path[1] == '\\')
            {
                return path.Length >= 3 && path[2] != '\\' && path[2] != '/';
            }

            return path.Length >= 3 && char.IsLetter(path[0]) && path[1] == ':'
                && (path[2] == '\\' || path[2] == '/');
        }
    }
}
