using System;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using Newtonsoft.Json;

namespace T7.Rekindle.Desktop.Services
{
    internal sealed class GitHubReleaseUpdateService
    {
        private readonly HttpClient _client;
        private readonly Uri _repository;

        internal GitHubReleaseUpdateService(HttpClient client, string repositoryAddress)
        {
            _client = client ?? throw new ArgumentNullException(nameof(client));
            _repository = ReleaseMetadata.Repository(repositoryAddress);
        }

        internal async Task<LauncherUpdateInfo> CheckAsync(string currentVersion)
        {
            var current = ReleaseMetadata.ParseVersion(currentVersion);
            var endpoint = "https://api.github.com/repos" + _repository.AbsolutePath.TrimEnd('/') + "/releases/latest";
            using (var request = new HttpRequestMessage(HttpMethod.Get, endpoint))
            {
                request.Headers.UserAgent.ParseAdd("T7-Rekindle/" + current.ToString(3));
                request.Headers.Accept.ParseAdd("application/vnd.github+json");
                request.Headers.Add("X-GitHub-Api-Version", "2026-03-10");
                try
                {
                    using (var response = await _client.SendAsync(request).ConfigureAwait(false))
                    {
                        if (response.StatusCode == HttpStatusCode.NotFound)
                            return new LauncherUpdateInfo
                            {
                                CurrentVersion = currentVersion,
                                TargetVersion = "未发布",
                                HasPublishedRelease = false,
                                UpdateSource = "GitHub",
                                Summary = "GitHub Releases 暂无正式版本。",
                                DownloadAddress = _repository.AbsoluteUri.TrimEnd('/') + "/releases"
                            };
                        if (response.StatusCode == HttpStatusCode.Forbidden || (int)response.StatusCode == 429)
                            throw new IOException("GitHub 请求受限，请稍后重试。");
                        if (!response.IsSuccessStatusCode)
                            throw new IOException("GitHub 更新服务返回 HTTP " + (int)response.StatusCode + "，请稍后重试。");
                        var json = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                        return ReadRelease(json, currentVersion, current);
                    }
                }
                catch (TaskCanceledException error)
                {
                    throw new TimeoutException("检查更新超时，请检查网络后重试。", error);
                }
                catch (HttpRequestException error)
                {
                    throw new IOException("连接 GitHub 失败，请检查网络后重试。", error);
                }
                catch (JsonException error)
                {
                    throw new InvalidDataException("GitHub 返回的版本信息格式不正确，请稍后重试。", error);
                }
            }
        }

        private LauncherUpdateInfo ReadRelease(string json, string currentVersion, Version current)
        {
            var release = JsonConvert.DeserializeObject<Release>(json);
            if (release == null || release.Draft != false || release.Prerelease != false)
                throw new InvalidDataException("GitHub 返回的不是正式发布版本，请到发布页确认。");
            var target = ReleaseMetadata.ParseVersion(release.TagName);
            if (!Uri.TryCreate(release.HtmlUrl, UriKind.Absolute, out var address)
                || address.Scheme != Uri.UriSchemeHttps || address.Host != _repository.Host
                || !address.IsDefaultPort || address.UserInfo.Length != 0
                || !address.AbsolutePath.StartsWith(_repository.AbsolutePath.TrimEnd('/') + "/releases/", StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("GitHub 返回的发布地址不属于当前项目，请到项目主页确认。");
            return new LauncherUpdateInfo
            {
                CurrentVersion = currentVersion,
                TargetVersion = release.TagName,
                IsNewVersion = target > current,
                IsCurrentVersionAhead = current > target,
                Summary = string.IsNullOrWhiteSpace(release.Body) ? "该版本未填写更新说明，请到发布页查看。" : release.Body.Trim(),
                DownloadAddress = address.AbsoluteUri,
                UpdateSource = "GitHub",
                Installer = ReadInstaller(release)
            };
        }

        private LauncherUpdateAsset ReadInstaller(Release release)
        {
            var matches = (release.Assets ?? new Asset[0]).Where(asset => asset != null
                && asset.Name == ReleaseMetadata.InstallerName(release.TagName)).ToArray();
            if (matches.Length == 0) return null;
            if (matches.Length != 1) throw new InvalidDataException("GitHub 安装包名称重复。");
            var installer = matches[0];
            if (installer.State != "uploaded" || string.IsNullOrWhiteSpace(installer.Digest)) return null;
            if (!Regex.IsMatch(installer.Digest, @"\Asha256:[0-9a-fA-F]{64}\z", RegexOptions.IgnoreCase))
                return null;
            var expected = ReleaseMetadata.GitHubAsset(_repository, release.TagName);
            if (!ReleaseMetadata.IsSameObject(installer.BrowserDownloadUrl, expected))
                throw new InvalidDataException("GitHub 安装包地址不属于当前项目或版本。");
            return new LauncherUpdateAsset(expected, null, installer.Size, installer.Digest.Substring(7), "GitHub");
        }

        private sealed class Release
        {
            [JsonProperty("tag_name")] public string TagName { get; set; }
            [JsonProperty("html_url")] public string HtmlUrl { get; set; }
            [JsonProperty("body")] public string Body { get; set; }
            [JsonProperty("draft")] public bool? Draft { get; set; }
            [JsonProperty("prerelease")] public bool? Prerelease { get; set; }
            [JsonProperty("assets")] public Asset[] Assets { get; set; }
        }

        private sealed class Asset
        {
            [JsonProperty("name")] public string Name { get; set; }
            [JsonProperty("state")] public string State { get; set; }
            [JsonProperty("browser_download_url")] public string BrowserDownloadUrl { get; set; }
            [JsonProperty("size")] public long Size { get; set; }
            [JsonProperty("digest")] public string Digest { get; set; }
        }
    }
}
