using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using T7.Rekindle.Desktop.Services;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class UpdateFeedTests
    {
        internal static void Run() => RunAsync().GetAwaiter().GetResult();

        private static async Task RunAsync()
        {
            var requests = new List<string>();
            using (var handler = new UpdateResponseHandler((request, token) =>
            {
                requests.Add(request.RequestUri.AbsoluteUri);
                Assert(request.Headers.Authorization == null, "public mirror request contained credentials");
                return Task.FromResult(UpdateFixtures.Json(UpdateFixtures.Manifest()));
            }))
            using (var client = new HttpClient(handler))
            {
                var service = new ReleaseUpdateService(client, UpdateFixtures.Repository, UpdateFixtures.Mirror);
                var info = await service.CheckAsync("v1.0.0");
                Assert(requests.Count == 1 && requests[0] == UpdateFixtures.Mirror + "/updates/stable.json",
                    "successful mirror check contacted GitHub or downloaded an asset");
                Assert(info.IsNewVersion && info.Installer != null && info.UpdateSource == "R2"
                    && info.Installer.Size == UpdateFixtures.Payload.Length
                    && info.Installer.FallbackAddress == UpdateFixtures.GitHubAsset,
                    "mirror installer metadata was lost");
                Assert(!(await service.CheckAsync("v1.2.3.0")).IsNewVersion, "equal mirror version was offered");
                Assert((await service.CheckAsync("v2.0.0")).IsCurrentVersionAhead, "mirror offered a downgrade");
            }

            var invalid = new List<JObject>();
            foreach (var change in new Action<JObject>[]
            {
                json => json["schemaVersion"] = 2,
                json => json["version"] = "v1.2.3-rc.1",
                json => json["installer"]["url"] = "https://foreign.example/Setup.exe",
                json => json["installer"]["url"] = UpdateFixtures.Mirror + "/other/Setup.exe",
                json => json["installer"]["sha256"] = "bad",
                json => json["installer"]["size"] = 0,
                json => json.Remove("installer"),
            })
            {
                var manifest = UpdateFixtures.Manifest();
                change(manifest);
                invalid.Add(manifest);
            }
            invalid.Add(null);
            foreach (var manifest in invalid)
            {
                requests.Clear();
                using (var handler = new UpdateResponseHandler((request, token) =>
                {
                    requests.Add(request.RequestUri.Host);
                    if (request.RequestUri.Host == "api.github.com")
                        return Task.FromResult(UpdateFixtures.Json(UpdateFixtures.Release()));
                    return Task.FromResult(UpdateFixtures.Json(manifest));
                }))
                using (var client = new HttpClient(handler))
                {
                    var info = await new ReleaseUpdateService(client, UpdateFixtures.Repository, UpdateFixtures.Mirror)
                        .CheckAsync("v1.0.0");
                    Assert(requests.Count == 2 && info.UpdateSource == "GitHub" && info.SourceNotice.Contains("R2")
                        && info.Installer != null && info.Installer.FallbackAddress == null,
                        "invalid mirror feed did not fall back with a visible explanation");
                }
            }
            foreach (var failure in new Exception[] { new HttpRequestException("offline"), new TaskCanceledException() })
            {
                using (var handler = new UpdateResponseHandler((request, token) =>
                    request.RequestUri.Host == "api.github.com"
                        ? Task.FromResult(UpdateFixtures.Json(UpdateFixtures.Release()))
                        : Task.FromException<HttpResponseMessage>(failure)))
                using (var client = new HttpClient(handler))
                    Assert((await new ReleaseUpdateService(client, UpdateFixtures.Repository, UpdateFixtures.Mirror)
                        .CheckAsync("v1.0.0")).Installer != null, "mirror network failure did not fall back");
            }
            foreach (var digest in new[] { null, "", "sha256:bad", "md5:unused" })
            {
                var oldRelease = UpdateFixtures.Release();
                oldRelease["assets"][0]["digest"] = digest;
                using (var handler = new UpdateResponseHandler((request, token) =>
                    Task.FromResult(UpdateFixtures.Json(oldRelease))))
                using (var client = new HttpClient(handler))
                {
                    var info = await new ReleaseUpdateService(client, UpdateFixtures.Repository, "").CheckAsync("v1.0.0");
                    Assert(info.Installer == null && info.HasDownloadAddress, "legacy release enabled unverified installation");
                }
            }
        }
    }

    internal static class UpdateFixtures
    {
        internal const string Repository = "https://github.com/example/project";
        internal const string Mirror = "https://updates.example.com";
        internal const string AssetName = "T7-Rekindle-Setup.exe";
        internal const string GitHubAsset = Repository + "/releases/download/v1.2.3/" + AssetName;
        internal static readonly byte[] Payload = Encoding.UTF8.GetBytes("installer payload fixture");
        internal static string Digest
        {
            get
            {
                using (var hash = SHA256.Create())
                    return BitConverter.ToString(hash.ComputeHash(Payload)).Replace("-", "").ToLowerInvariant();
            }
        }
        internal static LauncherUpdateAsset Asset(string digest = null, long? size = null) =>
            new LauncherUpdateAsset(Mirror + "/releases/v1.2.3/" + AssetName,
                GitHubAsset, size ?? Payload.Length, digest ?? Digest, "R2");

        internal static JObject Manifest() => new JObject
        {
            ["schemaVersion"] = 1, ["version"] = "v1.2.3", ["summary"] = "更新说明",
            ["installer"] = new JObject
            {
                ["url"] = Mirror + "/releases/v1.2.3/" + AssetName,
                ["size"] = Payload.Length, ["sha256"] = Digest
            },
            ["portable"] = new JObject
            {
                ["url"] = Mirror + "/releases/v1.2.3/T7-Rekindle-windows-x64.zip",
                ["size"] = Payload.Length, ["sha256"] = Digest
            }
        };

        internal static JObject Release() => new JObject
        {
            ["tag_name"] = "v1.2.3", ["html_url"] = Repository + "/releases/tag/v1.2.3",
            ["body"] = "更新说明", ["draft"] = false, ["prerelease"] = false,
            ["assets"] = new JArray(new JObject
            {
                ["name"] = AssetName, ["state"] = "uploaded", ["browser_download_url"] = GitHubAsset,
                ["size"] = Payload.Length, ["digest"] = "sha256:" + Digest
            })
        };

        internal static HttpResponseMessage Json(JObject value) => new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent(value?.ToString() ?? "invalid-json", Encoding.UTF8, "application/json")
        };

        internal static HttpResponseMessage Bytes() => new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new ByteArrayContent(Payload)
        };
    }

    internal sealed class UpdateResponseHandler : HttpMessageHandler
    {
        private readonly Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> _respond;
        internal int RequestCount { get; private set; }
        internal UpdateResponseHandler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> respond) =>
            _respond = respond;
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            RequestCount++;
            return _respond(request, token);
        }
    }
}
