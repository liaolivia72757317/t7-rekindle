using System;
using System.Collections.Generic;
using System.IO;
using System.Threading;
using T7.Rekindle.Core;

namespace T7.Rekindle.Desktop.Services
{
    public sealed class ClientDirectoryResult
    {
        public ClientDirectoryResult(string root, string directory, string message)
        {
            Root = root;
            Directory = directory;
            Message = message;
        }

        public string Root { get; }
        public string Directory { get; }
        public string Message { get; }
        public bool IsValid => Directory.Length != 0;
    }

    public static class ClientDirectoryService
    {
        internal static ClientDirectoryResult Locate(string input, CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var path = (input ?? string.Empty).Trim();
            var selected = Inspect(path);
            if (selected.IsValid || !NativeBridgeContract.IsUtf8PathAcceptable(path) || !System.IO.Directory.Exists(path))
                return selected;

            var pending = new Queue<string>();
            pending.Enqueue(path);
            var skipped = 0;
            while (pending.Count != 0)
            {
                cancellationToken.ThrowIfCancellationRequested();
                var current = pending.Dequeue();
                try
                {
                    // Child links can leave the selected tree or form a cycle.
                    if (current != path && (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                    {
                        skipped++;
                        continue;
                    }
                    var result = current == path ? selected : Inspect(current);
                    if (result.IsValid)
                        return new ClientDirectoryResult(result.Root, result.Directory,
                            "已自动定位游戏目录。" + result.Message + SkippedDirectoriesMessage(skipped));
                    var children = System.IO.Directory.GetDirectories(current);
                    Array.Sort(children, StringComparer.OrdinalIgnoreCase);
                    foreach (var child in children)
                    {
                        cancellationToken.ThrowIfCancellationRequested();
                        pending.Enqueue(child);
                    }
                }
                catch (Exception error) when (IsDirectoryError(error)) { skipped++; }
            }
            return Invalid("未在所选目录及其子目录中找到完整客户端。" + SkippedDirectoriesMessage(skipped) + "请重新选择更精确的目录。");
        }

        public static ClientDirectoryResult Inspect(string input)
        {
            var path = (input ?? string.Empty).Trim();
            if (path.Length == 0) return Invalid("请选择游戏根目录，目录中应包含 Bin、Data 和 vfs。");
            if (!NativeBridgeContract.IsUtf8PathAcceptable(path)) return Invalid("请输入完整的游戏目录路径。");
            try
            {
                path = Path.GetFullPath(path);
                if (path.Length > Path.GetPathRoot(path).Length) path = path.TrimEnd('\\', '/');
                if (!System.IO.Directory.Exists(path)) return Invalid("此目录不存在，请重新选择。");
                var bin = Path.Combine(path, "Bin");
                if (HasClient(bin) && HasResources(path))
                    return new ClientDirectoryResult(path, bin, "已找到 Bin\\TieJiClient.exe");
                var parent = System.IO.Directory.GetParent(path)?.FullName;
                if (HasClient(path) && parent != null && HasResources(parent))
                {
                    var isBin = string.Equals(Path.GetFileName(path.TrimEnd('\\', '/')), "Bin", StringComparison.OrdinalIgnoreCase);
                    return new ClientDirectoryResult(isBin ? parent : path, path,
                        isBin ? "已找到 Bin\\TieJiClient.exe" : "已找到 TieJiClient.exe 及上级目录中的资源");
                }
                return Invalid("未找到完整客户端：需要 Bin 内的 TieJiClient.exe、ProtocalHandler.dll，以及根目录下的 Data、vfs。");
            }
            catch (Exception error) when (IsDirectoryError(error))
            {
                return Invalid("目录检查失败：" + error.Message);
            }
        }

        private static bool HasClient(string path) => File.Exists(Path.Combine(path, "TieJiClient.exe"))
            && File.Exists(Path.Combine(path, "ProtocalHandler.dll"));
        private static bool HasResources(string path) => System.IO.Directory.Exists(Path.Combine(path, "Data"))
            && System.IO.Directory.Exists(Path.Combine(path, "vfs"));
        private static bool IsDirectoryError(Exception error) => error is IOException || error is UnauthorizedAccessException
            || error is ArgumentException || error is NotSupportedException || error is System.Security.SecurityException;
        private static string SkippedDirectoriesMessage(int count) => count == 0 ? string.Empty
            : "已跳过 " + count + " 个不可访问或链接目录。";
        private static ClientDirectoryResult Invalid(string message) => new ClientDirectoryResult(string.Empty, string.Empty, message);
    }
}
