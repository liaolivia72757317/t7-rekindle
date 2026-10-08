using System;
using System.IO;
using System.Text;
using Newtonsoft.Json.Linq;
using T7.Rekindle.Desktop.Services;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class SettingsMergeTests
    {
        internal static void Run(string directory)
        {
            TestMigration(Path.Combine(directory, "settings-merge"));
            TestPrecedence(Path.Combine(directory, "settings-precedence"));
            TestLegacyBackup(Path.Combine(directory, "settings-legacy-backup"));
            TestRecoveredBackupMigration(Path.Combine(directory, "settings-recovered-backup-migration"));
            TestMigrationFailure(Path.Combine(directory, "settings-migration-failure"));
            TestLegacyOnly(Path.Combine(directory, "settings-legacy-only"));
            TestDamagedMain(Path.Combine(directory, "settings-damaged-main"));
            TestStringPreservation(Path.Combine(directory, "settings-string-preservation"));
        }

        private static JObject WriteMain(string directory, string channel = null)
        {
            Directory.CreateDirectory(directory);
            var value = new JObject
            {
                ["schemaVersion"] = 1, ["clientDirectory"] = @"C:\Games\T7", ["playerName"] = "保留玩家",
                ["windowWidth"] = 1100.0, ["windowHeight"] = 800.0, ["darkTheme"] = true,
                ["minimizeToTray"] = true, ["startWithWindows"] = false, ["skipStartupAnimation"] = true
            };
            if (channel != null) value["updateChannel"] = channel;
            File.WriteAllText(Path.Combine(directory, "settings.json"), value.ToString(), Encoding.UTF8);
            return value;
        }

        private static void WriteLegacy(string directory, string channel, string suffix = "")
        {
            Directory.CreateDirectory(directory);
            File.WriteAllText(Path.Combine(directory, "update-settings.json" + suffix),
                new JObject { ["schemaVersion"] = 1, ["channel"] = channel }.ToString(), Encoding.UTF8);
        }

        private static void TestMigration(string directory)
        {
            var original = WriteMain(directory);
            WriteLegacy(directory, "preview");
            var settings = new SettingsService(directory);
            var loaded = settings.Load();
            var merged = JObject.Parse(File.ReadAllText(Path.Combine(directory, "settings.json"), Encoding.UTF8));
            Assert((string)merged["updateChannel"] == "preview", "legacy update channel was not merged into settings.json");
            merged.Remove("updateChannel");
            Assert(JToken.DeepEquals(merged, original) && loaded.PlayerName == "保留玩家" && loaded.SkipStartupAnimation,
                "channel migration lost unrelated settings");
            Assert(JToken.DeepEquals(JObject.Parse(File.ReadAllText(Path.Combine(directory, "settings.json.bak"), Encoding.UTF8)), original),
                "migration did not retain the previous main settings backup");
            WriteLegacy(directory, "stable");
            Assert(new SettingsService(directory).LoadUpdateChannel(out _) == UpdateChannel.Preview,
                "legacy file overwrote the unified channel after migration");
        }

        private static void TestPrecedence(string directory)
        {
            WriteMain(directory, "stable"); WriteLegacy(directory, "preview");
            var settings = new SettingsService(directory);
            Assert(settings.LoadUpdateChannel(out var warning) == UpdateChannel.Stable && warning.Length == 0,
                "explicit unified channel did not win over legacy settings");
            settings.SaveUpdateChannel(UpdateChannel.Preview);
            var main = JObject.Parse(File.ReadAllText(Path.Combine(directory, "settings.json"), Encoding.UTF8));
            Assert((string)main["updateChannel"] == "preview" && (string)main["playerName"] == "保留玩家"
                && (bool)main["darkTheme"] && (int)main["windowWidth"] == 1100,
                "saving the channel lost unified settings");
            settings.SaveUpdateChannel(UpdateChannel.Stable);
            File.WriteAllText(Path.Combine(directory, "settings.json"), "invalid", Encoding.UTF8);
            Assert(settings.LoadUpdateChannel(out warning) == UpdateChannel.Preview && warning.Contains("备份"),
                "unified backup did not restore the channel");
            var invalid = WriteMain(directory, "unknown");
            File.WriteAllText(Path.Combine(directory, "settings.json.bak"), invalid.ToString(), Encoding.UTF8);
            Assert(settings.LoadUpdateChannel(out warning) == UpdateChannel.Stable && warning.Length != 0,
                "invalid unified channel was silently accepted");
        }

        private static void TestLegacyBackup(string directory)
        {
            WriteMain(directory);
            WriteLegacy(directory, "unknown"); WriteLegacy(directory, "preview", ".bak");
            var settings = new SettingsService(directory);
            Assert(settings.LoadUpdateChannel(out var warning) == UpdateChannel.Preview && warning.Contains("备份"),
                "legacy backup was not migrated with a warning");
            Assert(new SettingsService(directory).LoadUpdateChannel(out warning) == UpdateChannel.Preview && warning.Length == 0,
                "completed migration kept reading broken legacy preferences");
        }

        private static void TestRecoveredBackupMigration(string directory)
        {
            WriteMain(directory);
            var path = Path.Combine(directory, "settings.json");
            File.Copy(path, path + ".bak");
            var invalid = WriteMain(directory, "stable");
            invalid["schemaVersion"] = 2;
            invalid["playerName"] = "损坏玩家";
            File.WriteAllText(path, invalid.ToString(), Encoding.UTF8);
            WriteLegacy(directory, "preview");

            var settings = new SettingsService(directory);
            var loaded = settings.Load();
            Assert(loaded.UpdateChannel == "preview",
                "invalid main settings prevented legacy channel migration into the recovered backup");
            Assert(loaded.PlayerName == "保留玩家" && loaded.SkipStartupAnimation && settings.LastWarning.Contains("备份"),
                "channel migration lost recovered settings or the backup warning");
            var reloaded = settings.Load();
            Assert(reloaded.UpdateChannel == "preview" && reloaded.PlayerName == "保留玩家"
                && reloaded.SkipStartupAnimation && settings.LastWarning.Length == 0,
                "recovered settings and migrated channel were not persisted together");
        }

        private static void TestMigrationFailure(string directory)
        {
            WriteMain(directory); WriteLegacy(directory, "preview");
            var path = Path.Combine(directory, "settings.json");
            var original = File.ReadAllText(path, Encoding.UTF8);
            using (File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                var settings = new SettingsService(directory);
                Assert(settings.LoadUpdateChannel(out var warning) == UpdateChannel.Preview && warning.Contains("迁移"),
                    "migration failure lost the loaded channel or hid the error");
                Assert(File.ReadAllText(path, Encoding.UTF8) == original, "failed migration overwrote the main file");
            }
            Assert(new SettingsService(directory).LoadUpdateChannel(out var restoredWarning) == UpdateChannel.Preview
                && restoredWarning.Length == 0, "migration was not retried after a transient write failure");
        }

        private static void TestLegacyOnly(string directory)
        {
            WriteLegacy(directory, "preview");
            var settings = new SettingsService(directory);
            Assert(settings.LoadUpdateChannel(out _) == UpdateChannel.Preview
                && (string)JObject.Parse(File.ReadAllText(Path.Combine(directory, "settings.json"), Encoding.UTF8))["updateChannel"] == "preview",
                "standalone legacy channel did not migrate without a main settings file");
        }

        private static void TestDamagedMain(string directory)
        {
            var invalid = WriteMain(directory);
            invalid["unknownField"] = true;
            var path = Path.Combine(directory, "settings.json");
            File.WriteAllText(path, invalid.ToString(), Encoding.UTF8);
            WriteLegacy(directory, "preview");
            var settings = new SettingsService(directory);
            var loaded = settings.Load();
            Assert(loaded.PlayerName == "新玩家" && loaded.UpdateChannel == "preview" && settings.LastWarning.Length != 0,
                "invalid main settings were accepted or prevented loading the legacy channel");
            Assert(File.ReadAllText(path, Encoding.UTF8) == invalid.ToString() && !File.Exists(path + ".bak"),
                "migration overwrote invalid main settings with defaults");
        }

        private static void TestStringPreservation(string directory)
        {
            const string name = "2026-10-08T00:00:00Z";
            var original = WriteMain(directory);
            original["playerName"] = name;
            File.WriteAllText(Path.Combine(directory, "settings.json"), original.ToString(), Encoding.UTF8);
            WriteLegacy(directory, "preview");
            var settings = new SettingsService(directory);
            Assert(settings.Load().PlayerName == name && new SettingsService(directory).Load().PlayerName == name,
                "migration converted a date-like player name instead of preserving its text");
        }
    }
}
