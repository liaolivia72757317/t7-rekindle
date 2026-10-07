using System;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using T7.Rekindle.Desktop.Services;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class UpdateChannelTests
    {
        internal static void Run() => RunAsync().GetAwaiter().GetResult();

        internal static LauncherBuild Preview(long number = 12, int attempt = 1) =>
            new LauncherBuild(UpdateChannel.Preview, "v0.1.0", 100 + number, number, attempt, new string('a', 40));

        internal static JObject Manifest() => new JObject
        {
            ["schemaVersion"] = 1, ["channel"] = "preview", ["version"] = "v0.1.0", ["summary"] = "CI 构建说明",
            ["build"] = new JObject { ["channel"] = "preview", ["version"] = "0.1.0", ["runId"] = 112,
                ["runNumber"] = 12, ["runAttempt"] = 1, ["commitHash"] = new string('a', 40) },
            ["installer"] = new JObject { ["url"] = UpdateFixtures.Mirror + "/previews/112/1/T7-Rekindle-Setup.exe",
                ["size"] = UpdateFixtures.Payload.Length, ["sha256"] = UpdateFixtures.Digest }
        };

        private static async Task RunAsync()
        {
            var configured = Environment.GetEnvironmentVariable("T7_BUILD_CHANNEL");
            var embedded = LauncherInformation.CurrentBuild;
            if (!string.IsNullOrEmpty(configured))
            {
                Assert(embedded.Channel == (configured == "stable" ? UpdateChannel.Stable : UpdateChannel.Preview)
                    && embedded.RunId.ToString() == Environment.GetEnvironmentVariable("T7_PREVIEW_RUN_ID")
                    && embedded.RunNumber.ToString() == Environment.GetEnvironmentVariable("T7_PREVIEW_RUN_NUMBER")
                    && embedded.RunAttempt.ToString() == Environment.GetEnvironmentVariable("T7_PREVIEW_RUN_ATTEMPT"),
                    "assembly build identity differs from the packaging environment");
                Assert(ReleaseMetadata.ParseVersion(embedded.Version)
                    == ReleaseMetadata.ParseVersion(Environment.GetEnvironmentVariable("T7_BUILD_VERSION")),
                    "assembly version differs from package identity");
            }
            foreach (var current in new[] { "v0.1.0", "v1.2.3", "v9.0.0" })
                foreach (var mirror in new[] { "", UpdateFixtures.Mirror })
                {
                    using (var handler = new UpdateResponseHandler((request, token) => Task.FromResult(
                        mirror.Length == 0 ? UpdateFixtures.GitHubResponse(request) : UpdateFixtures.Json(UpdateFixtures.Manifest()))))
                    using (var client = new HttpClient(handler))
                    {
                        var info = await new ReleaseUpdateService(client, UpdateFixtures.Repository, mirror)
                            .CheckAsync(new LauncherBuild(UpdateChannel.Preview, current), UpdateChannel.Stable);
                        Assert(info.IsNewVersion && !info.IsCurrentVersionAhead && handler.RequestCount == 1
                            && info.ReleaseNotes.Count == 1, "preview-to-stable comparison or target-only notes failed");
                    }
                }
            foreach (var current in new[] { Preview(11), Preview(), Preview(12, 2), Preview(13),
                new LauncherBuild(UpdateChannel.Stable, "v9.0.0"), new LauncherBuild(UpdateChannel.Preview, "v9.0.0") })
            {
                using (var handler = new UpdateResponseHandler((request, token) =>
                {
                    Assert(request.RequestUri.AbsoluteUri == UpdateFixtures.Mirror + "/updates/preview.json"
                        && request.Headers.Authorization == null, "preview used a different channel or authentication");
                    return Task.FromResult(UpdateFixtures.Json(Manifest()));
                }))
                using (var client = new HttpClient(handler))
                {
                    var info = await new ReleaseUpdateService(client, UpdateFixtures.Repository, UpdateFixtures.Mirror)
                        .CheckAsync(current, UpdateChannel.Preview);
                    var expected = current.Channel == UpdateChannel.Stable || current.RunNumber == 0 || current.RunNumber < 12;
                    Assert(info.IsNewVersion == expected && info.Channel == UpdateChannel.Preview
                        && info.Installer.FallbackAddress == null && info.TargetDisplayVersion.Contains("12.1")
                        && info.UpdateIdentity == "preview:112:1", "preview identity/comparison failed");
                }
            }
            foreach (var status in new[] { HttpStatusCode.NotFound, HttpStatusCode.InternalServerError })
            {
                using (var handler = new UpdateResponseHandler((request, token) => Task.FromResult(new HttpResponseMessage(status))))
                using (var client = new HttpClient(handler))
                {
                    try
                    {
                        var info = await new ReleaseUpdateService(client, UpdateFixtures.Repository, UpdateFixtures.Mirror)
                            .CheckAsync(Preview(), UpdateChannel.Preview);
                        Assert(status == HttpStatusCode.NotFound && !info.HasPublishedRelease
                            && info.StatusText == "暂无预览构建", "missing preview state was incorrect");
                    }
                    catch (IOException) { Assert(status != HttpStatusCode.NotFound, "missing preview threw"); }
                    Assert(handler.RequestCount == 1, "preview fell back to GitHub");
                }
            }
            foreach (var change in new Action<JObject>[]
            {
                json => json["channel"] = "stable", json => json["schemaVersion"] = 2,
                json => json["build"]["runId"] = 0, json => json["build"]["runNumber"] = -1,
                json => json["build"]["runAttempt"] = 0, json => json["build"]["commitHash"] = "invalid",
                json => json["build"]["version"] = "9.0.0", json => json.Remove("build"),
                json => json["installer"]["url"] = UpdateFixtures.Mirror + "/previews/112/2/T7-Rekindle-Setup.exe",
                json => json["installer"]["url"] = "https://foreign.example/Setup.exe",
                json => json["installer"]["size"] = 0, json => json["installer"]["sha256"] = "invalid"
            })
            {
                var manifest = Manifest();
                change(manifest);
                using (var handler = new UpdateResponseHandler((request, token) => Task.FromResult(UpdateFixtures.Json(manifest))))
                using (var client = new HttpClient(handler))
                {
                    var failed = false;
                    try { await new ReleaseUpdateService(client, UpdateFixtures.Repository, UpdateFixtures.Mirror).CheckAsync(Preview(), UpdateChannel.Preview); }
                    catch (InvalidDataException) { failed = true; }
                    Assert(failed && handler.RequestCount == 1, "invalid preview was accepted or changed source");
                }
            }
        }
    }
}
