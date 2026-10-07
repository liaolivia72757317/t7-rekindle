using System;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Threading.Tasks;
using Newtonsoft.Json;

namespace T7.Rekindle.Desktop.Services
{
    internal sealed class PreviewUpdateService
    {
        private readonly HttpClient _client;
        private readonly Uri _repository;
        private readonly string _mirror;

        internal PreviewUpdateService(HttpClient client, Uri repository, string mirror)
        {
            _client = client;
            _repository = repository;
            _mirror = mirror;
        }

        internal async Task<LauncherUpdateInfo> CheckAsync(LauncherBuild current)
        {
            if (string.IsNullOrWhiteSpace(_mirror)) throw new IOException("当前构建未配置 R2 预览更新地址。");
            var origin = ReleaseMetadata.MirrorOrigin(_mirror);
            using (var request = new HttpRequestMessage(HttpMethod.Get, new Uri(origin, "updates/preview.json")))
            {
                request.Headers.UserAgent.ParseAdd("T7-Rekindle/" + ReleaseMetadata.ParseVersion(current.Version).ToString(3));
                request.Headers.Accept.ParseAdd("application/json");
                request.Headers.CacheControl = new CacheControlHeaderValue { NoCache = true };
                try
                {
                    using (var response = await _client.SendAsync(request).ConfigureAwait(false))
                    {
                        if (response.StatusCode == HttpStatusCode.NotFound)
                            return new LauncherUpdateInfo { CurrentVersion = current.Version, CurrentChannel = current.Channel,
                                Channel = UpdateChannel.Preview, HasPublishedRelease = false, UpdateSource = "R2", TargetVersion = "未发布" };
                        if (!response.IsSuccessStatusCode)
                            throw new IOException("预览更新服务返回 HTTP " + (int)response.StatusCode + "。");
                        var manifest = JsonConvert.DeserializeObject<Manifest>(await response.Content.ReadAsStringAsync().ConfigureAwait(false));
                        if (manifest?.SchemaVersion != 1 || manifest.Channel != "preview" || manifest.Build?.Channel != "preview"
                            || manifest.Installer == null)
                            throw new InvalidDataException("预览更新清单格式不正确。");
                        var identity = manifest.Build;
                        var target = new LauncherBuild(UpdateChannel.Preview, manifest.Version, identity.RunId,
                            identity.RunNumber, identity.RunAttempt, identity.CommitHash);
                        if (!target.HasPreviewIdentity || ReleaseMetadata.ParseVersion(identity.Version) != ReleaseMetadata.ParseVersion(manifest.Version))
                            throw new InvalidDataException("预览构建身份与清单不一致。");
                        var path = "previews/" + target.RunId.ToString(CultureInfo.InvariantCulture) + "/"
                            + target.RunAttempt.ToString(CultureInfo.InvariantCulture) + "/T7-Rekindle-Setup.exe";
                        var expected = new Uri(origin, path).AbsoluteUri;
                        if (!ReleaseMetadata.IsSameObject(manifest.Installer.Url, expected))
                            throw new InvalidDataException("预览安装包地址与当前镜像或构建不一致。");
                        var info = new LauncherUpdateInfo
                        {
                            Channel = UpdateChannel.Preview, TargetVersion = manifest.Version, TargetBuild = target,
                            Summary = string.IsNullOrWhiteSpace(manifest.Summary) ? "该构建未填写说明。" : manifest.Summary.Trim(),
                            UpdateSource = "R2", DownloadAddress = _repository.AbsoluteUri.TrimEnd('/') + "/actions/runs/"
                                + target.RunId.ToString(CultureInfo.InvariantCulture),
                            Installer = new LauncherUpdateAsset(expected, null, manifest.Installer.Size, manifest.Installer.Sha256, "R2")
                        };
                        current.ApplyTo(info);
                        return info;
                    }
                }
                catch (TaskCanceledException error) { throw new TimeoutException("检查预览更新超时，请稍后重试。", error); }
                catch (HttpRequestException error) { throw new IOException("连接预览更新镜像失败。", error); }
                catch (JsonException error) { throw new InvalidDataException("预览更新清单格式不正确。", error); }
            }
        }

        private sealed class Manifest
        {
            [JsonProperty("schemaVersion")] public int? SchemaVersion { get; set; }
            [JsonProperty("channel")] public string Channel { get; set; }
            [JsonProperty("version")] public string Version { get; set; }
            [JsonProperty("summary")] public string Summary { get; set; }
            [JsonProperty("build")] public BuildIdentity Build { get; set; }
            [JsonProperty("installer")] public Asset Installer { get; set; }
        }

        private sealed class BuildIdentity
        {
            [JsonProperty("channel")] public string Channel { get; set; }
            [JsonProperty("version")] public string Version { get; set; }
            [JsonProperty("runId")] public long RunId { get; set; }
            [JsonProperty("runNumber")] public long RunNumber { get; set; }
            [JsonProperty("runAttempt")] public int RunAttempt { get; set; }
            [JsonProperty("commitHash")] public string CommitHash { get; set; }
        }

        private sealed class Asset
        {
            [JsonProperty("url")] public string Url { get; set; }
            [JsonProperty("size")] public long Size { get; set; }
            [JsonProperty("sha256")] public string Sha256 { get; set; }
        }
    }
}
