using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.Serialization;
using T7R.Core;

namespace T7R.Battle
{
    internal sealed class LocalProfileStore
    {
        private readonly string _path;
        internal LocalProfileStore(string path)
        {
            if (!NativeBridgeContract.IsUtf8PathAcceptable(path)) throw new ArgumentException("Expected absolute offline profile path.", nameof(path));
            _path = Path.GetFullPath(path);
        }

        internal PlatformProfile Load(LocalProfileCatalogue catalogue, out IReadOnlyList<string> warnings)
        {
            var messages = new List<string>();
            foreach (var path in new[] { _path, _path + ".bak" })
            {
                try
                {
                    byte[] bytes;
                    using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
                    using (var data = new MemoryStream())
                    {
                        var block = new byte[8192];
                        int count;
                        while ((count = stream.Read(block, 0, block.Length)) != 0)
                        {
                            if (data.Length + count > PlatformProfileJson.MaxBytes) throw new InvalidDataException("Player profile exceeds size limit.");
                            data.Write(block, 0, count);
                        }
                        bytes = data.ToArray();
                    }
                    var profile = PlatformProfileJson.Read(bytes);
                    catalogue.Validate(profile);
                    if (path != _path) messages.Add("已从备份恢复武将配置。");
                    warnings = messages.AsReadOnly();
                    return profile;
                }
                catch (FileNotFoundException) { }
                catch (DirectoryNotFoundException) { }
                catch (Exception error) when (error is IOException || error is InvalidDataException || error is UnauthorizedAccessException || error is ArgumentException || error is SerializationException)
                { messages.Add("武将配置读取失败，原文件已保留：" + error.GetType().Name); }
            }
            if (messages.Count != 0) throw new InvalidDataException("武将配置及备份无效；请保留文件并修复后重试。");
            warnings = messages.AsReadOnly();
            return catalogue.Create();
        }

        internal void Save(PlatformProfile value, PlatformProfile previous)
        {
            // Serialize both before any disk change; publish in memory only after
            // replacing the primary. The backup is the last valid snapshot.
            var next = PlatformProfileJson.Write(value);
            var backup = PlatformProfileJson.Write(previous);
            Replace(_path + ".bak", backup);
            Replace(_path, next);
        }

        private static void Replace(string path, byte[] bytes)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            var temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    stream.Write(bytes, 0, bytes.Length);
                    stream.Flush(true);
                }
                if (File.Exists(path)) File.Replace(temporary, path, null);
                else File.Move(temporary, path);
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }
    }
}
