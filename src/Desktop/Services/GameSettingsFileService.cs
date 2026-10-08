using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Xml;
using T7.Rekindle.Core;

namespace T7.Rekindle.Desktop.Services
{
    internal sealed class GameSettingsFileSnapshot
    {
        public GameSettingsFileSnapshot(GraphicsSettingsValues graphics, AudioSettingsValues audio)
        { Graphics = graphics; Audio = audio; }
        public GraphicsSettingsValues Graphics { get; }
        public AudioSettingsValues Audio { get; }
    }

    internal static class GameSettingsFileService
    {
        internal static GameSettingsFileSnapshot Read(string binaryDirectory) => Load(binaryDirectory).Read();

        internal static bool SaveGraphics(string directory, GraphicsSettingsValues expected, GraphicsSettingsValues values,
            out GameSettingsFileSnapshot current)
        {
            if (values == null || !values.IsValid) throw new ArgumentException("画面设置超出有效范围。", nameof(values));
            return Save(directory, snapshot => snapshot.Graphics.Equals(expected), document =>
            {
                document.Set("TargetSize", "str", values.Resolution);
                document.Set("FullScreen", "bool", Boolean(values.FullScreen));
                document.Set("ConfigLevel", "i32", Number(4 - values.Quality));
                document.Set("vSync", "bool", Boolean(values.VerticalSync));
                document.Set("enable_distance_fog", "bool", Boolean(values.Fog));
                document.Set("distance_fog_end", "f32", Number(values.ViewDistance));
                document.Set("ragDollEnable", "bool", Boolean(values.RagDoll));
                document.Set("FPS", "i32", values.FrameLimit ? "60" : "200");
                document.Set("Swoosh", "i32", Number(values.Swoosh));
            }, out current);
        }

        internal static bool SaveAudio(string directory, AudioSettingsValues expected, AudioSettingsValues values,
            out GameSettingsFileSnapshot current)
        {
            if (values == null || !values.IsValid) throw new ArgumentException("声音设置超出有效范围。", nameof(values));
            return Save(directory, snapshot => snapshot.Audio.Equals(expected), document =>
            {
                document.Set("MusicMute", "bool", Boolean(values.MusicMuted));
                document.Set("MusicVolume", "f32", values.MusicVolume.ToString("R", CultureInfo.InvariantCulture));
                document.Set("AudioMute", "bool", Boolean(values.EffectsMuted));
                document.Set("AudioVolume", "f32", values.EffectsVolume.ToString("R", CultureInfo.InvariantCulture));
            }, out current);
        }

        internal static bool IsClientRunning(string binaryDirectory)
        {
            var executable = Path.GetFullPath(Path.Combine(binaryDirectory, "TieJiClient.exe"));
            var processes = Process.GetProcessesByName("TieJiClient");
            try
            {
                foreach (var process in processes)
                {
                    try
                    {
                        if (!process.HasExited && string.Equals(process.MainModule.FileName, executable, StringComparison.OrdinalIgnoreCase))
                            return true;
                    }
                    catch (InvalidOperationException) { /* The process exited during inspection. */ }
                    catch (Win32Exception error) { throw new IOException("检查游戏进程失败，文件编辑已暂停。", error); }
                }
                return false;
            }
            finally { foreach (var process in processes) process.Dispose(); }
        }

        private static string Boolean(bool value) => value ? "true" : "false";
        private static string Number(uint value) => value.ToString(CultureInfo.InvariantCulture);

        private static Document Load(string binaryDirectory)
        {
            if (string.IsNullOrWhiteSpace(binaryDirectory)) throw new ArgumentException("请先选择有效的游戏目录。", nameof(binaryDirectory));
            return new Document(Path.GetFullPath(Path.Combine(binaryDirectory, "..", "Data", "UserData", "UserData.cfg")));
        }

        private static bool Save(string directory, Func<GameSettingsFileSnapshot, bool> matches,
            Action<Document> update, out GameSettingsFileSnapshot current)
        {
            var document = Load(directory);
            current = document.Read();
            if (!matches(current)) return false;
            update(document);
            var temporary = document.Path + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    document.Write(stream);
                    stream.Flush(true);
                }
                if (!File.ReadAllBytes(document.Path).SequenceEqual(document.Original))
                {
                    current = Read(directory);
                    return false;
                }
                File.Replace(temporary, document.Path, document.Path + ".bak", true);
                current = Read(directory);
                return true;
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }

        private sealed class Document
        {
            private readonly XmlDocument _xml = new XmlDocument { PreserveWhitespace = true, XmlResolver = null };
            private readonly XmlElement _header, _record;
            private readonly Encoding _encoding;
            public string Path { get; }
            public byte[] Original { get; }

            public Document(string path)
            {
                Path = path;
                Original = File.ReadAllBytes(path);
                using (var stream = new MemoryStream(Original, false))
                using (var reader = XmlReader.Create(stream, new XmlReaderSettings { DtdProcessing = DtdProcessing.Prohibit, XmlResolver = null }))
                    _xml.Load(reader);
                var root = _xml.DocumentElement;
                if (root?.Name != "PropertySheet" || root.GetAttribute("Version") != "100") throw Invalid("PropertySheet");
                _header = Element(root, "Header", true);
                _record = Element(root, "Record[@Name='Config']", true);
                var name = (_xml.FirstChild as XmlDeclaration)?.Encoding;
                _encoding = Encoding.GetEncoding(string.IsNullOrEmpty(name) ? "utf-8" : name,
                    EncoderFallback.ExceptionFallback, DecoderFallback.ExceptionFallback);
                if (_encoding.CodePage == 65001)
                    _encoding = new UTF8Encoding(Original.Take(3).SequenceEqual(new byte[] { 0xef, 0xbb, 0xbf }), true);
                else if (_encoding.CodePage == 1200 || _encoding.CodePage == 1201)
                    _encoding = new UnicodeEncoding(_encoding.CodePage == 1201,
                        Original.Take(2).SequenceEqual(_encoding.GetPreamble()), true);
            }

            public GameSettingsFileSnapshot Read()
            {
                var size = Get("TargetSize", "str").Split('x');
                if (size.Length != 2 || !uint.TryParse(size[0], NumberStyles.None, CultureInfo.InvariantCulture, out var width)
                    || !uint.TryParse(size[1], NumberStyles.None, CultureInfo.InvariantCulture, out var height)) throw Invalid("TargetSize");
                var level = Integer("ConfigLevel");
                var fps = Integer("FPS");
                var distance = Float("distance_fog_end");
                if (level > 4 || (fps != 60 && fps != 200) || distance < 32 || distance > 1024
                    || Math.Floor(distance) != distance) throw Invalid("ConfigLevel / FPS / distance_fog_end");
                var graphics = new GraphicsSettingsValues(width, height, Bool("FullScreen"), 4 - level, Bool("vSync"),
                    Bool("enable_distance_fog"), (uint)distance, Bool("ragDollEnable"), fps == 60, Integer("Swoosh", "0"));
                var audio = new AudioSettingsValues(Bool("MusicMute"), Float("MusicVolume"), Bool("AudioMute"), Float("AudioVolume"));
                if (!graphics.IsValid || !audio.IsValid) throw Invalid("画面或声音设置范围");
                return new GameSettingsFileSnapshot(graphics, audio);
            }

            private string Get(string key, string type, string fallback = null)
            {
                var header = Element(_header, key, false);
                var value = Element(_record, key, false);
                if (header == null && value == null && fallback != null) return fallback;
                if (header == null || header.GetAttribute("Type") != type) throw Invalid(key);
                var source = value ?? header;
                if (!source.HasAttribute("Value")) throw Invalid(key);
                return source.GetAttribute("Value");
            }

            private uint Integer(string key, string fallback = null)
            {
                if (!uint.TryParse(Get(key, "i32", fallback), NumberStyles.Integer, CultureInfo.InvariantCulture, out var value)) throw Invalid(key);
                return value;
            }
            private bool Bool(string key)
            {
                if (!bool.TryParse(Get(key, "bool"), out var value)) throw Invalid(key);
                return value;
            }
            private float Float(string key)
            {
                if (!float.TryParse(Get(key, "f32"), NumberStyles.Float, CultureInfo.InvariantCulture, out var value)
                    || float.IsNaN(value) || float.IsInfinity(value)) throw Invalid(key);
                return value;
            }

            public void Set(string key, string type, string value)
            {
                if (Element(_header, key, false) == null)
                {
                    var header = Add(_header, key);
                    header.SetAttribute("Type", type);
                    header.SetAttribute("Value", "0");
                }
                (Element(_record, key, false) ?? Add(_record, key)).SetAttribute("Value", value);
            }

            private XmlElement Add(XmlElement parent, string key)
            {
                var element = _xml.CreateElement(key);
                if (parent.LastChild is XmlWhitespace trailing)
                {
                    parent.InsertBefore(_xml.CreateWhitespace(trailing.Value + "\t"), trailing);
                    parent.InsertBefore(element, trailing);
                }
                else parent.AppendChild(element);
                return element;
            }

            public void Write(Stream stream)
            {
                using (var writer = XmlWriter.Create(stream, new XmlWriterSettings
                { Encoding = _encoding, Indent = false, NewLineHandling = NewLineHandling.None, CloseOutput = false }))
                    _xml.Save(writer);
            }

            private static XmlElement Element(XmlElement parent, string query, bool required)
            {
                var nodes = parent.SelectNodes(query);
                if (nodes.Count > 1 || (required && nodes.Count != 1)) throw Invalid(query);
                return nodes.Count == 0 ? null : (XmlElement)nodes[0];
            }
            private static InvalidDataException Invalid(string field) => new InvalidDataException("游戏配置文件中的 " + field + " 无效，请检查 UserData.cfg。");
        }
    }
}
