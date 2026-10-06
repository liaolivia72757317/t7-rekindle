using System;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace T7.Rekindle.Desktop.Services
{
    public sealed class LauncherUpdateInfo
    {
        public string CurrentVersion { get; set; }
        public string TargetVersion { get; set; }
        public string Summary { get; set; }
        public string DownloadAddress { get; set; }
        public LauncherUpdateAsset Installer { get; set; }
        public string UpdateSource { get; set; } = string.Empty;
        public string SourceNotice { get; set; } = string.Empty;
        public bool IsNewVersion { get; set; }
        public bool HasPublishedRelease { get; set; } = true;
        public bool IsCurrentVersionAhead { get; set; }
        public string StatusText => !HasPublishedRelease ? "暂无正式发布版本"
            : IsNewVersion ? "发现新版本"
            : IsCurrentVersionAhead ? "当前版本高于最新发布版" : "已是最新版本";
        public bool HasDownloadAddress => !string.IsNullOrWhiteSpace(DownloadAddress)
            && Uri.TryCreate(DownloadAddress, UriKind.Absolute, out var address)
            && address.Scheme == Uri.UriSchemeHttps && address.UserInfo.Length == 0;
    }

    public static class LauncherInformation
    {
        private static readonly Assembly Assembly = typeof(LauncherInformation).Assembly;
        private static readonly HttpClient UpdateClient = new HttpClient(new HttpClientHandler { AllowAutoRedirect = false })
            { Timeout = TimeSpan.FromSeconds(10) };
        private static readonly HttpClient DownloadClient = new HttpClient(new HttpClientHandler { AllowAutoRedirect = false })
            { Timeout = Timeout.InfiniteTimeSpan };
        public static string ProjectName => Assembly.GetCustomAttribute<AssemblyProductAttribute>()?.Product ?? "T7-Rekindle";
        public static string ProjectDescription => Assembly.GetCustomAttribute<AssemblyDescriptionAttribute>()?.Description ?? string.Empty;
        public static string Version => FormatVersion(Assembly.GetName().Version);
        public static string CommitHash => Assembly.GetCustomAttributes<AssemblyMetadataAttribute>()
            .FirstOrDefault(attribute => attribute.Key == "CommitHash")?.Value ?? string.Empty;
        public static string ShortHash => CommitHash.Length >= 7 ? CommitHash.Substring(0, Math.Min(10, CommitHash.Length)) : "提交未记录";
        public static string RepositoryAddress => ReadMetadata("RepositoryUrl");
        public static string DownloadAddress => ReadMetadata("DownloadUrl");
        public static string ClientDownloadAddress => "https://www.bilibili.com/opus/768784882628296761";
        public static string BuildsAddress => ReadMetadata("BuildsUrl");
        public static string ContactAddress => ReadMetadata("ContactUrl");
        public static string IssuesAddress => ReadMetadata("IssuesUrl");
        public static string UpdateBaseAddress => ReadMetadata("UpdateBaseUrl");

        public static Task<LauncherUpdateInfo> CheckUpdateAsync() =>
            new ReleaseUpdateService(UpdateClient, RepositoryAddress, UpdateBaseAddress).CheckAsync(Version);

        internal static Task<string> DownloadInstallerAsync(LauncherUpdateAsset asset,
            IProgress<UpdateDownloadProgress> progress, CancellationToken cancellation, UpdateDownloadControl control) =>
            new UpdateDownloadService(DownloadClient, Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "T7-Rekindle", "updates"))
                .DownloadAsync(asset, progress, cancellation, control);

        public static Task<LauncherUpdateInfo> CheckSampleUpdateAsync() => CheckUpdateAsync();

        internal static string FormatVersion(Version version) => "v" + version.ToString(version.Revision > 0 ? 4 : 3);

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
