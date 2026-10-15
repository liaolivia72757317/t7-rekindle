using System;
using System.IO;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace T7R.Desktop.Services
{
    public sealed class AccountApi : IAccountApi, IAccountTitlesApi
    {
        private readonly HttpClient _client;
        private readonly Uri _baseAddress;

        public AccountApi(string address) : this(address, new HttpClient(new HttpClientHandler
            { AllowAutoRedirect = false, UseCookies = false, MaxConnectionsPerServer = 8 })) { }

        internal AccountApi(string address, HttpClient client)
        {
            if (!Uri.TryCreate(address.TrimEnd('/') + "/", UriKind.Absolute, out _baseAddress)
                || !IsAllowedScheme(_baseAddress) || _baseAddress.UserInfo.Length != 0
                || _baseAddress.Query.Length != 0 || _baseAddress.Fragment.Length != 0)
                throw new ArgumentException("账号服务地址必须为 HTTPS；仅 Debug 构建允许本机回环 HTTP。", nameof(address));
            _client = client;
            _client.Timeout = Timeout.InfiniteTimeSpan;
        }

        private static bool IsAllowedScheme(Uri address)
        {
            if (address.Scheme == Uri.UriSchemeHttps) return true;
#if DEBUG
            return address.Scheme == Uri.UriSchemeHttp && address.IsLoopback;
#else
            return false;
#endif
        }

        public Task<AccountSession> LoginAsync(string machineId, string cookie, CancellationToken cancellation) =>
            SendAsync<AccountSession>(HttpMethod.Post, "qq/sessions", null, new { machineId, qqSessionCookie = cookie }, cancellation);

        public Task<AccountSession> CompleteRegistrationAsync(string machineId, string cookie, string gameName, CancellationToken cancellation) =>
            SendAsync<AccountSession>(HttpMethod.Post, "qq/sessions", null, new { machineId, qqSessionCookie = cookie, gameName }, cancellation);
        public Task<AccountGameName> GameNameAsync(AccountSession session, CancellationToken cancellation) =>
            SendAsync<AccountGameName>(HttpMethod.Get, "game-name", session, null, cancellation);
        public Task<AccountGameName> SetGameNameAsync(AccountSession session, string gameName, CancellationToken cancellation) =>
            SendAsync<AccountGameName>(HttpMethod.Put, "game-name", session, new { gameName }, cancellation);

        public async Task<AccountSession> CurrentAsync(AccountSession session, CancellationToken cancellation) =>
            (await SendAsync<AccountSession>(HttpMethod.Get, "session", session, null, cancellation).ConfigureAwait(false))
                .WithToken(session.SessionToken);

        public Task<AccountConnection> OnlineAsync(AccountSession session, CancellationToken cancellation) =>
            SendAsync<AccountConnection>(HttpMethod.Post, "online", session, null, cancellation);
        public Task<AccountConnection> HeartbeatAsync(AccountSession session, string connectionId, CancellationToken cancellation) =>
            SendAsync<AccountConnection>(HttpMethod.Post, ConnectionPath(connectionId) + "/heartbeat", session, null, cancellation);
        public Task OfflineAsync(AccountSession session, string connectionId, CancellationToken cancellation) =>
            SendAsync<object>(HttpMethod.Delete, ConnectionPath(connectionId), session, null, cancellation);
        public Task LogoutAsync(AccountSession session, CancellationToken cancellation) =>
            SendAsync<object>(HttpMethod.Delete, "session", session, null, cancellation);

        private async Task<T> SendAsync<T>(HttpMethod method, string path, AccountSession session, object body,
            CancellationToken cancellation) where T : class
        {
            using (var limit = CancellationTokenSource.CreateLinkedTokenSource(cancellation))
            using (var request = Request(method, path, session))
            {
                limit.CancelAfter(TimeSpan.FromSeconds(path == "qq/sessions" ? 20 : 8));
                if (body != null) request.Content = new StringContent(JsonConvert.SerializeObject(body), Encoding.UTF8, "application/json");
                using (var response = await _client.SendAsync(request, limit.Token).ConfigureAwait(false))
                {
                    await RequireSuccessAsync(response).ConfigureAwait(false);
                    if (response.StatusCode == System.Net.HttpStatusCode.NoContent) return default(T);
                    string text = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                    return JsonConvert.DeserializeObject<T>(text) ?? throw new InvalidDataException("账号响应为空。");
                }
            }
        }

        public async Task ListenAsync(AccountSession session, string connectionId, Action ready, Action<AccountKick> kicked,
            CancellationToken cancellation)
        {
            using (var limit = CancellationTokenSource.CreateLinkedTokenSource(cancellation))
            using (var request = Request(HttpMethod.Get, ConnectionPath(connectionId) + "/events", session))
            {
                request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("text/event-stream"));
                limit.CancelAfter(TimeSpan.FromSeconds(8));
                using (var response = await _client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, limit.Token).ConfigureAwait(false))
                using (limit.Token.Register(response.Dispose))
                {
                    await RequireSuccessAsync(response).ConfigureAwait(false);
                    if (response.Content.Headers.ContentType?.MediaType != "text/event-stream")
                        throw new InvalidDataException("账号推送响应格式错误。");
                    using (var reader = new StreamReader(await response.Content.ReadAsStreamAsync().ConfigureAwait(false), Encoding.UTF8))
                    {
                        var data = new StringBuilder();
                        string eventName = string.Empty;
                        while (true)
                        {
                            limit.CancelAfter(TimeSpan.FromSeconds(45));
                            string line = await reader.ReadLineAsync().ConfigureAwait(false);
                            cancellation.ThrowIfCancellationRequested();
                            if (line == null) throw new IOException("账号推送连接已断开。");
                            if (line.Length == 0)
                            {
                                if (eventName == "ready") ready();
                                if (eventName == "kicked") kicked(JsonConvert.DeserializeObject<AccountKick>(data.ToString()));
                                eventName = string.Empty;
                                data.Clear();
                            }
                            else if (line.StartsWith("event:", StringComparison.Ordinal)) eventName = line.Substring(6).Trim();
                            else if (line.StartsWith("data:", StringComparison.Ordinal)) data.AppendLine(line.Substring(5).TrimStart());
                            if (data.Length > 16384) throw new InvalidDataException("账号推送事件过长。");
                        }
                    }
                }
            }
        }

        private HttpRequestMessage Request(HttpMethod method, string path, AccountSession session)
        {
            var request = new HttpRequestMessage(method, new Uri(_baseAddress, "api/v1/auth/" + path));
            if (session != null)
            {
                request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", session.SessionToken);
                request.Headers.Add("X-Machine-Id", session.MachineId);
            }
            return request;
        }

        private static async Task RequireSuccessAsync(HttpResponseMessage response)
        {
            if (response.IsSuccessStatusCode) return;
            string code = string.Empty;
            string message = null;
            try
            {
                var body = JObject.Parse(await response.Content.ReadAsStringAsync().ConfigureAwait(false));
                code = (string)body["code"] ?? string.Empty;
                if (code == "QQ_GROUP_NOT_MEMBER" || code == "QQ_GROUP_UNAVAILABLE") message = (string)body["message"];
            }
            catch (JsonException) { code = "INVALID_RESPONSE"; }
            throw new AccountApiException((int)response.StatusCode, code, message);
        }

        private static string ConnectionPath(string id) => "online/" + Uri.EscapeDataString(id);
        public Task<AccountTitles> TitlesAsync(AccountSession session, string connectionId, CancellationToken cancellation) =>
            SendAsync<AccountTitles>(HttpMethod.Get, ConnectionPath(connectionId) + "/titles", session, null, cancellation);
        public Task<AccountTitles> EquipTitleAsync(AccountSession session, string connectionId, int? titleId, CancellationToken cancellation) =>
            SendAsync<AccountTitles>(HttpMethod.Put, ConnectionPath(connectionId) + "/titles/equipped", session, new { titleId }, cancellation);
        public void Dispose() => _client.Dispose();
    }
}
