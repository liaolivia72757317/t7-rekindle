using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace T7.Rekindle.Desktop.Services
{
    public sealed class LauncherReleaseNote
    {
        public LauncherReleaseNote(string version, string summary, string error = "")
        {
            Version = version;
            Summary = summary ?? string.Empty;
            Error = error ?? string.Empty;
        }

        public string Version { get; }
        public string Summary { get; }
        public string Error { get; }
    }

    internal sealed class ReleaseNotesResult
    {
        internal ReleaseNotesResult(IEnumerable<LauncherReleaseNote> entries, string notice = "")
        {
            Entries = Array.AsReadOnly(entries.OrderByDescending(note => ReleaseMetadata.ParseVersion(note.Version))
                .ThenByDescending(note => note.Version, StringComparer.Ordinal).ToArray());
            Notice = notice;
        }

        internal IReadOnlyList<LauncherReleaseNote> Entries { get; }
        internal string Notice { get; }
    }

    internal sealed class ReleaseNotesService
    {
        private readonly HttpClient _client;
        private readonly Uri _repository;

        internal ReleaseNotesService(HttpClient client, Uri repository)
        {
            _client = client;
            _repository = repository;
        }

        internal async Task<ReleaseNotesResult> ReadMirrorAsync(LauncherUpdateInfo info, Uri origin, JToken versions)
        {
            if (!info.IsNewVersion || info.CurrentChannel != info.Channel) return TargetOnly(info);
            string[] tags;
            try { tags = SelectVersions(info, versions); }
            catch (InvalidDataException)
            {
                var fallback = await ReadGitHubAsync(info).ConfigureAwait(false);
                return new ReleaseNotesResult(fallback.Entries, fallback.Notice.Length != 0 ? fallback.Notice
                    : "R2 历史版本列表缺失或无效，已使用 GitHub 日志。");
            }

            LauncherReleaseNote[] notes;
            using (var limit = new SemaphoreSlim(4))
            {
                notes = await Task.WhenAll(tags.Select(async tag =>
                {
                    await limit.WaitAsync().ConfigureAwait(false);
                    try { return await ReadMirrorNoteAsync(info, origin, tag).ConfigureAwait(false); }
                    finally { limit.Release(); }
                })).ConfigureAwait(false);
            }
            if (notes.All(note => note.Error.Length == 0)) return new ReleaseNotesResult(notes);

            var history = await ReadHistoryAsync(info).ConfigureAwait(false);
            var completed = notes.Select(note => note.Error.Length == 0 ? note
                : history.Notes.TryGetValue(note.Version, out var replacement) ? replacement
                : note.Version == info.TargetVersion ? TargetNote(info) : note).ToArray();
            var notice = completed.Any(note => note.Error.Length != 0)
                ? "部分版本日志加载失败，已显示获取到的内容。后续检查将重试。"
                : "部分 R2 日志未取得，已使用 GitHub 日志或目标版本说明补齐。";
            return new ReleaseNotesResult(completed, notice);
        }

        internal async Task<ReleaseNotesResult> ReadGitHubAsync(LauncherUpdateInfo info)
        {
            if (!info.IsNewVersion || info.CurrentChannel != info.Channel) return TargetOnly(info);
            var history = await ReadHistoryAsync(info).ConfigureAwait(false);
            if (!history.Notes.ContainsKey(info.TargetVersion)) history.Notes.Add(info.TargetVersion, TargetNote(info));
            return new ReleaseNotesResult(history.Notes.Values, history.Notice);
        }

        private static string[] SelectVersions(LauncherUpdateInfo info, JToken versions)
        {
            if (!(versions is JArray array) || array.Count == 0)
                throw new InvalidDataException("历史版本列表缺失。");
            var tags = new List<string>();
            var current = ReleaseMetadata.ParseVersion(info.CurrentVersion);
            var target = ReleaseMetadata.ParseVersion(info.TargetVersion);
            foreach (var item in array)
            {
                if (item.Type != JTokenType.String) throw new InvalidDataException("历史版本列表格式不正确。");
                var tag = item.Value<string>();
                var version = ReleaseMetadata.ParseVersion(tag);
                if (version > current && version <= target) tags.Add(tag);
            }
            if (!tags.Contains(info.TargetVersion)) throw new InvalidDataException("历史版本列表缺少目标版本。");
            return tags.Distinct(StringComparer.Ordinal).ToArray();
        }

        private async Task<LauncherReleaseNote> ReadMirrorNoteAsync(LauncherUpdateInfo info, Uri origin, string tag)
        {
            try
            {
                var address = new Uri(origin, "releases/" + Uri.EscapeDataString(tag) + "/changelog.md");
                using (var request = CreateRequest(info, address, false))
                using (var response = await _client.SendAsync(request).ConfigureAwait(false))
                {
                    if (!response.IsSuccessStatusCode) throw new IOException("R2 日志服务返回 HTTP " + (int)response.StatusCode + "。");
                    return new LauncherReleaseNote(tag, await response.Content.ReadAsStringAsync().ConfigureAwait(false));
                }
            }
            catch (Exception error) when (IsReadFailure(error))
            {
                return new LauncherReleaseNote(tag, "", error is TaskCanceledException ? "读取日志超时。" : error.Message);
            }
        }

        private async Task<HistoryResult> ReadHistoryAsync(LauncherUpdateInfo info)
        {
            var notes = new Dictionary<string, LauncherReleaseNote>(StringComparer.Ordinal);
            var current = ReleaseMetadata.ParseVersion(info.CurrentVersion);
            var target = ReleaseMetadata.ParseVersion(info.TargetVersion);
            try
            {
                for (var page = 1; ; page++)
                {
                    var address = new Uri("https://api.github.com/repos" + _repository.AbsolutePath.TrimEnd('/')
                        + "/releases?per_page=100&page=" + page);
                    using (var request = CreateRequest(info, address, true))
                    using (var response = await _client.SendAsync(request).ConfigureAwait(false))
                    {
                        if (!response.IsSuccessStatusCode)
                            throw new IOException("GitHub 日志服务返回 HTTP " + (int)response.StatusCode + "。");
                        var releases = JsonConvert.DeserializeObject<HistoryRelease[]>(
                            await response.Content.ReadAsStringAsync().ConfigureAwait(false));
                        if (releases == null) throw new InvalidDataException("GitHub 历史版本列表为空。");
                        foreach (var release in releases)
                        {
                            if (release == null || !release.Draft.HasValue || !release.Prerelease.HasValue)
                                throw new InvalidDataException("GitHub 历史版本信息不完整。");
                            if (release.Draft.Value || release.Prerelease.Value) continue;
                            Version version;
                            try { version = ReleaseMetadata.ParseVersion(release.Tag); }
                            catch (InvalidDataException) { continue; }
                            if (version > current && version <= target && !notes.ContainsKey(release.Tag))
                                notes.Add(release.Tag, new LauncherReleaseNote(release.Tag, release.Body));
                        }
                        if (releases.Length < 100) break;
                    }
                }
                return new HistoryResult(notes, "");
            }
            catch (Exception error) when (IsReadFailure(error))
            {
                return new HistoryResult(notes, "历史版本列表未完整加载，已显示获取到的内容："
                    + (error is TaskCanceledException ? "读取超时。" : error.Message));
            }
        }

        private static HttpRequestMessage CreateRequest(LauncherUpdateInfo info, Uri address, bool github)
        {
            var request = new HttpRequestMessage(HttpMethod.Get, address);
            request.Headers.UserAgent.ParseAdd("T7-Rekindle/" + ReleaseMetadata.ParseVersion(info.CurrentVersion).ToString(3));
            request.Headers.Accept.ParseAdd(github ? "application/vnd.github+json" : "text/markdown");
            request.Headers.CacheControl = new CacheControlHeaderValue { NoCache = true };
            if (github) request.Headers.Add("X-GitHub-Api-Version", "2026-03-10");
            return request;
        }

        private static bool IsReadFailure(Exception error) => error is IOException || error is InvalidDataException
            || error is HttpRequestException || error is TaskCanceledException || error is JsonException;
        private static LauncherReleaseNote TargetNote(LauncherUpdateInfo info) => new LauncherReleaseNote(info.TargetVersion, info.Summary);
        private static ReleaseNotesResult TargetOnly(LauncherUpdateInfo info) => new ReleaseNotesResult(new[] { TargetNote(info) });

        private sealed class HistoryResult
        {
            internal HistoryResult(Dictionary<string, LauncherReleaseNote> notes, string notice) { Notes = notes; Notice = notice; }
            internal Dictionary<string, LauncherReleaseNote> Notes { get; }
            internal string Notice { get; }
        }

        private sealed class HistoryRelease
        {
            [JsonProperty("tag_name")] public string Tag { get; set; }
            [JsonProperty("body")] public string Body { get; set; }
            [JsonProperty("draft")] public bool? Draft { get; set; }
            [JsonProperty("prerelease")] public bool? Prerelease { get; set; }
        }
    }
}
