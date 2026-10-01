using System;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using T7.Rekindle.Desktop.Services;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class GitHubReleaseUpdateTests
    {
        private const string REPOSITORY = "https://github.com/example/project";

        internal static void Run()
        {
            foreach (var address in new[] { LauncherInformation.RepositoryAddress, LauncherInformation.DownloadAddress })
                Assert(Uri.TryCreate(address, UriKind.Absolute, out var uri) && uri.Scheme == Uri.UriSchemeHttps,
                    "public project link metadata is missing");
            RunAsync().GetAwaiter().GetResult();
            foreach (var address in new[] { "", "relative", "http://example.invalid", "file:///C:/sample", "https://user@example.invalid" })
            {
                Assert(!new LauncherUpdateInfo { DownloadAddress = address }.HasDownloadAddress, "invalid download link was enabled");
                try { new DesktopInteraction().OpenAddress(address); }
                catch (ArgumentException) { continue; }
                throw new InvalidOperationException("non-HTTPS link reached the browser launcher");
            }
        }

        private static async Task RunAsync()
        {
            foreach (var version in new[] { new Version(0, 1, 0), new Version(0, 1, 0, 0) })
                Assert(LauncherInformation.FormatVersion(version) == "v0.1.0", "zero revision changed the displayed version");
            var newer = await CheckAsync(HttpStatusCode.OK, Release("v0.10.0").ToString(), "v0.2.0");
            Assert(newer.IsNewVersion && newer.HasPublishedRelease && !newer.IsCurrentVersionAhead,
                "release versions were compared lexically instead of numerically");
            Assert(newer.StatusText == "发现新版本" && newer.TargetVersion == "v0.10.0"
                && newer.Summary == "发布说明\n第二行" && newer.DownloadAddress == REPOSITORY + "/releases/tag/v0.10.0",
                "release metadata was not preserved");
            foreach (var tag in new[] { "v0.1.0", "0.1.0", "V0.1.0", "v0.1.0.0", "v0.1.0+build.12" })
            {
                var equal = await CheckAsync(HttpStatusCode.OK, Release(tag).ToString());
                Assert(!equal.IsNewVersion && !equal.IsCurrentVersionAhead && equal.StatusText == "已是最新版本",
                    "equivalent release versions did not compare equally");
            }
            var currentWithRevision = LauncherInformation.FormatVersion(new Version(0, 1, 0, 1));
            var sameRevision = await CheckAsync(HttpStatusCode.OK, Release("v0.1.0.1").ToString(), currentWithRevision);
            Assert(!sameRevision.IsNewVersion && !sameRevision.IsCurrentVersionAhead
                && sameRevision.CurrentVersion == "v0.1.0.1" && sameRevision.StatusText == "已是最新版本",
                "non-zero assembly revision was truncated and the installed release was offered again");
            var higherRevision = await CheckAsync(HttpStatusCode.OK, Release("v0.1.0.2").ToString(), currentWithRevision);
            Assert(higherRevision.IsNewVersion && !higherRevision.IsCurrentVersionAhead,
                "a higher release revision was not offered as an update");
            var lowerRevision = await CheckAsync(HttpStatusCode.OK, Release("v0.1.0.0").ToString(), currentWithRevision);
            Assert(lowerRevision.IsCurrentVersionAhead && !lowerRevision.IsNewVersion,
                "a newer local revision was offered a downgrade");
            var ahead = await CheckAsync(HttpStatusCode.OK, Release("v0.0.9").ToString());
            Assert(ahead.IsCurrentVersionAhead && !ahead.IsNewVersion && ahead.StatusText.Contains("高于"),
                "a newer local build was offered a downgrade");
            var missing = await CheckAsync(HttpStatusCode.NotFound, "{}");
            Assert(!missing.HasPublishedRelease && !missing.IsNewVersion && missing.TargetVersion == "未发布"
                && missing.StatusText == "暂无正式发布版本" && missing.DownloadAddress == REPOSITORY + "/releases",
                "an absent release was reported as up to date");
            var noNotes = Release("v0.2.0");
            noNotes["body"] = null;
            Assert((await CheckAsync(HttpStatusCode.OK, noNotes.ToString())).Summary.Contains("未填写"), "empty release notes have no fallback");

            foreach (var status in new[] { HttpStatusCode.Forbidden, (HttpStatusCode)429, HttpStatusCode.ServiceUnavailable })
                await ExpectFailure(() => CheckAsync(status, "{}"), status == HttpStatusCode.ServiceUnavailable ? "503" : "受限");
            foreach (var json in new[] { "invalid-json", "[]", "null", "{}" })
                await ExpectFailure(() => CheckAsync(HttpStatusCode.OK, json), "GitHub");
            foreach (var field in new[] { "draft", "prerelease" })
            {
                var unpublished = Release("v0.2.0");
                unpublished[field] = true;
                await ExpectFailure(() => CheckAsync(HttpStatusCode.OK, unpublished.ToString()), "不是正式");
            }
            foreach (var tag in new[] { "release-test", "v0.2.0-rc.1", "v0.2", "v999999999999.0.0", "v0.2.0+" })
                await ExpectFailure(() => CheckAsync(HttpStatusCode.OK, Release(tag).ToString()), "版本号");
            foreach (var address in new[] { "javascript:alert(1)", "http://github.com/example/project/releases/tag/v0.2.0",
                "https://example.invalid/releases/tag/v0.2.0", "https://github.com/example/another/releases/tag/v0.2.0",
                "https://user@github.com/example/project/releases/tag/v0.2.0" })
            {
                var invalid = Release("v0.2.0");
                invalid["html_url"] = address;
                await ExpectFailure(() => CheckAsync(HttpStatusCode.OK, invalid.ToString()), "发布地址");
            }
            await ExpectFailure(() => CheckAsync(HttpStatusCode.OK, "", failure: new HttpRequestException("network fixture")), "连接 GitHub 失败");
            await ExpectFailure(() => CheckAsync(HttpStatusCode.OK, "", failure: new TaskCanceledException()), "超时");
        }

        private static JObject Release(string tag) => new JObject
        {
            ["tag_name"] = tag, ["html_url"] = REPOSITORY + "/releases/tag/" + tag,
            ["body"] = "发布说明\n第二行", ["draft"] = false, ["prerelease"] = false
        };

        private static async Task<LauncherUpdateInfo> CheckAsync(HttpStatusCode status, string json,
            string current = "v0.1.0", Exception failure = null)
        {
            using (var handler = new ResponseHandler(status, json, failure))
            using (var client = new HttpClient(handler))
            {
                var result = await new GitHubReleaseUpdateService(client, REPOSITORY).CheckAsync(current);
                Assert(handler.RequestCount == 1, "an update check made an extra request or downloaded an asset");
                return result;
            }
        }

        private static async Task ExpectFailure(Func<Task<LauncherUpdateInfo>> operation, string message)
        {
            try { await operation(); }
            catch (Exception error)
            {
                Assert(error.Message.Contains(message), "update error lost its explanation: " + error.Message);
                return;
            }
            throw new InvalidOperationException("invalid update response was accepted");
        }

        private sealed class ResponseHandler : HttpMessageHandler
        {
            private readonly HttpStatusCode _status;
            private readonly string _json;
            private readonly Exception _failure;
            internal int RequestCount { get; private set; }

            internal ResponseHandler(HttpStatusCode status, string json, Exception failure)
            {
                _status = status;
                _json = json;
                _failure = failure;
            }

            protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
            {
                RequestCount++;
                Assert(request.Method == HttpMethod.Get && request.RequestUri.AbsoluteUri == "https://api.github.com/repos/example/project/releases/latest",
                    "update check did not query the latest published GitHub release");
                Assert(request.Headers.Authorization == null && request.Headers.UserAgent.ToString().StartsWith("T7-Rekindle/")
                    && request.Headers.Accept.ToString() == "application/vnd.github+json"
                    && string.Join(",", request.Headers.GetValues("X-GitHub-Api-Version")) == "2026-03-10", "GitHub request headers are invalid");
                return _failure == null
                    ? Task.FromResult(new HttpResponseMessage(_status) { Content = new StringContent(_json) })
                    : Task.FromException<HttpResponseMessage>(_failure);
            }
        }
    }
}
