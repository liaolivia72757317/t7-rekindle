using System;
using System.IO;
using System.Linq;
using System.Text;
using Newtonsoft.Json.Linq;
using T7R.Core;
using T7R.Desktop.Services;
using static T7R.ManagedHarness.LauncherTests;

namespace T7R.ManagedHarness
{
    internal static class MatchMapPreferencesTests
    {
        internal static void Run(string root)
        {
            var directory = Path.Combine(root, "map-settings");
            var settings = new SettingsService(directory);
            var all = new uint[] { 1003, 1030, 1021, 1001, 6003, 1034 };
            Assert(settings.Load().SelectedMatchMapIds.SequenceEqual(all), "new settings select every team map");
            settings.Save(new UserSettings { PlayerName = "测试玩家", DarkTheme = true });
            var path = Path.Combine(directory, "settings.json");
            var legacy = JObject.Parse(File.ReadAllText(path, Encoding.UTF8));
            legacy.Remove("selectedMatchMapIds");
            File.WriteAllText(path, legacy.ToString(), Encoding.UTF8);
            Assert(settings.Load().SelectedMatchMapIds.SequenceEqual(all), "legacy settings default to all team maps");

            var client = GameSettingsFileTests.CreateFixture(Path.Combine(root, "map-client"));
            var original = GameSettingsFileService.Read(client);
            var session = new MatchMapPreferences(client, directory);
            session.Restore();
            Assert(GameSettingsFileService.ReadMatchMaps(client).SequenceEqual(all), "launch restores native filters");
            var bytes = File.ReadAllBytes(path);
            session.Capture();
            Assert(File.ReadAllBytes(path).SequenceEqual(bytes), "unchanged selection does not write settings");

            var selected = new uint[] { 1003, 1034 };
            GameSettingsFileService.SaveMatchMaps(client, selected);
            session.Capture();
            var loaded = new SettingsService(directory).Load();
            Assert(loaded.SelectedMatchMapIds.SequenceEqual(selected) && loaded.PlayerName == "测试玩家" && loaded.DarkTheme,
                "confirmed native choice persists without replacing other launcher preferences");
            settings.SaveLauncherSettings(new UserSettings { PlayerName = "修改玩家", WindowWidth = 1300 });
            Assert(settings.Load().SelectedMatchMapIds.SequenceEqual(selected), "launcher saves preserve the latest game selection");
            settings.SaveUpdateChannel(UpdateChannel.Preview);
            Assert(settings.Load().SelectedMatchMapIds.SequenceEqual(selected), "channel changes preserve map selection");

            GameSettingsFileService.SaveMatchMaps(client, all);
            new MatchMapPreferences(client, directory).Restore();
            Assert(GameSettingsFileService.ReadMatchMaps(client).SequenceEqual(selected), "next launch restores the persisted subset");
            Assert(GameSettingsFileService.Read(client).Graphics.Equals(original.Graphics)
                && GameSettingsFileService.Read(client).Audio.Equals(original.Audio), "map restore preserves game graphics and audio");
            var native = File.ReadAllText(GameSettingsFileTests.ConfigPath(client), Encoding.GetEncoding(936));
            Assert(native.Contains("<0_1003 ") && !native.Contains("__t7r_numeric_"), "native numeric field names roundtrip");

            foreach (var invalid in new[] { Array.Empty<uint>(), new uint[] { 1003, 1003 }, new uint[] { 90003 }, new uint[] { 9999 } })
            {
                var rejected = false;
                try { GameSettingsFileService.SaveMatchMaps(client, invalid); }
                catch (ArgumentException) { rejected = true; }
                Assert(rejected, "empty, duplicated and non-team selections are rejected");
            }
            var nativePath = GameSettingsFileTests.ConfigPath(client);
            File.WriteAllText(nativePath, native.Replace("<0_1003 Value=\"false\"", "<0_1003 Value=\"true\"")
                .Replace("<0_1034 Value=\"false\"", "<0_1034 Value=\"true\""), Encoding.GetEncoding(936));
            session.Capture();
            Assert(settings.Load().SelectedMatchMapIds.SequenceEqual(selected), "an empty native selection never replaces a valid preference");
            GameSettingsFileService.SaveMatchMaps(client, new uint[] { 1030 });
            using (var held = File.Open(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) session.Capture();
            Assert(settings.Load().SelectedMatchMapIds.SequenceEqual(selected), "failed persistence retains the prior selection");
            session.Capture();
            Assert(settings.Load().SelectedMatchMapIds.SequenceEqual(new uint[] { 1030 }), "failed persistence is retried");
        }
    }
}
