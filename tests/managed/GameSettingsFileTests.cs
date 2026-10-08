using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Xml;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop.Services;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class GameSettingsFileTests
    {
        private static readonly Encoding Gbk = Encoding.GetEncoding(936);

        internal static string CreateFixture(string root)
        {
            var directory = Path.Combine(root, "Bin");
            Directory.CreateDirectory(directory);
            Directory.CreateDirectory(Path.Combine(root, "Data", "UserData"));
            var fields = new[,]
            {
                { "TargetSize", "str", "1920x1080" }, { "FullScreen", "bool", "false" },
                { "ConfigLevel", "i32", "1" }, { "vSync", "bool", "false" },
                { "enable_distance_fog", "bool", "false" }, { "distance_fog_end", "f32", "159" },
                { "ragDollEnable", "bool", "true" }, { "FPS", "i32", "200" },
                { "MusicMute", "bool", "false" }, { "MusicVolume", "f32", "0.75" },
                { "AudioMute", "bool", "true" }, { "AudioVolume", "f32", "0.25" },
                { "Unrelated", "str", "保留中文配置" }
            };
            var xml = new StringBuilder("<?xml version=\"1.0\" encoding=\"gb2312\"?>\r\n<PropertySheet Version=\"100\">\r\n<!--保留注释-->\r\n<Header>\r\n");
            for (var i = 0; i < fields.GetLength(0); i++)
                xml.AppendFormat("\t<{0} Type=\"{1}\" Value=\"{2}\"/>\r\n", fields[i, 0], fields[i, 1], fields[i, 2]);
            xml.Append("</Header>\r\n<Record Name=\"Config\">\r\n");
            for (var i = 0; i < fields.GetLength(0); i++)
                xml.AppendFormat("\t<{0} Value=\"{1}\"/>\r\n", fields[i, 0], fields[i, 2]);
            xml.Append("</Record>\r\n<Record Name=\"Other\"><Unrelated Value=\"原样保留\"/></Record>\r\n</PropertySheet>\r\n");
            File.WriteAllText(ConfigPath(directory), xml.ToString(), Gbk);
            return directory;
        }

        internal static string ConfigPath(string binaryDirectory) =>
            Path.GetFullPath(Path.Combine(binaryDirectory, "..", "Data", "UserData", "UserData.cfg"));

        internal static void Run(string root)
        {
            TestReadAndSave(CreateFixture(Path.Combine(root, "game-file")));
            TestValidation(CreateFixture(Path.Combine(root, "game-file-invalid")));
            TestWriteFailure(CreateFixture(Path.Combine(root, "game-file-locked")));
            TestUtf8(CreateFixture(Path.Combine(root, "game-file-utf8")));
        }

        private static void TestReadAndSave(string directory)
        {
            var path = ConfigPath(directory);
            var original = File.ReadAllBytes(path);
            var initial = GameSettingsFileService.Read(directory);
            Assert(initial.Graphics.Quality == 3 && initial.Graphics.ViewDistance == 159 && !initial.Graphics.FrameLimit
                && initial.Graphics.Swoosh == 0 && initial.Graphics.Resolution == "1920x1080", "file graphics mapping");
            Assert(initial.Audio.Equals(new AudioSettingsValues(false, .75f, true, .25f)), "file audio mapping");
            var graphics = new GraphicsSettingsValues(1600, 900, false, 1, true, true, 200, false, true, 2);
            Assert(GameSettingsFileService.SaveGraphics(directory, initial.Graphics, graphics, out var saved)
                && saved.Graphics.Equals(graphics) && saved.Audio.Equals(initial.Audio), "graphics file save changed audio");
            Assert(File.ReadAllBytes(path + ".bak").SequenceEqual(original), "game configuration backup lost original bytes");
            var document = Load(path);
            Assert(document.SelectSingleNode("/PropertySheet/Header/Swoosh/@Type")?.Value == "i32"
                && document.SelectSingleNode("/PropertySheet/Record[@Name='Config']/ConfigLevel/@Value")?.Value == "3"
                && document.SelectSingleNode("/PropertySheet/Record[@Name='Config']/FPS/@Value")?.Value == "60",
                "game field types or inverted quality mapping were not saved");
            var culture = CultureInfo.CurrentCulture;
            try
            {
                CultureInfo.CurrentCulture = CultureInfo.GetCultureInfo("fr-FR");
                var audio = new AudioSettingsValues(true, .35f, false, .2f);
                Assert(GameSettingsFileService.SaveAudio(directory, initial.Audio, audio, out saved)
                    && saved.Audio.Equals(audio) && saved.Graphics.Equals(graphics), "audio file save changed graphics or used local decimal separator");
            }
            finally { CultureInfo.CurrentCulture = culture; }
            var text = File.ReadAllText(path, Gbk);
            Assert(text.Contains("保留中文配置") && text.Contains("原样保留") && text.Contains("<!--保留注释-->")
                && text.Contains("encoding=\"gb2312\""), "unrelated XML, comments or encoding changed");
            var currentBytes = File.ReadAllBytes(path);
            Assert(!GameSettingsFileService.SaveGraphics(directory, initial.Graphics, new GraphicsSettingsValues(), out saved)
                && saved.Graphics.Equals(graphics) && File.ReadAllBytes(path).SequenceEqual(currentBytes), "stale graphics overwrote current file");
            Assert(!GameSettingsFileService.SaveAudio(directory, initial.Audio, new AudioSettingsValues(), out saved)
                && File.ReadAllBytes(path).SequenceEqual(currentBytes), "stale audio overwrote current file");
        }

        private static void TestValidation(string directory)
        {
            var path = ConfigPath(directory);
            var xml = File.ReadAllText(path, Gbk);
            File.WriteAllText(path, xml.Replace("\t<MusicVolume Value=\"0.75\"/>\r\n", ""), Gbk);
            Assert(GameSettingsFileService.Read(directory).Audio.MusicVolume == .75f, "missing record value did not use header default");
            File.WriteAllText(path, xml.Replace("<FPS Value=\"200\"/>", "<FPS Value=\"999\"/>"), Gbk);
            Throws<InvalidDataException>(() => GameSettingsFileService.Read(directory));
            File.WriteAllText(path, xml.Replace("<MusicVolume Value=\"0.75\"/>", "<MusicVolume Value=\"NaN\"/>"), Gbk);
            Throws<InvalidDataException>(() => GameSettingsFileService.Read(directory));
            File.WriteAllText(path, xml.Replace("<TargetSize Type=\"str\"", "<TargetSize Type=\"i32\""), Gbk);
            Throws<InvalidDataException>(() => GameSettingsFileService.Read(directory));
            File.WriteAllText(path, xml.Replace("<ConfigLevel Value=\"1\"/>", "<ConfigLevel Value=\"1\"/><ConfigLevel Value=\"2\"/>"), Gbk);
            Throws<InvalidDataException>(() => GameSettingsFileService.Read(directory));
            File.WriteAllText(path, "<!DOCTYPE PropertySheet [<!ENTITY external SYSTEM 'file:///HOST/TOKEN'>]><PropertySheet>&external;</PropertySheet>", Gbk);
            Throws<XmlException>(() => GameSettingsFileService.Read(directory));
            File.WriteAllText(path, xml, Gbk);
            var initial = GameSettingsFileService.Read(directory);
            Throws<ArgumentException>(() => GameSettingsFileService.SaveAudio(directory, initial.Audio, new AudioSettingsValues(musicVolume: -1), out _));
            Assert(File.ReadAllText(path, Gbk) == xml, "invalid requested value modified the file");
            File.Move(path, path + ".fixture");
            Throws<FileNotFoundException>(() => GameSettingsFileService.Read(directory));
        }

        private static void TestWriteFailure(string directory)
        {
            var path = ConfigPath(directory);
            var before = File.ReadAllBytes(path);
            var initial = GameSettingsFileService.Read(directory);
            using (File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read))
                Throws<IOException>(() => GameSettingsFileService.SaveAudio(directory, initial.Audio, new AudioSettingsValues(), out _));
            Assert(File.ReadAllBytes(path).SequenceEqual(before)
                && Directory.GetFiles(Path.GetDirectoryName(path), "*.tmp").Length == 0, "failed file save corrupted configuration or left temporary files");
        }

        private static void TestUtf8(string directory)
        {
            var path = ConfigPath(directory);
            var xml = File.ReadAllText(path, Gbk).Replace("encoding=\"gb2312\"", "encoding=\"utf-8\"");
            foreach (var bom in new[] { false, true })
            {
                File.WriteAllText(path, xml, new UTF8Encoding(bom));
                var initial = GameSettingsFileService.Read(directory);
                Assert(GameSettingsFileService.SaveAudio(directory, initial.Audio, new AudioSettingsValues(), out _), "UTF-8 settings save");
                Assert(File.ReadAllBytes(path).Take(3).SequenceEqual(new byte[] { 0xef, 0xbb, 0xbf }) == bom
                    && File.ReadAllText(path, Encoding.UTF8).Contains("保留中文配置"), "UTF-8 BOM or text was not preserved");
            }
        }

        private static XmlDocument Load(string path)
        {
            var document = new XmlDocument { XmlResolver = null };
            document.Load(path);
            return document;
        }

        private static void Throws<T>(Action action) where T : Exception
        {
            try { action(); }
            catch (T) { return; }
            throw new InvalidOperationException("Expected " + typeof(T).Name);
        }
    }
}
