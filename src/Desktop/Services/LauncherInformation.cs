using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Threading.Tasks;

namespace T7.Rekindle.Desktop.Services
{
    public sealed class LauncherUpdateInfo
    {
        public string CurrentVersion { get; set; }
        public string TargetVersion { get; set; }
        public string Summary { get; set; }
        public string DownloadAddress { get; set; }
        public bool IsNewVersion { get; set; }
        public bool HasDownloadAddress => !string.IsNullOrWhiteSpace(DownloadAddress)
            && Uri.TryCreate(DownloadAddress, UriKind.Absolute, out var address)
            && (address.Scheme == Uri.UriSchemeHttps || address.Scheme == Uri.UriSchemeHttp);
    }

    public static class LauncherInformation
    {
        private static readonly Assembly Assembly = typeof(LauncherInformation).Assembly;
        public static string Version => "v" + Assembly.GetName().Version.ToString(3);
        public static string CommitHash => Assembly.GetCustomAttributes<AssemblyMetadataAttribute>()
            .FirstOrDefault(attribute => attribute.Key == "CommitHash")?.Value ?? string.Empty;
        public static string ShortHash => CommitHash.Length >= 7 ? CommitHash.Substring(0, Math.Min(10, CommitHash.Length)) : "提交未记录";
        public static string RepositoryAddress => ReadMetadata("RepositoryUrl");
        public static string DownloadAddress => ReadMetadata("DownloadUrl");

        public static Task<LauncherUpdateInfo> CheckSampleUpdateAsync() => Task.FromResult(new LauncherUpdateInfo
        {
            CurrentVersion = Version,
            TargetVersion = "v0.2.0",
            Summary = "• 优化启动页布局与状态反馈。\n• 改进目录检查及错误诊断。",
            DownloadAddress = DownloadAddress,
            IsNewVersion = true
        });

        public static string ReadDocument(string name)
        {
            using (var stream = Assembly.GetManifestResourceStream("T7.Rekindle.Documents." + name))
            {
                if (stream == null) throw new FileNotFoundException("缺少随应用打包的说明：" + name);
                using (var reader = new StreamReader(stream, Encoding.UTF8)) return reader.ReadToEnd();
            }
        }

        private static string ReadMetadata(string key) => Assembly.GetCustomAttributes<AssemblyMetadataAttribute>()
            .FirstOrDefault(attribute => attribute.Key == key)?.Value?.Trim() ?? string.Empty;
    }
}
