using System;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using T7.Rekindle.Desktop.ViewModels;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class GameServerTests
    {
        internal static void Run() => RunTask(CheckAsync());
        private static async Task CheckAsync()
        {
            var root = Path.Combine(Path.GetTempPath(), "t7-server-settings-" + Guid.NewGuid().ToString("N"));
            var ticket = new string('a', 64);
            var returnedName = "Player";
            const string identity = "00000000-0000-4000-8000-000000000001";
            int calls = 0;
            try
            {
                var model = new GameServerViewModel(root, () => new HttpClient(new Handler(async request =>
                {
                    calls++;
                    Assert(request.RequestUri.AbsoluteUri == "https://server.example.invalid:8091/auth/guest", "game login endpoint was replaced");
                    var payload = JObject.Parse(await request.Content.ReadAsStringAsync());
                    Assert((string)payload["displayName"] == "Player" && payload["password"] == null && payload["accountId"] == null, "game login credentials changed");
                    return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(new JObject {
                        ["accountId"] = identity, ["displayName"] = returnedName, ["ticket"] = ticket, ["expiresInSeconds"] = 120 }.ToString()) };
                }))) { Enabled = true, Host = "server.example.invalid", Port = "8091" };
                await model.PrepareAsync("Player", CancellationToken.None);
                var ini = File.ReadAllText(Path.Combine(root, "custom-room.ini"));
                Assert(ini.Contains("serverHost=server.example.invalid") && ini.Contains("serverPort=8091") && ini.Contains("secure=true"), "native endpoint was not saved");
                Assert(!ini.Contains(ticket) && !ini.Contains("test-password-123"), "game credential persisted in plaintext");
                var encrypted = ini.Split(new[] { "protectedTicket=" }, StringSplitOptions.None)[1].Trim();
                Assert(Encoding.ASCII.GetString(ProtectedData.Unprotect(Convert.FromBase64String(encrypted), null, DataProtectionScope.CurrentUser)) == ticket,
                    "native ticket protection did not round trip");
                var restored = new GameServerViewModel(root);
                Assert(restored.Enabled && restored.Host == model.Host && restored.Port == model.Port, "endpoint preferences did not persist");
                var saved = File.ReadAllText(Path.Combine(root, "game-server.json"));
                Assert(!saved.Contains(ticket) && !saved.Contains("test-password-123"), "preferences contained credentials");
                returnedName = "Another Player";
                try { await model.PrepareAsync("Player", CancellationToken.None); throw new Exception("foreign identity accepted"); }
                catch (InvalidDataException) { }
                Assert(File.ReadAllText(Path.Combine(root, "custom-room.ini")) == ini, "failed login replaced native credentials");
                model.Host = "invalid/host";
                try { await model.PrepareAsync("Player", CancellationToken.None); throw new Exception("invalid host accepted"); }
                catch (InvalidOperationException) { }
                Assert(calls == 2, "invalid endpoint reached network");
                model.Enabled = false;
                await model.PrepareAsync("Player", CancellationToken.None);
                Assert(!File.Exists(Path.Combine(root, "custom-room.ini")), "local mode retained online routing");
            }
            finally { if (Directory.Exists(root)) Directory.Delete(root, true); }
        }
        private sealed class Handler : HttpMessageHandler
        {
            private readonly Func<HttpRequestMessage, Task<HttpResponseMessage>> _send;
            internal Handler(Func<HttpRequestMessage, Task<HttpResponseMessage>> send) { _send = send; }
            protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) => _send(request);
        }
    }
}
