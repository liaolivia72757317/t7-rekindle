using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using T7R.Desktop.Services;
using T7R.Desktop.ViewModels;
using static T7R.ManagedHarness.LauncherTests;

namespace T7R.ManagedHarness
{
    internal static partial class AccountTests
    {
        private const string Machine = "12345678-1234-1234-1234-123456789abc";
        internal static void Run() { RunAsync().GetAwaiter().GetResult(); GameNameLaunchWorkflow(); TitleBridgeRoundTrip(); }
        internal static void Render(string output)
        {
            using (var warnings = new StringWriter())
            using (var listener = new TextWriterTraceListener(warnings))
            {
                var source = PresentationTraceSources.DataBindingSource;
                var previousLevel = source.Switch.Level;
                source.Switch.Level = SourceLevels.Warning;
                source.Listeners.Add(listener);
                try
                {
                    RenderAccountUi(output);
                    RenderGameNameSetup(output);
                    RenderTitles(output);
                    RenderAccountLoading(output);
                    VerifyAccountLoadingAnimation();
                    QqLoginUiTests.Run(output);
                    Assert(warnings.ToString().Length == 0, "account WPF binding warning: " + warnings);
                }
                finally { source.Listeners.Remove(listener); source.Switch.Level = previousLevel; }
            }
        }
        private static async Task RunAsync()
        {
            AddressValidation();
            await GameNameRegistrationAsync();
            await GameNameSyncAsync();
            await GameNameHttpAsync();
            await PresentationStateAsync();
            await WaitingForServerAsync();
            await RestoreWaitingForServerAsync();
            await CancelWaitingForServerAsync();
            await LoginAndKickAsync();
            await OnlineTakeoverAsync();
            await GroupMembershipErrorsAsync();
            await SilentLogoutAndLateLoginAsync();
            await RestoreAndHeartbeatAsync();
            await PushReconnectAndCloseAsync();
            await HttpContractsAsync("https://auth.example.invalid/");
            await RunTitlesAsync();
#if DEBUG
            await HttpContractsAsync("http://localhost:8080/");
#endif
            ProtectedStore();
        }

        private static void AddressValidation()
        {
            foreach (string address in new[] { "https://auth.example.invalid", "https://localhost:8443/" })
                CheckAddress(address, true);
#if DEBUG
            const bool allowLoopbackHttp = true;
#else
            const bool allowLoopbackHttp = false;
#endif
            foreach (string address in new[] { "http://localhost:8080", "http://127.0.0.1:8080/", "http://[::1]:8080/" })
                CheckAddress(address, allowLoopbackHttp);
            foreach (string address in new[]
            {
                "http://auth.example.invalid", "http://192.0.2.1:8080", "http://0.0.0.0:8080",
                "http://localhost.example.invalid:8080", "http://user@localhost:8080",
                "http://localhost:8080/?query=value", "http://localhost:8080/#fragment",
                "https://user@auth.example.invalid", "https://auth.example.invalid/?query=value",
                "https://auth.example.invalid/#fragment", "ftp://localhost", "relative/path"
            }) CheckAddress(address, false);
        }

        private static void CheckAddress(string address, bool expected)
        {
            bool accepted = false;
            using (var client = new HttpClient())
            {
                try { using (var api = new AccountApi(address, client)) accepted = true; }
                catch (ArgumentException) { }
            }
            Assert(accepted == expected, "account address validation mismatch: " + address);
        }

        private static async Task LoginAndKickAsync()
        {
            var api = new FakeApi(); var store = new MemoryStore(); var time = new ManualDelay();
            using (var model = Model(api, store, time))
            {
                await model.LoginAsync();
                Assert(model.IsSignedIn && model.Status == "已登录 · 在线", "QQ login did not become online");
                Assert(store.Value?.LoginName == "12345" && api.OnlineCalls == 1, "numeric login or persisted session missing");
                var oldKick = api.Kick;
                api.Kick(new AccountKick(api.Session.SessionId, Guid.NewGuid().ToString(), "MACHINE_CHANGED"));
                Assert(model.IsSignedIn, "stale connection event kicked current account");
                api.Kick(new AccountKick(api.Session.SessionId, api.Connection.ConnectionId, "MACHINE_CHANGED"));
                Assert(!model.HasSession && store.Value == null && model.Status.Contains("其他机器"), "remote kick did not clear account");
                Assert(api.LogoutCalls == 0, "remote kick revoked a possibly reused session");
                api.Session = Session();
                await model.LoginAsync();
                oldKick(new AccountKick(api.Session.SessionId, api.Connection.ConnectionId, "ADMIN_REVOKED"));
                Assert(model.IsSignedIn, "previous login generation changed new login");
            }
        }

        private static async Task OnlineTakeoverAsync()
        {
            foreach (string source in new[] { "heartbeat", "push", "kick" })
            {
                var api = new FakeApi(); var store = new MemoryStore(); var time = new ManualDelay();
                using (var model = Model(api, store, time))
                {
                    if (source == "push") api.ListenError = new AccountApiException(409, "ONLINE_TAKEN_OVER");
                    await model.LoginAsync();
                    var previousKick = api.Kick;
                    string previousConnection = api.Connection.ConnectionId;
                    if (source == "heartbeat")
                    {
                        api.HeartbeatError = new AccountApiException(409, "ONLINE_TAKEN_OVER");
                        await time.TickAsync(15);
                        await Until(() => model.Status.Contains("其他连接"));
                    }
                    else if (source == "kick")
                        api.Kick(new AccountKick(api.Session.SessionId, previousConnection, "ONLINE_TAKEN_OVER"));

                    Assert(model.IsSignedIn && model.HasSession && store.Value == api.Session,
                        source + " takeover discarded a valid session");
                    Assert(model.CanReconnect && model.ReconnectCommand.CanExecute(null) && !model.LoginCommand.CanExecute(null),
                        source + " takeover did not offer explicit re-online");
                    Assert(api.ListenCancellation.IsCancellationRequested && api.OnlineCalls == 1
                        && api.LogoutCalls == 0 && api.OfflineCalls == 0,
                        source + " takeover did not stop the old connection without revoking or replacing it");

                    api.HeartbeatError = api.ListenError = null;
                    api.Connection = new AccountConnection(Guid.NewGuid().ToString(), DateTimeOffset.UtcNow.AddSeconds(45), 15);
                    await model.ReconnectAsync();
                    Assert(model.Status == "已登录 · 在线" && api.OnlineCalls == 2 && api.LoginCalls == 1
                        && api.LastOnlineSession == store.Value && store.Value == api.Session,
                        source + " explicit re-online did not reuse the saved session");
                    previousKick?.Invoke(new AccountKick(api.Session.SessionId, previousConnection, "MACHINE_CHANGED"));
                    Assert(model.IsSignedIn && !model.CanReconnect, "old connection event changed the reconnected session");
                }
            }
        }

        private static async Task GroupMembershipErrorsAsync()
        {
            var api = new FakeApi(); var store = new MemoryStore(); var time = new ManualDelay();
            const string groupMessage = "当前 QQ 未加入指定群（群号：123456），请加入后重试。";
            using (var model = Model(api, store, time))
            {
                api.LoginError = new AccountApiException(403, "QQ_GROUP_NOT_MEMBER", groupMessage);
                await model.LoginAsync();
                Assert(model.Status == groupMessage && !model.HasSession, "group rejection lost the server message");
                api.LoginError = null;
                await model.LoginAsync();
                api.Kick(new AccountKick(api.Session.SessionId, api.Connection.ConnectionId, "QQ_GROUP_NOT_MEMBER", "123456"));
                Assert(model.IsSignedIn && model.HasSession && model.CanReconnect && model.Status.Contains("123456"),
                    "group kick did not preserve the session or show the target group");
                api.OnlineError = new AccountApiException(503, "QQ_GROUP_UNAVAILABLE", "入群校验暂不可用，请稍后重试");
                await model.ReconnectAsync();
                Assert(model.HasSession && model.CanReconnect && model.Status.Contains("校验暂不可用"), "group outage cleared the saved session");
                api.OnlineError = null;
                api.Connection = new AccountConnection(Guid.NewGuid().ToString(), DateTimeOffset.UtcNow.AddSeconds(45), 15);
                await model.ReconnectAsync();
                api.HeartbeatError = new AccountApiException(403, "QQ_GROUP_NOT_MEMBER", groupMessage);
                await time.TickAsync(15);
                await Until(() => model.CanReconnect);
                Assert(model.Status == groupMessage && store.Value != null, "heartbeat group rejection discarded login");
            }
            using (var handler = new ResponseHandler(request => Task.FromResult(new HttpResponseMessage(HttpStatusCode.Forbidden)
            { Content = new StringContent(JsonConvert.SerializeObject(new { code = "QQ_GROUP_NOT_MEMBER", message = groupMessage })) })))
            using (var client = new AccountApi("https://auth.example.invalid/", new HttpClient(handler)))
            {
                try { await client.LoginAsync(Machine, "TEST_COOKIE", CancellationToken.None); throw new InvalidOperationException("expected group rejection"); }
                catch (AccountApiException error) { Assert(error.Message == groupMessage && error.Code == "QQ_GROUP_NOT_MEMBER", "HTTP group message lost"); }
            }
        }

        private static async Task SilentLogoutAndLateLoginAsync()
        {
            var api = new FakeApi(); var store = new MemoryStore(); var time = new ManualDelay();
            using (var model = Model(api, store, time))
            {
                await model.LoginAsync();
                api.LogoutError = new IOException("TEST_FAILURE");
                await model.LogoutAsync();
                Assert(model.Status == "未登录" && store.Value == null && !model.IsSignedIn, "logout failure changed local signed-out UI");
                var pending = new TaskCompletionSource<AccountSession>();
                api.PendingLogin = pending.Task;
                var login = model.LoginAsync();
                await model.LogoutAsync();
                pending.SetResult(api.Session);
                await login;
                Assert(!model.HasSession && store.Value == null && api.OnlineCalls == 1, "late login response resurrected signed-out account");
                api.PendingLogin = null;
                await model.LoginAsync();
                var pendingLogout = new TaskCompletionSource<bool>();
                api.PendingLogout = pendingLogout.Task;
                var logout = model.LogoutAsync();
                Assert(!model.HasSession, "logout waited for network before clearing local state");
                api.Session = Session();
                await model.LoginAsync();
                pendingLogout.SetException(new IOException("TEST_LATE_FAILURE"));
                await logout;
                Assert(model.IsSignedIn && store.Value.SessionId == api.Session.SessionId, "old logout response cleared new session");
            }
        }

        private static async Task RestoreAndHeartbeatAsync()
        {
            var api = new FakeApi(); var store = new MemoryStore { Value = api.Session }; var time = new ManualDelay();
            using (var model = Model(api, store, time))
            {
                await model.RestoreAsync();
                Assert(api.CurrentCalls == 1 && api.OnlineCalls == 1 && model.IsSignedIn, "startup did not validate saved session");
                api.HeartbeatError = new AccountApiException(503, "SERVICE_UNAVAILABLE");
                await time.TickAsync(15);
                await Until(() => model.Status.Contains("网络连接异常"));
                Assert(model.HasSession, "transient outage deleted session");
                api.HeartbeatError = new AccountApiException(409, "NOT_ONLINE");
                await time.TickAsync(15);
                await Until(() => model.CanReconnect);
                Assert(model.IsSignedIn && api.OnlineCalls == 1, "expired lease automatically stole online ownership");
                api.HeartbeatError = null;
                await model.ReconnectAsync();
                Assert(api.OnlineCalls == 2 && model.Status == "已登录 · 在线", "explicit re-online failed");
                api.HeartbeatError = new AccountApiException(401, "INVALID_CREDENTIALS");
                await time.TickAsync(15);
                await Until(() => !model.HasSession);
                Assert(store.Value == null, "heartbeat revocation did not remove saved token");
            }
            api = new FakeApi { CurrentError = new AccountApiException(401, "INVALID_CREDENTIALS") };
            store = new MemoryStore { Value = api.Session };
            using (var model = Model(api, store, new ManualDelay()))
            {
                await model.RestoreAsync();
                Assert(!model.HasSession && store.Value == null && api.OnlineCalls == 0, "invalid restored session went online");
            }
            api = new FakeApi(); store = new MemoryStore { Value = Session("other-machine") };
            using (var model = Model(api, store, new ManualDelay()))
            {
                await model.RestoreAsync();
                Assert(api.CurrentCalls == 0 && store.Value == null, "session from another machine was replayed");
            }
        }

        private static async Task PushReconnectAndCloseAsync()
        {
            var api = new FakeApi { ListenError = new IOException("TEST_DISCONNECT") };
            var store = new MemoryStore(); var time = new ManualDelay();
            using (var model = Model(api, store, time))
            {
                await model.LoginAsync();
                api.ListenError = null;
                await time.TickAsync(1);
                await Until(() => api.ListenCalls == 2);
                Assert(api.OnlineCalls == 1, "SSE reconnect acquired a new online connection");
                await model.SuspendAsync();
                Assert(api.OfflineCalls == 1 && api.LogoutCalls == 0 && store.Value != null, "closing launcher revoked saved session");
            }
        }

        private static async Task HttpContractsAsync(string address)
        {
            var session = Session();
            using (var handler = new ResponseHandler(async request =>
            {
                Assert(request.RequestUri.GetLeftPart(UriPartial.Authority) == new Uri(address).GetLeftPart(UriPartial.Authority),
                    "account request did not use the configured service address");
                string path = request.RequestUri.AbsolutePath;
                if (path.EndsWith("qq/sessions", StringComparison.Ordinal))
                {
                    Assert(request.Headers.Authorization == null, "QQ exchange unexpectedly requires application token");
                    string body = await request.Content.ReadAsStringAsync();
                    Assert(body.Contains("machineId") && body.Contains("qqSessionCookie"), "QQ exchange wire fields incorrect");
                    return Json(session);
                }
                Assert(request.Headers.Authorization?.Parameter == session.SessionToken, "Bearer token missing");
                Assert(string.Join("", request.Headers.GetValues("X-Machine-Id")) == Machine, "machine header missing");
                Assert(!request.RequestUri.Query.Contains(session.SessionToken), "token included in URL");
                if (path.EndsWith("events", StringComparison.Ordinal))
                    return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(
                        "event: ready\ndata: {}\n\n: keepalive\n\nevent: kicked\ndata: " +
                        JsonConvert.SerializeObject(new AccountKick(session.SessionId, "connection", "ADMIN_REVOKED")) + "\n\n", Encoding.UTF8, "text/event-stream") };
                return Json(session.WithToken(null));
            }))
            using (var api = new AccountApi(address, new HttpClient(handler)))
            {
                Assert((await api.LoginAsync(Machine, "TEST_COOKIE", CancellationToken.None)).LoginName == "12345", "QQ response decoding failed");
                Assert((await api.CurrentAsync(session, CancellationToken.None)).SessionToken == session.SessionToken, "restoration discarded token");
                bool ready = false; AccountKick kick = null;
                try { await api.ListenAsync(session, "connection", () => ready = true, value => kick = value, CancellationToken.None); }
                catch (IOException) { }
                Assert(ready && kick?.Reason == "ADMIN_REVOKED", "SSE stream was not decoded incrementally");
            }
        }

        private static void ProtectedStore()
        {
            string directory = Path.Combine(Path.GetTempPath(), "T7R-account-tests-" + Guid.NewGuid().ToString("N"));
            try
            {
                var store = new AccountSessionStore("https://auth.example.invalid", directory);
                var session = Session();
                Assert(store.Load() == null, "missing store should be empty");
                store.Save(session);
                Assert(!File.ReadAllText(Path.Combine(directory, "auth-session.json")).Contains(session.SessionToken), "token stored as plaintext");
                Assert(store.Load().SessionToken == session.SessionToken, "DPAPI session did not round trip");
                Assert(new AccountSessionStore("https://other.example.invalid", directory).Load() == null, "session leaked across backends");
                store.Save(session);
                store.Clear();
                Assert(store.Load() == null, "logout did not clear persisted token");
            }
            finally { if (Directory.Exists(directory)) Directory.Delete(directory, true); }
        }

        private static AccountViewModel Model(FakeApi api, MemoryStore store, ManualDelay time) =>
            new AccountViewModel(api, store, () => Machine, token => Task.FromResult("TEST_COOKIE"), action => action(), time.Delay);
        private static AccountSession Session(string machine = Machine) => new AccountSession(Guid.NewGuid().ToString(), new string('A', 43),
            DateTimeOffset.UtcNow.AddDays(30), Guid.NewGuid().ToString(), "12345", machine, "12345", "测试昵称", "https://q.qlogo.cn/g?b=qq&nk=12345&s=100", "测试玩家");
        private static HttpResponseMessage Json(object body) => new HttpResponseMessage(HttpStatusCode.OK)
            { Content = new StringContent(JsonConvert.SerializeObject(body), Encoding.UTF8, "application/json") };
        private static async Task Until(Func<bool> condition)
        {
            for (int i = 0; i < 200; i++) { if (condition()) return; await Task.Delay(10); }
            throw new Exception("account asynchronous state timed out");
        }
        private sealed class MemoryStore : IAccountSessionStore
        {
            public AccountSession Value;
            public AccountSession Load() => Value;
            public void Save(AccountSession value) => Value = value;
            public void Clear() => Value = null;
        }
        private sealed class ManualDelay
        {
            private readonly List<Tuple<int, TaskCompletionSource<bool>>> _pending = new List<Tuple<int, TaskCompletionSource<bool>>>();
            public Task Delay(TimeSpan time, CancellationToken cancellation)
            {
                var result = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                cancellation.Register(() => result.TrySetCanceled());
                lock (_pending) _pending.Add(Tuple.Create((int)time.TotalSeconds, result));
                return result.Task;
            }
            public async Task TickAsync(int seconds)
            {
                TaskCompletionSource<bool> next = null;
                await Until(() =>
                {
                    lock (_pending)
                    {
                        var match = _pending.Find(item => item.Item1 == seconds && !item.Item2.Task.IsCompleted);
                        if (match == null) return false;
                        _pending.Remove(match); next = match.Item2; return true;
                    }
                });
                next.SetResult(true);
            }
        }
        private sealed class ResponseHandler : HttpMessageHandler
        {
            private readonly Func<HttpRequestMessage, Task<HttpResponseMessage>> _response;
            public ResponseHandler(Func<HttpRequestMessage, Task<HttpResponseMessage>> response) { _response = response; }
            protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) => _response(request);
        }
        private sealed partial class FakeApi : IAccountApi
        {
            public AccountSession Session = AccountTests.Session();
            public AccountConnection Connection = new AccountConnection(Guid.NewGuid().ToString(), DateTimeOffset.UtcNow.AddSeconds(45), 15);
            public Action<AccountKick> Kick;
            public Exception LogoutError, CurrentError, HeartbeatError, ListenError, LoginError, OnlineError;
            public Task<AccountSession> PendingLogin, PendingCurrent;
            public Task<AccountConnection> PendingOnline;
            public Task PendingLogout;
            public AccountSession LastOnlineSession;
            public CancellationToken ListenCancellation;
            public Task<AccountGameName> PendingName;
            public Exception NameError;
            public int NameCalls, RegistrationCalls, SetNameCalls;
            public int LoginCalls, OnlineCalls, LogoutCalls, CurrentCalls, ListenCalls, OfflineCalls;
            public Task<AccountSession> LoginAsync(string machine, string cookie, CancellationToken token)
            { LoginCalls++; return LoginError == null ? PendingLogin ?? Task.FromResult(Session) : Task.FromException<AccountSession>(LoginError); }
            public Task<AccountSession> CompleteRegistrationAsync(string machine, string cookie, string name, CancellationToken token)
            { RegistrationCalls++; Session = Session.WithGameName(name); return Task.FromResult(Session); }
            public Task<AccountGameName> GameNameAsync(AccountSession session, CancellationToken token)
            { NameCalls++; return NameError == null ? PendingName ?? Task.FromResult(new AccountGameName(Session.GameName)) : Task.FromException<AccountGameName>(NameError); }
            public Task<AccountGameName> SetGameNameAsync(AccountSession session, string name, CancellationToken token)
            { SetNameCalls++; Session = Session.WithGameName(name); return Task.FromResult(new AccountGameName(name)); }
            public Task<AccountSession> CurrentAsync(AccountSession session, CancellationToken token)
            { CurrentCalls++; return CurrentError == null ? PendingCurrent ?? Task.FromResult(Session) : Task.FromException<AccountSession>(CurrentError); }
            public Task<AccountConnection> OnlineAsync(AccountSession session, CancellationToken token)
            { OnlineCalls++; LastOnlineSession = session; return OnlineError == null ? PendingOnline ?? Task.FromResult(Connection) : Task.FromException<AccountConnection>(OnlineError); }
            public Task<AccountConnection> HeartbeatAsync(AccountSession session, string connection, CancellationToken token) =>
                HeartbeatError == null ? Task.FromResult(Connection) : Task.FromException<AccountConnection>(HeartbeatError);
            public Task OfflineAsync(AccountSession session, string connection, CancellationToken token)
            { OfflineCalls++; return Task.CompletedTask; }
            public Task LogoutAsync(AccountSession session, CancellationToken token)
            { LogoutCalls++; return PendingLogout ?? (LogoutError == null ? Task.CompletedTask : Task.FromException(LogoutError)); }
            public Task ListenAsync(AccountSession session, string connection, Action ready, Action<AccountKick> kick, CancellationToken token)
            {
                ListenCalls++;
                ListenCancellation = token;
                if (ListenError != null) return Task.FromException(ListenError);
                Kick = kick; ready();
                var result = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                token.Register(() => result.TrySetCanceled());
                return result.Task;
            }
            public void Dispose() { }
        }
    }
}
