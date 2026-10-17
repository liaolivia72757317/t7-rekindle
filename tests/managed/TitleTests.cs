using System;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using Newtonsoft.Json;
using T7R.Desktop.Services;
using T7R.Desktop.Views;
using T7R.Desktop.ViewModels;
using T7R.Core;
using static T7R.ManagedHarness.LauncherTests;

namespace T7R.ManagedHarness
{
    internal static partial class AccountTests
    {
        internal static void RunTitles() { NativeTitlePublicationTests.Run(); RunTitlesAsync().GetAwaiter().GetResult(); TitleBridgeRoundTrip(); }
        private static AccountTitles TitleData(int? equipped = 1008) => new AccountTitles(new[] {
            new AccountTitle(1007, "首测元老", "感谢最早参与测试的玩家", "获取：管理员授予；永久。", 0, true, true, "ADMIN", DateTimeOffset.UtcNow, null),
            new AccountTitle(1008, "内测精英", "感谢参与本次测试的玩家", "获取：2026年10月功能上线后登录；永久。", 1, true, true, "OCTOBER_2026_LOGIN", DateTimeOffset.UtcNow, null)
        }, equipped);

        private static async Task RunTitlesAsync()
        {
            var api = new FakeApi { TitleData = TitleData() }; var time = new ManualDelay();
            using (var model = Model(api, new MemoryStore(), time))
            {
                Assert(model.Titles.Count == 0 && !model.CanChangeTitle, "offline title controls were enabled");
                await model.LoginAsync();
                Assert(model.Titles.Count == 2 && model.EquippedTitle == "内测精英", "login did not refresh titles");
                var pending = new TaskCompletionSource<AccountTitles>(); api.PendingTitleEquip = pending.Task;
                var change = model.EquipTitleAsync(null);
                Assert(!model.CanChangeTitle && model.EquippedTitle == "内测精英", "title changed before server confirmation");
                pending.SetResult(TitleData(null));
                Assert(await change && model.EquippedTitle == "未佩戴称号", "server-confirmed unequip was lost");
                api.PendingTitleEquip = null;
                api.TitleError = new AccountApiException(409, "TITLE_NOT_AVAILABLE");
                Assert(!await model.EquipTitleAsync(1007) && model.EquippedTitle == "未佩戴称号", "rejected title changed selection");
                api.TitleError = null;
                api.HeartbeatError = new IOException("TEST_OFFLINE");
                await time.TickAsync(15); await Until(() => !model.IsOnline);
                Assert(model.Titles.Count == 0 && model.TitleSnapshot.EquippedTitleId == null, "offline titles leaked into the game snapshot");
                api.HeartbeatError = null; api.TitleData = TitleData(1007);
                await time.TickAsync(15); await Until(() => model.EquippedTitle == "首测元老");
                Assert(api.TitleReads >= 2, "heartbeat did not refresh changed titles");
                api.TitleError = new AccountApiException(409, "ONLINE_TAKEN_OVER");
                Assert(!await model.EquipTitleAsync(null) && !model.IsOnline && model.Titles.Count == 0,
                    "taken-over title write retained online privileges");
            }
            await LateTitleResponseAsync();
            await TitleHttpContractAsync();
            Assert(Marshal.SizeOf(typeof(NativeTitleRequest)) == 32 && Marshal.OffsetOf(typeof(NativeTitleRequest), "Epoch").ToInt32() == 16,
                "managed title request ABI differs from native");
            var text = JsonConvert.SerializeObject(new { titles = TitleData().Titles.Select(t => new {
                titleId = t.TitleId, name = t.Name, description = t.Description, rule = t.Rule }).ToArray() });
            var bytes = NativeBridgeService.TitleCatalogue(text);
            Assert(bytes.Length == 8 + 2 * 164 && BitConverter.ToUInt32(bytes, 0) == 1
                && BitConverter.ToUInt32(bytes, 4) == 2 && BitConverter.ToInt32(bytes, 8) == 1007
                && Encoding.GetEncoding(936).GetString(bytes, 8 + 164 + 36, 128).Contains("2026年10月"), "title catalogue is not versioned GBK/null padded");
            Assert(NativeBridgeService.TitleCatalogue("{\"titles\":[]}").Length == 0, "offline catalogue rewrote original resources");
            CustomTitleCatalogue();
            bool invalid = false;
            try { new AccountTitles(TitleData().Titles, 999); } catch (InvalidDataException) { invalid = true; }
            Assert(invalid, "response equipped an unowned title");
        }

        private static void CustomTitleCatalogue()
        {
            Func<int, string, string, object> item = (id, name, description) => new {
                titleId = id, name, description, rule = "获取：管理员授予；永久。" };
            Func<object[], byte[]> encode = items => NativeBridgeService.TitleCatalogue(JsonConvert.SerializeObject(new { titles = items }));
            var bytes = encode(new[] { item(1000000, "重燃共建者", "共同续写战场") });
            Assert(bytes.Length == 172 && BitConverter.ToInt32(bytes, 8) == 1000000
                && Encoding.GetEncoding(936).GetString(bytes, 12, 32).TrimEnd('\0') == "重燃共建者"
                && bytes[43] == 0 && bytes[171] == 0, "custom title metadata was lost");
            foreach (var items in new[] {
                new[] { item(999, "称号", "") }, new[] { item(1000000, new string('长', 16), "") },
                new[] { item(1000000, "称号\0隐藏", "") }, new[] { item(1000000, "😀", "") },
                new[] { item(1000000, "称号", new string('长', 64)) },
                new[] { item(1000000, "称号", ""), item(1000000, "重复", "") },
                Enumerable.Range(0, 65).Select(i => item(1000000 + i, "称号", "")).ToArray() })
            {
                bool rejected = false;
                try { encode(items); } catch (InvalidDataException) { rejected = true; }
                Assert(rejected, "invalid custom title catalogue accepted");
            }
            Assert(encode(Enumerable.Range(0, 64).Select(i => item(1000000 + i, "称号", "")).ToArray()).Length == 10504,
                "maximum title catalogue rejected");
        }

        private static async Task LateTitleResponseAsync()
        {
            var pending = new TaskCompletionSource<AccountTitles>();
            var api = new FakeApi { PendingTitles = pending.Task };
            using (var model = Model(api, new MemoryStore(), new ManualDelay()))
            {
                await model.LoginAsync();
                Assert(api.TitleReads == 1 && model.Titles.Count == 0, "pending title fetch mutated the catalogue");
                await model.LogoutAsync();
                pending.SetResult(TitleData());
                await Until(() => !model.CanChangeTitle);
                Assert(model.Titles.Count == 0 && model.TitleSnapshot.EquippedTitleId == null, "late response restored a logged-out title");
            }
        }

        private static async Task TitleHttpContractAsync()
        {
            var session = Session(); int reads = 0, writes = 0;
            using (var handler = new ResponseHandler(async request => {
                Assert(request.Headers.Authorization?.Parameter == session.SessionToken
                    && request.Headers.GetValues("X-Machine-Id").Single() == Machine, "title request omitted machine/session credentials");
                if (request.Method == HttpMethod.Get) {
                    reads++; Assert(request.RequestUri.AbsolutePath.EndsWith("/online/connection/titles"), "title GET route changed");
                    return Json(TitleData());
                }
                writes++; Assert(request.Method == HttpMethod.Put && request.RequestUri.AbsolutePath.EndsWith("/titles/equipped"), "title equip route changed");
                Assert(await request.Content.ReadAsStringAsync() == "{\"titleId\":null}", "unequip is not explicit JSON null");
                return Json(TitleData(null));
            }))
            using (var api = new AccountApi("https://auth.example.invalid/", new HttpClient(handler)))
            {
                Assert((await api.TitlesAsync(session, "connection", CancellationToken.None)).EquippedTitleId == 1008, "title JSON decoding failed");
                Assert((await api.EquipTitleAsync(session, "connection", null, CancellationToken.None)).EquippedTitleId == null, "unequip JSON decoding failed");
            }
            Assert(reads == 1 && writes == 1, "title HTTP operations were duplicated");
        }

        internal static void RenderTitles(string output)
        {
            var api = new FakeApi { TitleData = TitleData() };
            using (var model = Model(api, new MemoryStore(), new ManualDelay()))
            {
                RunTask(model.LoginAsync());
                var dialog = new TitlesDialog(model);
                var root = (FrameworkElement)dialog.Content;
                foreach (var scale in new[] { 1.0, 1.5, 2.0 }) {
                    LauncherLayoutTests.Render(root, null, output, "titles-" + (int)(scale * 100), 504, 561, scale);
                    LauncherLayoutTests.AssertWithin((Button)dialog.FindName("UnequipButton"), root, 504, 561);
                    LauncherLayoutTests.Render(root, null, output, "titles-compact-" + (int)(scale * 100), 424, 281, scale);
                    LauncherLayoutTests.AssertWithin((Button)dialog.FindName("CloseButton"), root, 424, 281);
                }
                Assert(((ItemsControl)dialog.FindName("TitleItems")).Items.Count == 2, "dialog did not display original titles");
                RunTask(model.LogoutAsync()); Pump();
                Assert(((ItemsControl)dialog.FindName("TitleItems")).Items.Count == 0, "dialog kept titles after logout");
            }
        }

        private static void TitleBridgeRoundTrip()
        {
            var directory = Path.Combine(Path.GetTempPath(), "T7R-title-tests-" + Guid.NewGuid().ToString("N"));
            var api = new FakeApi { TitleData = TitleData() };
            var account = Model(api, new MemoryStore(), new ManualDelay());
            var bridge = new FakeLauncherBridge { Snapshot = new SessionSnapshot { State = SessionState.Running } };
            try {
                using (var model = new MainWindowViewModel(bridge,
                new SettingsService(directory),
                new UserSettings(),
                null,
                new FakeClientEnvironment(path => Task.FromResult(ClientDirectoryService.Inspect(path))) { Devices = () => Array.Empty<OutputDeviceOption>() },
                new FakeDesktopInteraction(),
                account: account)) {
                    var poll = typeof(MainWindowViewModel).GetMethod("PollTitles", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic);
                    Action tick = () => { model.Refresh(); poll.Invoke(model, null); };
                    RunTask(account.LoginAsync()); tick();
                    Assert(bridge.TitleJson.Contains("1008") && bridge.TitleEpoch != 0, "launcher did not publish central title snapshot");
                    bridge.TitleRequest = new NativeTitleRequest { RequestId = 10, Epoch = bridge.TitleEpoch, TitleId = 0 };
                    tick();
                    Assert(bridge.CompletedTitleRequest == 10 && bridge.TitleSuccess && account.TitleSnapshot.EquippedTitleId == null,
                        "game request did not round trip through the authenticated account API");
                    var pending = new TaskCompletionSource<AccountTitles>(); api.PendingTitleEquip = pending.Task;
                    bridge.TitleRequest = new NativeTitleRequest { RequestId = 11, Epoch = bridge.TitleEpoch, TitleId = 1007 };
                    tick();
                    var previousEpoch = bridge.TitleEpoch;
                    RunTask(account.LogoutAsync()); tick();
                    Assert(bridge.TitleEpoch > previousEpoch && bridge.TitleJson.Contains("\"titles\":[]"), "logout did not clear game title visibility");
                    pending.SetResult(TitleData(1007)); Pump(); tick();
                    Assert(bridge.CompletedTitleRequest == 0 && bridge.TitleJson.Contains("\"titles\":[]"), "old account completion restored a game title");
                }
            } finally {
                account.Dispose();
                if (Directory.Exists(directory)) Directory.Delete(directory, true);
            }
        }

        private sealed partial class FakeApi : IAccountTitlesApi
        {
            public AccountTitles TitleData = AccountTitles.Empty;
            public Task<AccountTitles> PendingTitles, PendingTitleEquip;
            public Exception TitleError;
            public int TitleReads;
            public Task<AccountTitles> TitlesAsync(AccountSession session, string connection, CancellationToken cancellation) {
                TitleReads++; return TitleError == null ? PendingTitles ?? Task.FromResult(TitleData) : Task.FromException<AccountTitles>(TitleError);
            }
            public Task<AccountTitles> EquipTitleAsync(AccountSession session, string connection, int? titleId, CancellationToken cancellation) =>
                TitleError == null ? PendingTitleEquip ?? Task.FromResult(new AccountTitles(TitleData.Titles, titleId)) : Task.FromException<AccountTitles>(TitleError);
        }
    }

    internal sealed partial class FakeLauncherBridge : INativeTitles
    {
        public ulong TitleEpoch, CompletedTitleRequest;
        public string TitleJson;
        public bool TitleSuccess;
        public NativeTitleRequest TitleRequest;
        public void PublishTitles(ulong epoch, string json, ulong requestId = 0, bool success = true) {
            TitleEpoch = epoch; TitleJson = json; CompletedTitleRequest = requestId; TitleSuccess = success;
        }
        public NativeTitleRequest ReadTitleRequest() => TitleRequest;
    }
}
