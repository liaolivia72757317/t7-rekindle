using System;
using System.IO;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace T7.Rekindle.Desktop.Services
{
    internal sealed class ReleaseUpdateService
    {
        private readonly HttpClient _client;
        private readonly Uri _repository;
        private readonly string _mirror;

        internal ReleaseUpdateService(HttpClient client, string repositoryAddress, string mirrorAddress)
        {
            _client = client ?? throw new ArgumentNullException(nameof(client));
            _repository = ReleaseMetadata.Repository(repositoryAddress);
            _mirror = mirrorAddress;
        }

        internal Task<LauncherUpdateInfo> CheckAsync(string currentVersion) =>
            CheckAsync(new LauncherBuild(UpdateChannel.Stable, currentVersion), UpdateChannel.Stable);

        internal async Task<LauncherUpdateInfo> CheckAsync(LauncherBuild build, UpdateChannel channel)
        {
            if (channel == UpdateChannel.Preview)
                return await new PreviewUpdateService(_client, _repository, _mirror).CheckAsync(build).ConfigureAwait(false);
            if (channel != UpdateChannel.Stable) throw new ArgumentOutOfRangeException(nameof(channel));
            var currentVersion = build.Version;
            var current = ReleaseMetadata.ParseVersion(currentVersion);
            Exception mirrorFailure = null;
            if (!string.IsNullOrWhiteSpace(_mirror))
            {
                try { return await CheckMirrorAsync(build, current).ConfigureAwait(false); }
                catch (Exception error) when (error is IOException || error is InvalidDataException || error is TimeoutException)
                {
                    mirrorFailure = error;
                }
            }
            try
            {
                var info = await new GitHubReleaseUpdateService(_client, _repository.AbsoluteUri)
                    .CheckAsync(build).ConfigureAwait(false);
                if (mirrorFailure != null)
                    info.SourceNotice = "R2 检查失败，已使用 GitHub：" + mirrorFailure.Message;
                return info;
            }
            catch (Exception error) when (mirrorFailure != null && (error is IOException || error is InvalidDataException || error is TimeoutException))
            {
                throw new IOException("R2：" + mirrorFailure.Message + "\nGitHub：" + error.Message, error);
            }
        }

        private async Task<LauncherUpdateInfo> CheckMirrorAsync(LauncherBuild build, Version current)
        {
            var origin = ReleaseMetadata.MirrorOrigin(_mirror);
            using (var request = new HttpRequestMessage(HttpMethod.Get, new Uri(origin, "updates/stable.json")))
            {
                request.Headers.UserAgent.ParseAdd("T7-Rekindle/" + current.ToString(3));
                request.Headers.Accept.ParseAdd("application/json");
                request.Headers.CacheControl = new CacheControlHeaderValue { NoCache = true };
                try
                {
                    using (var response = await _client.SendAsync(request).ConfigureAwait(false))
                    {
                        if (!response.IsSuccessStatusCode)
                            throw new IOException("更新服务返回 HTTP " + (int)response.StatusCode + "。");
                        var json = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                        var manifest = JsonConvert.DeserializeObject<Manifest>(json);
                        if (manifest?.SchemaVersion != 1 || manifest.Installer == null)
                            throw new InvalidDataException("更新清单缺少安装包或版本格式不受支持。");
                        ReleaseMetadata.ParseVersion(manifest.Version);
                        var expected = new Uri(origin, "releases/" + Uri.EscapeDataString(manifest.Version)
                            + "/" + Uri.EscapeDataString(ReleaseMetadata.InstallerName(manifest.Version))).AbsoluteUri;
                        if (!ReleaseMetadata.IsSameObject(manifest.Installer.Url, expected))
                            throw new InvalidDataException("R2 安装包地址与当前镜像或版本不一致。");
                        var info = new LauncherUpdateInfo
                        {
                            CurrentVersion = build.Version,
                            TargetVersion = manifest.Version,
                            Summary = string.IsNullOrWhiteSpace(manifest.Summary) ? "该版本未填写更新说明。" : manifest.Summary.Trim(),
                            DownloadAddress = _repository.AbsoluteUri.TrimEnd('/') + "/releases/tag/" + Uri.EscapeDataString(manifest.Version),
                            UpdateSource = "R2",
                            Installer = new LauncherUpdateAsset(expected, ReleaseMetadata.GitHubAsset(_repository, manifest.Version),
                                manifest.Installer.Size, manifest.Installer.Sha256, "R2")
                        };
                        build.ApplyTo(info);
                        var notes = await new ReleaseNotesService(_client, _repository)
                            .ReadMirrorAsync(info, origin, manifest.Versions).ConfigureAwait(false);
                        info.ReleaseNotes = notes.Entries;
                        info.ReleaseNotesNotice = notes.Notice;
                        return info;
                    }
                }
                catch (TaskCanceledException error) { throw new TimeoutException("检查更新超时。", error); }
                catch (HttpRequestException error) { throw new IOException("连接更新镜像失败。", error); }
                catch (JsonException error) { throw new InvalidDataException("更新清单格式不正确。", error); }
            }
        }

        private sealed class Manifest
        {
            [JsonProperty("schemaVersion")] public int? SchemaVersion { get; set; }
            [JsonProperty("version")] public string Version { get; set; }
            [JsonProperty("summary")] public string Summary { get; set; }
            [JsonProperty("versions")] public JToken Versions { get; set; }
            [JsonProperty("installer")] public Asset Installer { get; set; }
        }

        private sealed class Asset
        {
            [JsonProperty("url")] public string Url { get; set; }
            [JsonProperty("size")] public long Size { get; set; }
            [JsonProperty("sha256")] public string Sha256 { get; set; }
        }
    }
}
