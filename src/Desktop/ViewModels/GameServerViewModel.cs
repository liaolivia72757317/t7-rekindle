using System;
using System.IO;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using T7.Rekindle.Desktop.Services;

namespace T7.Rekindle.Desktop.ViewModels
{
    // Endpoint preferences persist; one-use guest tickets are protected by Windows.
    public sealed class GameServerViewModel : ObservableObject
    {
        private readonly string _directory;
        private readonly Func<HttpClient> _clientFactory;
        private bool _enabled;
        private string _host = string.Empty, _port = "8091";
        public bool Enabled { get => _enabled; set { SetProperty(ref _enabled, value); } }
        public string Host { get => _host; set { SetProperty(ref _host, value ?? string.Empty); } }
        public string Port { get => _port; set { SetProperty(ref _port, value ?? string.Empty); } }

        internal GameServerViewModel(string directory, Func<HttpClient> clientFactory = null)
        {
            _directory = directory;
            _clientFactory = clientFactory ?? (() => new HttpClient(new HttpClientHandler { UseProxy = false, AllowAutoRedirect = false, UseCookies = false }));
            var path = Path.Combine(directory, "game-server.json");
            if (!File.Exists(path)) return;
            try
            {
                var value = JObject.Parse(File.ReadAllText(path, Encoding.UTF8));
                if (value.Count != 3 || value["enabled"]?.Type != JTokenType.Boolean
                    || value["host"]?.Type != JTokenType.String || value["port"]?.Type != JTokenType.String) return;
                _enabled = (bool)value["enabled"];
                _host = (string)value["host"];
                _port = (string)value["port"];
            }
            catch (Exception error) when (error is IOException || error is JsonException) { }
        }

        internal async Task PrepareAsync(string playerName, CancellationToken cancellation)
        {
            Directory.CreateDirectory(_directory);
            var ini = Path.Combine(_directory, "custom-room.ini");
            if (!Enabled)
            {
                if (File.Exists(ini)) File.Delete(ini);
                SavePreferences();
                return;
            }
            var host = Host.Trim();
            if (Uri.CheckHostName(host) == UriHostNameType.Unknown || !ushort.TryParse(Port, out var port) || port == 0)
                throw new InvalidOperationException("请填写有效的服务器 IP 或域名和端口。");
            var nameError = T7.Rekindle.Core.PlayerNameRules.Validate(playerName);
            if (nameError.Length != 0) throw new InvalidOperationException(nameError);
            var endpoint = new UriBuilder("https", host, port, "/auth/guest").Uri;
            using (var client = _clientFactory())
            using (var limit = CancellationTokenSource.CreateLinkedTokenSource(cancellation))
            {
                client.MaxResponseContentBufferSize = 4096;
                limit.CancelAfter(TimeSpan.FromSeconds(10));
                var payload = JsonConvert.SerializeObject(new { displayName = playerName });
                using (var content = new StringContent(payload, Encoding.UTF8, "application/json"))
                using (var response = await client.PostAsync(endpoint, content, limit.Token).ConfigureAwait(true))
                {
                    if (!response.IsSuccessStatusCode)
                        throw new InvalidOperationException(response.StatusCode == (System.Net.HttpStatusCode)429
                            ? "连接尝试过于频繁，请稍后重试。" : "游戏服务接入失败，请检查服务器状态。");
                    var reply = JObject.Parse(await response.Content.ReadAsStringAsync().ConfigureAwait(true));
                    var ticket = (string)reply["ticket"];
                    var identity = (string)reply["accountId"];
                    if (!Guid.TryParseExact(identity, "D", out var id) || id.ToString("D") != identity
                        || (string)reply["displayName"] != playerName || ticket?.Length != 64
                        || System.Linq.Enumerable.Any(ticket, c => !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f')))
                        throw new InvalidDataException("游戏接入票据响应无效。");
                    var protectedTicket = Convert.ToBase64String(ProtectedData.Protect(Encoding.ASCII.GetBytes(ticket), null, DataProtectionScope.CurrentUser));
                    SettingsService.WriteJson(ini, "[custom-room]\r\nserverHost=" + host + "\r\nserverPort=" + port
                        + "\r\nsecure=true\r\naccountId=" + identity + "\r\nprotectedTicket=" + protectedTicket + "\r\n");
                    SavePreferences();
                }
            }
        }
        private void SavePreferences() => SettingsService.WriteJson(Path.Combine(_directory, "game-server.json"),
            JsonConvert.SerializeObject(new { enabled = Enabled, host = Host.Trim(), port = Port }));
    }
}
