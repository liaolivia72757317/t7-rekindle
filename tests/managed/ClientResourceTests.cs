using System;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using T7R.Core;
using T7R.Desktop.Services;
using T7R.Desktop.ViewModels;
using static T7R.ManagedHarness.LauncherTests;

namespace T7R.ManagedHarness
{
    internal static class ClientResourceTests
    {
        internal static void Run(string directory)
        {
            var root = Path.Combine(directory, "resource-client");
            Directory.CreateDirectory(root);
            TestMetadata(root);
            TestFailurePresentation(root);
            TestVersionRefresh(root);
            TestStaleResults(root);
        }

        private static MainWindowViewModel Model(string root, FakeLauncherBridge bridge,
            Func<string, Task<ClientVersionResult>> readVersion = null) => new MainWindowViewModel(bridge,
                new SettingsService(Path.Combine(root, "settings")),
                new UserSettings { ClientDirectory = root, PlayerName = "玩家" },
                null,
                new FakeClientEnvironment(path => Task.FromResult(ValidDirectory(path))) { Version = readVersion ?? (path => Task.FromResult(ClientVersionService.Read(path))) },
                new FakeDesktopInteraction());

        private static void TestMetadata(string root)
        {
            var manifest = Path.Combine(root, "update.lst");
            Assert(ClientVersionService.Read(root).Version == "", "missing version must be unknown");
            Directory.CreateDirectory(Path.Combine(root, "TCLS"));
            File.WriteAllText(Path.Combine(root, "TCLS/version.ini"), "version=12345");
            Assert(ClientVersionService.Read(root).Version == "", "updater version leaked into client version");
            foreach (var bom in new[] { false, true })
            {
                File.WriteAllText(manifest, "v1.2.3.4Beta\r\nvfs/MapPatch.vfs\r\n", new UTF8Encoding(bom));
                Assert(ClientVersionService.Read(root).Version == "v1.2.3.4Beta", "version line/BOM parsing failed");
            }
            foreach (var value in new[] { "", "vfs/MapPatch.vfs", "v1.2\0", "v1.2\tBeta", new string('1', 257) })
            {
                File.WriteAllText(manifest, value, new UTF8Encoding(false));
                var result = ClientVersionService.Read(root);
                Assert(result.Version == "" && result.Diagnostic.Length != 0, "invalid version accepted");
            }
            File.WriteAllBytes(manifest, new byte[] { 0xFF, 0xFE });
            Assert(ClientVersionService.Read(root).Version == "", "malformed UTF-8 accepted");
            using (File.Open(manifest, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
                Assert(ClientVersionService.Read(root).Diagnostic.Contains("IOException"), "locked version file was not diagnosed");
            File.Delete(manifest);
        }

        private static void TestFailurePresentation(string root)
        {
            foreach (var code in new[] { "unsupported_overlay", "directory_invalid", "entry_missing", "entry_decode", "content_mismatch" })
            {
                var detail = "ResourceError: [T7R_RESOURCE:" + code + "] archive='map.vfs' entry='data/fixture': 覆盖布局尚未支持\ntraceback detail";
                var bridge = new FakeLauncherBridge { PendingCheck = new TaskCompletionSource<OperationSnapshot>() };
                using (var model = Model(root, bridge))
                {
                    RunTask(model.ValidationTask);
                    Assert(model.ClientVersionText == "客户端版本：未识别" && model.CanStart, "unknown metadata blocked startup");
                    var pending = model.StartCommand.ExecuteAsync(null);
                    bridge.Snapshot = new SessionSnapshot { State = SessionState.Failed, CleanupComplete = true };
                    bridge.PendingCheck.SetResult(new OperationSnapshot { Status = OperationStatus.Failed, Error = detail });
                    RunTask(pending);
                    Assert(model.DiagnosticText == "资源不匹配" && model.HomeStatusDetail == "资源不匹配"
                        && model.ResourceCheckText == "资源不匹配", "resource details leaked to ordinary UI");
                    Assert(!model.LogsExpanded && bridge.StartCount == 0, "resource failure expanded logs or launched client");
                    Assert(model.NativeLogText.Contains(detail), "detailed resource error was lost from logs");
                    model.BeginDirectoryDraft();
                    Assert(model.ResourceCheckText == "未检查" && !model.HomeStatusDetail.Contains("资源不匹配"),
                        "directory draft retained previous resource error");
                }
            }
            var startBridge = new FakeLauncherBridge { Failure = "[T7R_RESOURCE:entry_decode] changed after check" };
            using (var model = Model(root, startBridge))
            {
                RunTask(model.ValidationTask);
                RunTask(model.StartCommand.ExecuteAsync(null));
                Assert(model.DiagnosticText == "资源不匹配" && model.ResourceCheckText == "资源不匹配" && !model.LogsExpanded,
                    "start-time resource failure escaped generic presentation");
            }
        }

        private static void TestVersionRefresh(string root)
        {
            var manifest = Path.Combine(root, "update.lst");
            File.WriteAllText(manifest, "v1.2.3.4Beta\n");
            var bridge = new FakeLauncherBridge();
            using (var model = Model(root, bridge))
            {
                RunTask(model.ValidationTask);
                Assert(model.ClientVersionText == "客户端版本：v1.2.3.4Beta" && model.ResourceCheckText == "未检查",
                    "directory inspection claimed resource compatibility");
                File.WriteAllText(manifest, "v1.2.3.5\n");
                RunTask(model.CheckCommand.ExecuteAsync(null));
                Assert(model.ClientVersionText == "客户端版本：v1.2.3.5" && model.ResourceCheckText == "检查通过",
                    "recheck did not refresh version/status");
                File.Delete(manifest);
                RunTask(model.CheckCommand.ExecuteAsync(null));
                Assert(model.ClientVersionText == "客户端版本：未识别" && model.ResourceCheckText == "检查通过",
                    "missing metadata overrode resource compatibility");
                bridge.PendingCheck = new TaskCompletionSource<OperationSnapshot>();
                var check = model.CheckCommand.ExecuteAsync(null);
                Assert(model.ResourceCheckText == "检查中", "check progress missing");
                bridge.Snapshot = new SessionSnapshot { State = SessionState.Idle, CleanupComplete = true };
                bridge.PendingCheck.SetResult(new OperationSnapshot { Status = OperationStatus.Cancelled });
                RunTask(check);
                Assert(model.ResourceCheckText == "未检查", "cancelled check retained success");
            }
            using (var reopened = Model(root, new FakeLauncherBridge()))
            {
                RunTask(reopened.ValidationTask);
                Assert(reopened.ResourceCheckText == "未检查", "resource success was persisted");
            }
        }

        private static void TestStaleResults(string root)
        {
            var oldVersion = new TaskCompletionSource<ClientVersionResult>();
            var nextRoot = Path.Combine(root, "next");
            using (var model = Model(root, new FakeLauncherBridge(), path => path == root ? oldVersion.Task
                : Task.FromResult(new ClientVersionResult("v2.0"))))
            {
                var initial = model.ValidationTask;
                model.ClientDirectory = nextRoot;
                RunTask(model.ValidationTask);
                oldVersion.SetResult(new ClientVersionResult("v1.0"));
                RunTask(initial);
                Assert(model.ClientVersionText == "客户端版本：v2.0", "stale metadata replaced current version");
            }
            var bridge = new FakeLauncherBridge { PendingCheck = new TaskCompletionSource<OperationSnapshot>() };
            using (var model = Model(root, bridge))
            {
                RunTask(model.ValidationTask);
                var check = model.CheckCommand.ExecuteAsync(null);
                model.ClientDirectory = nextRoot;
                RunTask(model.ValidationTask);
                bridge.Snapshot = new SessionSnapshot { State = SessionState.Failed, CleanupComplete = true };
                bridge.PendingCheck.SetResult(new OperationSnapshot { Status = OperationStatus.Failed,
                    Error = "[T7R_RESOURCE:entry_missing] old directory" });
                RunTask(check);
                Assert(model.ClientDirectory == nextRoot && model.ResourceCheckText == "未检查"
                    && model.DiagnosticText != "资源不匹配", "stale check replaced current directory state");
            }
        }
    }
}
