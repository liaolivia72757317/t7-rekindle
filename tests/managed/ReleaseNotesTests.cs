using System;
using System.Collections.Concurrent;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using T7.Rekindle.Desktop.Services;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class ReleaseNotesTests
    {
        internal static void Run() => RunAsync().GetAwaiter().GetResult();

        private static async Task RunAsync()
        {
            await MirrorRangeAsync();
            await MirrorFallbackAsync();
            await GitHubPagesAsync(false);
            await GitHubPagesAsync(true);
            await ConcurrencyAsync();
            await RevisionRangeAsync();
        }

        private static async Task MirrorRangeAsync()
        {
            var calls = new ConcurrentBag<string>();
            var manifest = Manifest("v0.10.0", "v0.2.0", "v0.4.0", "v0.3.0", "v0.4.0");
            using (var handler = new UpdateResponseHandler((request, token) =>
            {
                calls.Add(request.RequestUri.AbsolutePath);
                Assert(request.RequestUri.Host != "api.github.com", "healthy mirror queried GitHub");
                return Task.FromResult(request.RequestUri.AbsolutePath.EndsWith("stable.json")
                    ? UpdateFixtures.Json(manifest) : Text("## 改动\n- " + request.RequestUri.AbsolutePath));
            }))
            using (var client = new HttpClient(handler))
            {
                var service = new ReleaseUpdateService(client, UpdateFixtures.Repository, UpdateFixtures.Mirror);
                var result = await service.CheckAsync("v0.2.5");
                Assert(result.ReleaseNotes.Select(note => note.Version).SequenceEqual(new[] { "v0.10.0", "v0.4.0", "v0.3.0" }),
                    "mirror history was not filtered, deduplicated and numerically sorted");
                Assert(result.ReleaseNotes.All(note => note.Error.Length == 0) && result.ReleaseNotesNotice.Length == 0,
                    "healthy notes reported a failure");
                Assert(result.Summary == "目标说明" && calls.Count == 4 && result.Installer != null,
                    "aggregation changed the latest summary or downloaded an installer");
                foreach (var current in new[] { "v0.10.0", "v1.0.0" })
                {
                    calls = new ConcurrentBag<string>();
                    var equal = await service.CheckAsync(current);
                    Assert(calls.Count == 1 && equal.ReleaseNotes.Count == 1
                        && equal.ReleaseNotes[0].Version == "v0.10.0", "up-to-date launcher fetched historical notes");
                }
            }
        }

        private static async Task MirrorFallbackAsync()
        {
            foreach (var index in new JToken[] { null, new JValue("invalid"), new JArray("../bad"),
                new JArray("v1.2.3", "v1.1.0", "v1.0.5") })
            {
                var manifest = Manifest("v1.2.3");
                if (index == null) manifest.Remove("versions"); else manifest["versions"] = index;
                var githubCalls = 0;
                using (var handler = new UpdateResponseHandler((request, token) =>
                {
                    if (request.RequestUri.Host == "api.github.com")
                    {
                        githubCalls++;
                        Assert(!request.RequestUri.AbsolutePath.EndsWith("latest"), "notes fallback replaced the installable target");
                        return Task.FromResult(UpdateFixtures.Json(new JArray(Release("v1.1.0", "补齐日志"))));
                    }
                    return Task.FromResult(request.RequestUri.AbsolutePath.EndsWith("stable.json")
                        ? UpdateFixtures.Json(manifest) : new HttpResponseMessage(HttpStatusCode.NotFound));
                }))
                using (var client = new HttpClient(handler))
                {
                    var result = await new ReleaseUpdateService(client, UpdateFixtures.Repository, UpdateFixtures.Mirror).CheckAsync("v1.0.0");
                    Assert(result.UpdateSource == "R2" && result.Installer != null && result.IsNewVersion && githubCalls == 1,
                        "notes failures blocked installation or repeated GitHub history requests");
                    Assert(result.ReleaseNotes.Any(note => note.Version == "v1.1.0" && note.Summary == "补齐日志"),
                        "GitHub did not supplement release notes");
                    Assert(result.ReleaseNotes.First().Summary == "目标说明", "target summary was not retained as fallback");
                    if (index is JArray array && array.Count == 3)
                        Assert(result.ReleaseNotes.Last().Error.Length != 0 && result.ReleaseNotesNotice.Length != 0,
                            "missing indexed version was silently omitted");
                }
            }
            using (var handler = new UpdateResponseHandler((request, token) => request.RequestUri.Host == "api.github.com"
                ? Task.FromException<HttpResponseMessage>(new TaskCanceledException())
                : Task.FromResult(UpdateFixtures.Json(Manifest("v1.2.3", "../invalid")))))
            using (var client = new HttpClient(handler))
            {
                var result = await new ReleaseUpdateService(client, UpdateFixtures.Repository, UpdateFixtures.Mirror).CheckAsync("v1.0.0");
                Assert(result.Installer != null && result.ReleaseNotesNotice.Length != 0 && result.ReleaseNotes.Count == 1,
                    "unavailable historical index invalidated a valid update");
            }
        }

        private static async Task GitHubPagesAsync(bool failSecondPage)
        {
            var calls = 0;
            var target = UpdateFixtures.Release();
            var firstPage = new JArray(Release("v1.1.0", "第一页"));
            while (firstPage.Count < 100) firstPage.Add(Release("v0.1.0", "已安装"));
            using (var handler = new UpdateResponseHandler((request, token) =>
            {
                calls++;
                if (request.RequestUri.AbsolutePath.EndsWith("latest")) return Task.FromResult(UpdateFixtures.Json(target));
                if (request.RequestUri.Query.EndsWith("&page=1")) return Task.FromResult(UpdateFixtures.Json(firstPage));
                Assert(request.RequestUri.Query == "?per_page=100&page=2", "history pagination did not continue");
                if (failSecondPage) return Task.FromResult(new HttpResponseMessage((HttpStatusCode)429));
                var draft = Release("v1.2.0", "草稿"); draft["draft"] = true;
                var prerelease = Release("v1.2.1", "预发布"); prerelease["prerelease"] = true;
                return Task.FromResult(UpdateFixtures.Json(new JArray(Release("v1.0.5", "第二页"),
                    Release("v9.0.0", "目标之后"), Release("invalid", "无效"), draft, prerelease)));
            }))
            using (var client = new HttpClient(handler))
            {
                var result = await new ReleaseUpdateService(client, UpdateFixtures.Repository, "").CheckAsync("v1.0.0");
                var expected = failSecondPage ? new[] { "v1.2.3", "v1.1.0" } : new[] { "v1.2.3", "v1.1.0", "v1.0.5" };
                Assert(calls == 3 && result.ReleaseNotes.Select(note => note.Version).SequenceEqual(expected),
                    "GitHub pagination lost notes or trusted API ordering");
                Assert((result.ReleaseNotesNotice.Length > 0) == failSecondPage && result.Installer != null,
                    "partial history failure was not isolated from installation");
            }
        }

        private static async Task ConcurrencyAsync()
        {
            var tags = Enumerable.Range(1, 9).Select(value => "v1.0." + value).ToArray();
            var manifest = Manifest("v1.0.9", tags);
            var active = 0;
            var maximum = 0;
            var gate = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            using (var handler = new UpdateResponseHandler(async (request, token) =>
            {
                if (request.RequestUri.AbsolutePath.EndsWith("stable.json")) return UpdateFixtures.Json(manifest);
                var count = Interlocked.Increment(ref active);
                maximum = Math.Max(maximum, count);
                if (count == 4) gate.TrySetResult(true);
                await gate.Task;
                await Task.Delay(10);
                Interlocked.Decrement(ref active);
                return Text("");
            }))
            using (var client = new HttpClient(handler))
            {
                var operation = new ReleaseUpdateService(client, UpdateFixtures.Repository, UpdateFixtures.Mirror).CheckAsync("v1.0.0");
                Assert(await Task.WhenAny(operation, Task.Delay(5000)) == operation, "notes concurrency deadlocked");
                var result = await operation;
                Assert(maximum == 4 && result.ReleaseNotes.Count == 9 && result.ReleaseNotes.All(note => note.Error.Length == 0),
                    "notes concurrency was not bounded or empty notes were treated as failures");
            }
        }

        private static async Task RevisionRangeAsync()
        {
            using (var handler = new UpdateResponseHandler((request, token) => Task.FromResult(
                request.RequestUri.AbsolutePath.EndsWith("stable.json")
                    ? UpdateFixtures.Json(Manifest("V1.0.0.3+ci.2", "v1.0.0.1+ci.3", "v1.0.0.2")) : Text("日志"))))
            using (var client = new HttpClient(handler))
            {
                var result = await new ReleaseUpdateService(client, UpdateFixtures.Repository, UpdateFixtures.Mirror).CheckAsync("v1.0.0.1");
                Assert(result.ReleaseNotes.Select(note => note.Version).SequenceEqual(new[] { "V1.0.0.3+ci.2", "v1.0.0.2" }),
                    "notes filtering lost revision or build metadata semantics");
            }
        }

        private static JObject Manifest(string target, params string[] versions)
        {
            var manifest = UpdateFixtures.Manifest();
            manifest["version"] = target;
            manifest["summary"] = "目标说明";
            manifest["installer"]["url"] = UpdateFixtures.Mirror + "/releases/" + Uri.EscapeDataString(target)
                + "/" + Uri.EscapeDataString("T7-Rekindle-" + target + "-Setup.exe");
            manifest["versions"] = new JArray(new[] { target }.Concat(versions));
            return manifest;
        }

        private static JObject Release(string version, string body) => new JObject
        {
            ["tag_name"] = version, ["body"] = body, ["draft"] = false, ["prerelease"] = false
        };

        private static HttpResponseMessage Text(string text) => new HttpResponseMessage(HttpStatusCode.OK)
            { Content = new StringContent(text) };
    }
}
