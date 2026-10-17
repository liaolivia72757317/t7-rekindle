using System;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using T7R.Core;
using T7R.Desktop.Services;
using T7R.Desktop.ViewModels;
using T7R.Desktop.Views;
using static T7R.ManagedHarness.LauncherTests;

namespace T7R.ManagedHarness
{
    internal static partial class AccountTests
    {
        private static async Task GameNameRegistrationAsync()
        {
            var api = new FakeApi { LoginError = new AccountApiException(409, "REGISTRATION_REQUIRED") };
            var store = new MemoryStore();
            AccountViewModel model = null;
            model = new AccountViewModel(api, store, () => Machine, token => Task.FromResult("TEST_COOKIE"), action => action(),
                new ManualDelay().Delay, async (submit, token) =>
                {
                    Assert(model.IsRegistrationOpen && !model.IsSignedIn && store.Value == null && api.OnlineCalls == 0,
                        "incomplete registration was accepted as a signed-in account");
                    return await submit("注册玩家", token);
                });
            using (model)
            {
                await model.LoginAsync();
                Assert(model.IsSignedIn && model.GameName == "注册玩家" && store.Value.GameName == "注册玩家"
                    && api.RegistrationCalls == 1 && api.OnlineCalls == 1 && !model.IsRegistrationOpen, "registration did not finish before online");
            }
            api = new FakeApi { LoginError = new AccountApiException(409, "REGISTRATION_REQUIRED") };
            store = new MemoryStore();
            using (model = new AccountViewModel(api, store, () => Machine, token => Task.FromResult("TEST_COOKIE"), action => action(),
                new ManualDelay().Delay, (submit, token) => Task.FromResult<AccountSession>(null)))
            {
                await model.LoginAsync();
                Assert(!model.HasSession && !model.IsSignedIn && api.RegistrationCalls == 0 && api.OnlineCalls == 0,
                    "cancelled registration created or retained a session");
            }
            api = new FakeApi(); api.Session = api.Session.WithGameName(null);
            store = new MemoryStore { Value = api.Session };
            using (model = new AccountViewModel(api, store, () => Machine, token => Task.FromResult("TEST_COOKIE"), action => action(),
                new ManualDelay().Delay, (submit, token) => submit("历史玩家", token)))
            {
                await model.RestoreAsync();
                Assert(model.IsSignedIn && model.GameName == "历史玩家" && api.SetNameCalls == 1 && api.LoginCalls == 0,
                    "legacy cache did not initialize the server game name");
            }
            api = new FakeApi(); api.Session = api.Session.WithGameName(null);
            store = new MemoryStore { Value = api.Session };
            using (model = new AccountViewModel(api, store, () => Machine, token => Task.FromResult("TEST_COOKIE"), action => action(),
                new ManualDelay().Delay, (submit, token) => Task.FromResult<AccountSession>(null)))
            {
                await model.RestoreAsync();
                Assert(!model.HasSession && store.Value == null && api.LogoutCalls == 1 && api.OnlineCalls == 0,
                    "cancelling legacy completion did not log out");
            }
            api = new FakeApi { CurrentError = new IOException("TEST_OFFLINE") };
            using (model = Model(api, new MemoryStore { Value = api.Session }, new ManualDelay()))
            {
                await model.RestoreAsync();
                Assert(!model.IsSignedIn && api.OnlineCalls == 0, "offline restart accepted an unverified cache");
            }
        }

        private static async Task GameNameSyncAsync()
        {
            var api = new FakeApi(); var time = new ManualDelay(); var store = new MemoryStore();
            using (var model = Model(api, store, time))
            {
                await model.LoginAsync();
                api.Kick(new AccountKick(api.Session.SessionId, api.Connection.ConnectionId, "ONLINE_TAKEN_OVER"));
                api.Session = api.Session.WithGameName("服务端改名");
                await time.TickAsync(30); await Until(() => model.GameName == "服务端改名");
                Assert(model.IsSignedIn && !model.IsOnline && store.Value.GameName == "服务端改名", "offline login stopped name synchronization");
                api.NameError = new IOException("TEST_OFFLINE");
                int calls = api.NameCalls;
                await time.TickAsync(30); await Until(() => api.NameCalls > calls);
                Assert(model.IsSignedIn && model.GameName == "服务端改名", "network failure unlocked or discarded account name");
                api.NameError = null;
                var late = new TaskCompletionSource<AccountGameName>(); api.PendingName = late.Task;
                calls = api.NameCalls;
                await time.TickAsync(30); await Until(() => api.NameCalls > calls);
                await model.LogoutAsync();
                api.PendingName = null; api.Session = Session().WithGameName("新账号");
                await model.LoginAsync();
                late.SetResult(new AccountGameName("旧响应"));
                await Task.Delay(30);
                Assert(model.GameName == "新账号", "late name response overwrote a new login");
                api.NameError = new AccountApiException(401, "INVALID_CREDENTIALS");
                await time.TickAsync(30); await Until(() => !model.IsSignedIn);
                Assert(store.Value == null, "name synchronization kept a revoked session");
            }
        }

        private static async Task GameNameHttpAsync()
        {
            var session = Session();
            using (var handler = new ResponseHandler(async request =>
            {
                if (request.RequestUri.AbsolutePath.EndsWith("qq/sessions", StringComparison.Ordinal))
                {
                    var body = JObject.Parse(await request.Content.ReadAsStringAsync());
                    Assert((string)body["gameName"] == "注册玩家" && (string)body["qqSessionCookie"] == "TEST_COOKIE", "registration body lost name or QQ proof");
                    return Json(session.WithGameName("注册玩家"));
                }
                Assert(request.RequestUri.AbsolutePath == "/api/v1/auth/game-name" && request.Headers.Authorization.Parameter == session.SessionToken,
                    "name request is not authenticated or has a wrong route");
                if (request.Method == HttpMethod.Put)
                    Assert((string)JObject.Parse(await request.Content.ReadAsStringAsync())["gameName"] == "玩家", "name update body differs");
                return Json(new { gameName = "玩家" });
            }))
            using (var api = new AccountApi("https://auth.example.invalid/", new HttpClient(handler)))
            {
                Assert((await api.CompleteRegistrationAsync(Machine, "TEST_COOKIE", "注册玩家", CancellationToken.None)).GameName == "注册玩家", "registration response lost name");
                Assert((await api.GameNameAsync(session, CancellationToken.None)).GameName == "玩家", "name query lost name");
                Assert((await api.SetGameNameAsync(session, "玩家", CancellationToken.None)).GameName == "玩家", "name initialization lost name");
            }
            Assert(JsonConvert.DeserializeObject<AccountSession>(JsonConvert.SerializeObject(session)).GameName == session.GameName,
                "session serialization lost game name");
        }

        private static void GameNameLaunchWorkflow()
        {
            string directory = Path.Combine(Path.GetTempPath(), "t7r-game-name-" + Guid.NewGuid().ToString("N"));
            var previousContext = SynchronizationContext.Current;
            SynchronizationContext.SetSynchronizationContext(new System.Windows.Threading.DispatcherSynchronizationContext());
            try
            {
                var api = new FakeApi(); var time = new ManualDelay();
                var account = Model(api, new MemoryStore(), time);
                var bridge = new FakeLauncherBridge(); var settings = new SettingsService(directory);
                using (var model = new MainWindowViewModel(bridge,
                settings,
                new UserSettings { ClientDirectory = @"C:\Games\Sample" },
                null,
                new FakeClientEnvironment(path => Task.FromResult(ValidDirectory(path))),
                new FakeDesktopInteraction(),
                account: account))
                {
                    RunTask(model.ValidationTask);
                    Assert(model.PlayerName == "新玩家" && model.CanEditPlayerName, "guest default or editing changed");
                    model.PlayerName = "游客";
                    RunTask(account.LoginAsync());
                    Assert(model.PlayerName == api.Session.GameName && model.SavedPlayerName == api.Session.GameName && !model.CanEditPlayerName,
                        "login did not synchronize and lock the player name");
                    model.PlayerName = "不应生效";
                    Assert(model.PlayerName == api.Session.GameName, "view model allowed a signed-in name edit");
                    RunTask(model.StartCommand.ExecuteAsync(null));
                    Assert(bridge.StartedName == api.Session.GameName, "native launch did not receive account name");
                    api.Session = api.Session.WithGameName("下一局名称");
                    RunTask(time.TickAsync(30)); RunTask(Until(() => model.PlayerName == "下一局名称"));
                    Assert(bridge.StartedName != model.PlayerName && settings.Load().PlayerName == "下一局名称", "running game name changed or synchronized name was not saved");
                    RunTask(account.LogoutAsync());
                    Assert(model.PlayerName == "下一局名称" && !model.CanEditPlayerName, "logout lost name or unlocked a running game");
                    RunTask(model.StopCommand.ExecuteAsync(null));
                    Assert(model.CanEditPlayerName, "guest name remained locked after game stopped");
                    model.PlayerName = "游客改名";
                    Assert(settings.Load().PlayerName == "游客改名", "guest edit was not saved");
                }
            }
            finally { SynchronizationContext.SetSynchronizationContext(previousContext); if (Directory.Exists(directory)) Directory.Delete(directory, true); }
        }

        private static void RenderGameNameSetup(string output)
        {
            using (var model = new GameNameSetupViewModel((name, token) => Task.FromResult(Session().WithGameName(name)), CancellationToken.None))
            {
                var window = new GameNameSetupDialog(model);
                try
                {
                    window.Show(); Pump();
                    Assert(((TextBox)window.FindName("NameInput")).IsKeyboardFocused, "name setup did not focus its input");
                    model.GameName = "😀"; RunTask(model.SubmitCommand.ExecuteAsync(null));
                    Assert(model.Error.Length != 0 && model.Result == null, "invalid registration name was accepted");
                    if (!string.IsNullOrEmpty(output))
                    {
                        Directory.CreateDirectory(output);
                        var bitmap = new System.Windows.Media.Imaging.RenderTargetBitmap((int)window.ActualWidth, (int)window.ActualHeight, 96, 96, System.Windows.Media.PixelFormats.Pbgra32);
                        bitmap.Render(window);
                        var encoder = new System.Windows.Media.Imaging.PngBitmapEncoder();
                        encoder.Frames.Add(System.Windows.Media.Imaging.BitmapFrame.Create(bitmap));
                        using (var stream = File.Create(Path.Combine(output, "game-name-setup.png"))) encoder.Save(stream);
                    }
                }
                finally { window.Close(); }
            }
        }
    }
}
