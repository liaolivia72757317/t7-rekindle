using System;
using System.IO;
using System.Text.RegularExpressions;

namespace T7.Rekindle.Desktop.Services
{
    public sealed class LauncherUpdateAsset
    {
        internal LauncherUpdateAsset(string downloadAddress, string fallbackAddress, long size, string sha256, string source)
        {
            ReleaseMetadata.HttpsAddress(downloadAddress);
            if (fallbackAddress != null) ReleaseMetadata.HttpsAddress(fallbackAddress);
            if (size <= 0 || !Regex.IsMatch(sha256 ?? "", @"\A[0-9a-fA-F]{64}\z"))
                throw new InvalidDataException("安装包大小或 SHA-256 校验信息无效。");
            DownloadAddress = downloadAddress;
            FallbackAddress = fallbackAddress;
            Size = size;
            Sha256 = sha256.ToLowerInvariant();
            Source = source;
        }

        public string DownloadAddress { get; }
        public string FallbackAddress { get; }
        public long Size { get; }
        public string Sha256 { get; }
        public string Source { get; }
    }

    internal static class ReleaseMetadata
    {
        internal static string InstallerName(string tag) => "T7-Rekindle-" + tag + "-Setup.exe";

        internal static Version ParseVersion(string value)
        {
            var text = (value ?? string.Empty).Trim();
            if (!Regex.IsMatch(text, @"\A[vV]?[0-9]+\.[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\z"))
                throw new InvalidDataException("版本号格式不受支持；支持 v主版本.次版本.修订号。");
            text = text.TrimStart('v', 'V').Split('+')[0];
            if (!Version.TryParse(text, out var version) || version.Major > 65534 || version.Minor > 65534
                || version.Build > 65534 || version.Revision > 65534)
                throw new InvalidDataException("版本号超出支持范围，请到发布页确认。");
            return new Version(version.Major, version.Minor, version.Build, Math.Max(version.Revision, 0));
        }

        internal static Uri Repository(string value)
        {
            if (!Uri.TryCreate(value, UriKind.Absolute, out var address)
                || address.Scheme != Uri.UriSchemeHttps || address.Host != "github.com"
                || !address.IsDefaultPort || address.UserInfo.Length != 0
                || address.Query.Length != 0 || address.Fragment.Length != 0
                || address.AbsolutePath.Trim('/').Split('/').Length != 2)
                throw new ArgumentException("项目仓库地址不是有效的 GitHub 仓库地址。", nameof(value));
            return address;
        }

        internal static Uri HttpsAddress(string value)
        {
            if (!Uri.TryCreate(value, UriKind.Absolute, out var address) || address.Scheme != Uri.UriSchemeHttps
                || !address.IsDefaultPort || address.UserInfo.Length != 0 || address.Fragment.Length != 0)
                throw new InvalidDataException("更新地址不是有效的 HTTPS 地址。");
            return address;
        }

        internal static Uri MirrorOrigin(string value)
        {
            var address = HttpsAddress(value);
            if (address.AbsolutePath != "/" || address.Query.Length != 0)
                throw new InvalidDataException("R2 更新地址必须是独立的 HTTPS 域名。");
            return address;
        }

        internal static string GitHubAsset(Uri repository, string version) =>
            repository.AbsoluteUri.TrimEnd('/') + "/releases/download/" + Uri.EscapeDataString(version)
            + "/" + Uri.EscapeDataString(InstallerName(version));

        internal static bool IsSameObject(string actual, string expected)
        {
            if (!Uri.TryCreate(actual, UriKind.Absolute, out var address)) return false;
            var target = new Uri(expected);
            return address.Scheme == Uri.UriSchemeHttps && address.UserInfo.Length == 0
                && address.IsDefaultPort && address.Host == target.Host
                && address.Query.Length == 0 && address.Fragment.Length == 0
                && Uri.UnescapeDataString(address.AbsolutePath) == Uri.UnescapeDataString(target.AbsolutePath);
        }
    }
}
