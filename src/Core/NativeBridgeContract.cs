using System;
using System.IO;
using System.Text;

namespace T7.Rekindle.Core
{
    public static class NativeBridgeContract
    {
        public const uint AbiVersion = 1;
        public const uint StructPack = 8;
        public const string LibraryName = "T7.NativeBridge.dll";

        public static bool IsUtf8PathAcceptable(string path)
        {
            if (string.IsNullOrWhiteSpace(path) || path.IndexOf('\0') >= 0
                || path.IndexOf('\r') >= 0 || path.IndexOf('\n') >= 0)
            {
                return false;
            }

            if (!IsFullyQualifiedPath(path))
            {
                return false;
            }

            try
            {
                return new UTF8Encoding(false, true).GetByteCount(path) <= 32768;
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

            // .NET Framework may treat drive-relative C:client and a single
            // root-relative slash as rooted; native std::filesystem does not.
            if (path.Length >= 2 && path[0] == '\\' && path[1] == '\\')
            {
                return path.Length >= 3 && path[2] != '\\' && path[2] != '/';
            }

            return path.Length >= 3 && char.IsLetter(path[0]) && path[1] == ':'
                && (path[2] == '\\' || path[2] == '/');
        }
    }
}
