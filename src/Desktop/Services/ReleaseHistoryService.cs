using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using T7.Rekindle.Core;

namespace T7.Rekindle.Desktop.Services
{
    public sealed class LauncherHistoryEntry
    {
        internal LauncherHistoryEntry(LauncherBuild target, LauncherBuild current, DateTimeOffset publishedAt,
            string summary, int settingsSchemaVersion, LauncherUpdateAsset installer)
        {
            Build = target;
            PublishedAt = publishedAt;
            Summary = summary;
            SettingsSchemaVersion = settingsSchemaVersion;
            Installer = installer;
            var comparison = target.Channel != current.Channel || (current.Channel == UpdateChannel.Preview && !current.HasPreviewIdentity)
                ? -1 : target.Channel == UpdateChannel.Stable
                ? ReleaseMetadata.ParseVersion(target.Version).CompareTo(ReleaseMetadata.ParseVersion(current.Version))
                : target.RunNumber == current.RunNumber ? target.RunAttempt.CompareTo(current.RunAttempt)
                : target.RunNumber.CompareTo(current.RunNumber);
            CanRollback = comparison < 0;
            AvailabilityText = comparison == 0 ? "当前版本" : comparison > 0 ? "高于当前版本"
                : target.Channel != current.Channel ? "可切换至此版本" : "可回退";
        }

        internal LauncherBuild Build { get; }
        public UpdateChannel Channel => Build.Channel;
        public string Identity => Build.Identity;
        public string DisplayVersion => Build.DisplayVersion;
        public string ChannelText => Channel == UpdateChannel.Stable ? "正式版" : "预览版";
        public DateTimeOffset PublishedAt { get; }
        public string PublishedText => PublishedAt.ToLocalTime().ToString("yyyy-MM-dd HH:mm", CultureInfo.InvariantCulture);
        public string Summary { get; }
        public int SettingsSchemaVersion { get; }
        public bool ResetsSettings => SettingsSchemaVersion != SettingsSchema.CurrentVersion;
        public string SettingsNotice => ResetsSettings
            ? "设置格式不兼容：回退前备份，安装成功后重置启动器设置，仅保留更新渠道。游戏配置与存档不变。"
            : "设置格式兼容：回退前备份并保留启动器设置。游戏配置与存档不变。";
        public LauncherUpdateAsset Installer { get; }
        public bool CanRollback { get; }
        public string AvailabilityText { get; }

        internal LauncherUpdateInfo ToInstallInfo() => new LauncherUpdateInfo
        {
            TargetBuild = Build, TargetVersion = Build.Version, Channel = Channel,
            Installer = Installer, Summary = Summary, UpdateSource = "R2"
        };
    }

    internal sealed class ReleaseHistoryService
    {
        private readonly HttpClient _client;
        private readonly Uri _repository;
        private readonly string _mirror;
        private readonly LauncherBuild _current;

        internal ReleaseHistoryService(HttpClient client, string repository, string mirror, LauncherBuild current)
        {
            _client = client;
            _repository = ReleaseMetadata.Repository(repository);
            _mirror = mirror;
            _current = current;
        }

        internal async Task<IReadOnlyList<LauncherHistoryEntry>> GetHistoryAsync(UpdateChannel channel, CancellationToken cancellation)
        {
            if (channel != UpdateChannel.Stable && channel != UpdateChannel.Preview) throw new ArgumentOutOfRangeException(nameof(channel));
            if (string.IsNullOrWhiteSpace(_mirror)) throw new IOException("当前构建未配置 R2 历史版本地址。");
            var origin = ReleaseMetadata.MirrorOrigin(_mirror);
            var name = channel == UpdateChannel.Stable ? "stable" : "preview";
            using (var request = new HttpRequestMessage(HttpMethod.Get, new Uri(origin, "updates/" + name + "-history.json")))
            {
                request.Headers.UserAgent.ParseAdd("T7-Rekindle/" + ReleaseMetadata.ParseVersion(_current.Version).ToString(3));
                request.Headers.CacheControl = new CacheControlHeaderValue { NoCache = true };
                try
                {
                    using (var response = await _client.SendAsync(request, cancellation).ConfigureAwait(false))
                    {
                        if (response.StatusCode == HttpStatusCode.NotFound) return Array.Empty<LauncherHistoryEntry>();
                        if (!response.IsSuccessStatusCode) throw new IOException("历史版本服务返回 HTTP " + (int)response.StatusCode + "。");
                        var json = JsonConvert.DeserializeObject<JObject>(await response.Content.ReadAsStringAsync().ConfigureAwait(false),
                            new JsonSerializerSettings { DateParseHandling = DateParseHandling.None });
                        cancellation.ThrowIfCancellationRequested();
                        if (Integer(json, "schemaVersion") != 1 || Text(json, "channel") != name || !(json["entries"] is JArray entries))
                            throw new InvalidDataException("历史版本清单格式不正确。");
                        var result = new List<LauncherHistoryEntry>();
                        var identities = new HashSet<string>(StringComparer.Ordinal);
                        foreach (var token in entries)
                        {
                            if (!(token is JObject entry)) throw new InvalidDataException("历史版本记录格式不正确。");
                            var version = Text(entry, "version");
                            var build = new LauncherBuild(channel, version);
                            string path;
                            if (channel == UpdateChannel.Preview)
                            {
                                var value = entry["build"] as JObject;
                                if (Text(value, "channel") != name || ReleaseMetadata.ParseVersion(Text(value, "version")) != ReleaseMetadata.ParseVersion(version))
                                    throw new InvalidDataException("历史构建身份不一致。");
                                build = new LauncherBuild(channel, version, Integer(value, "runId"), Integer(value, "runNumber"),
                                    checked((int)Integer(value, "runAttempt")), Text(value, "commitHash"));
                                if (!build.HasPreviewIdentity) throw new InvalidDataException("历史构建缺少 CI 身份。");
                                path = "previews/" + build.RunId.ToString(CultureInfo.InvariantCulture) + "/"
                                    + build.RunAttempt.ToString(CultureInfo.InvariantCulture) + "/T7-Rekindle-Setup.exe";
                            }
                            else path = "releases/" + Uri.EscapeDataString(version) + "/" + Uri.EscapeDataString(ReleaseMetadata.InstallerName(version));
                            var expected = new Uri(origin, path).AbsoluteUri;
                            var asset = entry["installer"] as JObject;
                            if (!ReleaseMetadata.IsSameObject(Text(asset, "url"), expected) || !identities.Add(build.Identity))
                                throw new InvalidDataException("历史安装包地址或构建身份无效。");
                            var schema = checked((int)Integer(entry, "settingsSchemaVersion"));
                            var date = Text(entry, "publishedAt");
                            if (schema <= 0 || !DateTimeOffset.TryParse(date, CultureInfo.InvariantCulture, DateTimeStyles.None, out var published)
                                || !(date.EndsWith("Z", StringComparison.OrdinalIgnoreCase) || date.LastIndexOf('+') > 9 || date.LastIndexOf('-') > 9))
                                throw new InvalidDataException("历史版本设置格式或发布时间无效。");
                            result.Add(new LauncherHistoryEntry(build, _current, published, Text(entry, "summary"), schema,
                                new LauncherUpdateAsset(expected, channel == UpdateChannel.Stable ? ReleaseMetadata.GitHubAsset(_repository, version) : null,
                                    Integer(asset, "size"), Text(asset, "sha256"), "R2")));
                        }
                        return (channel == UpdateChannel.Stable
                            ? result.OrderByDescending(entry => ReleaseMetadata.ParseVersion(entry.Build.Version)).ThenByDescending(entry => entry.Build.Version, StringComparer.Ordinal)
                            : result.OrderByDescending(entry => entry.Build.RunNumber).ThenByDescending(entry => entry.Build.RunAttempt)).ToArray();
                    }
                }
                catch (OperationCanceledException error) when (!cancellation.IsCancellationRequested) { throw new TimeoutException("读取历史版本超时，请重试。", error); }
                catch (HttpRequestException error) { throw new IOException("连接历史版本服务失败。", error); }
                catch (Exception error) when (error is JsonException || error is OverflowException || error is FormatException)
                { throw new InvalidDataException("历史版本清单格式不正确。", error); }
            }
        }

        private static string Text(JObject value, string name)
        {
            if (value?[name]?.Type != JTokenType.String) throw new InvalidDataException("历史清单缺少字符串字段：" + name);
            return (string)value[name];
        }

        private static long Integer(JObject value, string name)
        {
            if (value?[name]?.Type != JTokenType.Integer) throw new InvalidDataException("历史清单缺少整数字段：" + name);
            return (long)value[name];
        }
    }
}
