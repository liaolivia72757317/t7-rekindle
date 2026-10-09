using System;
using System.Collections.Generic;
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
        public UpdateChannel Channel { get; set; }
        public UpdateChannel CurrentChannel { get; set; }
        internal LauncherBuild TargetBuild { get; set; }
        public string TargetDisplayVersion => TargetBuild?.DisplayVersion ?? TargetVersion;
        public string UpdateIdentity => TargetBuild?.Identity ?? (Channel == UpdateChannel.Preview ? "preview:" : "stable:") + TargetVersion;
        public string Summary { get; set; }
        public IReadOnlyList<LauncherReleaseNote> ReleaseNotes { get; set; } = Array.Empty<LauncherReleaseNote>();
        public string ReleaseNotesNotice { get; set; } = string.Empty;
        public string DownloadAddress { get; set; }
        public LauncherUpdateAsset Installer { get; set; }
        public string UpdateSource { get; set; } = string.Empty;
        public string SourceNotice { get; set; } = string.Empty;
        public bool IsNewVersion { get; set; }
        public bool HasPublishedRelease { get; set; } = true;
        public bool IsCurrentVersionAhead { get; set; }
        public string StatusText => !HasPublishedRelease ? (Channel == UpdateChannel.Preview ? "暂无预览构建" : "暂无正式发布版本")
            : IsNewVersion ? "发现新版本"
            : IsCurrentVersionAhead ? (Channel == UpdateChannel.Preview ? "当前构建高于最新预览构建" : "当前版本高于最新发布版") : "已是最新版本";
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
        internal static LauncherBuild CurrentBuild => new LauncherBuild(
            ReadMetadata("BuildChannel") == "stable" ? UpdateChannel.Stable : UpdateChannel.Preview,
            Version, ReadNumber("PreviewRunId"), ReadNumber("PreviewRunNumber"),
            checked((int)ReadNumber("PreviewRunAttempt")), CommitHash);
        public static string DisplayVersion => CurrentBuild.DisplayVersion;

        public static Task<LauncherUpdateInfo> CheckUpdateAsync() => CheckUpdateAsync(UpdateChannel.Stable);

        public static Task<LauncherUpdateInfo> CheckUpdateAsync(UpdateChannel channel) =>
            new ReleaseUpdateService(UpdateClient, RepositoryAddress, UpdateBaseAddress).CheckAsync(CurrentBuild, channel);

        public static Task<IReadOnlyList<LauncherHistoryEntry>> GetHistoryAsync(UpdateChannel channel, CancellationToken cancellation) =>
            new ReleaseHistoryService(UpdateClient, RepositoryAddress, UpdateBaseAddress, CurrentBuild).GetHistoryAsync(channel, cancellation);

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
        private static long ReadNumber(string key) => long.TryParse(ReadMetadata(key), out var number) ? number : 0;
    }
}
