using System;
using System.ComponentModel;
using System.IO;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using T7R.Battle;
using T7R.Core;
using T7R.Desktop.Services;

namespace T7R.ManagedHarness
{
    internal static class NativeBridgeServiceTests
    {
        internal static void Run()
        {
            NativeBridgeService.ValidateGatewayStartLayout();
            NativeBridgeService.ValidateBattleAttachmentLayout();
            CheckCancellationAsync().GetAwaiter().GetResult();
            CheckLogGrowth();
        }

        internal static void RunNativeContract(string packageRoot)
        {
            NativeBridgeService.ValidateGatewayStartLayout();
            NativeBridgeService.ValidateBattleAttachmentLayout();
            packageRoot = Path.GetFullPath(packageRoot);
            var legacy = Path.Combine(Path.GetTempPath(), "T7R-native-contract-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(legacy);
            try
            {
                foreach (var path in Directory.EnumerateFiles(packageRoot, "*.dll"))
                {
                    var name = Path.GetFileName(path);
                    if (name == NativeBridgeContract.LibraryName) name = "T7.NativeBridge.dll";
                    File.Copy(path, Path.Combine(legacy, name));
                }
                try
                {
                    var unexpected = new NativeBridgeService(legacy);
                    unexpected.Dispose();
                    throw new InvalidOperationException("old DLL name was accepted without the T7R library");
                }
                catch (Win32Exception error) when (error.NativeErrorCode == 126) { }
            }
            finally { Directory.Delete(legacy, true); }

            var bridge = new NativeBridgeService(packageRoot);
            try
            {
                using (var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10)))
                {
                    if (bridge.GetSnapshot().State != SessionState.Idle)
                        throw new InvalidOperationException("native session did not start idle");
                    bridge.SetLogLevel(DiagnosticLogLevel.Info);
                    bridge.SetGuardDebug(true); bridge.SetGuardDebug(false);
                    bridge.SetOutputDevice(Guid.Empty);
                    bridge.GetGraphicsSettings();
                    bridge.GetAudioSettings();
                    ulong cursor = 0;
                    bridge.ReadLogRecords(ref cursor);
                    var operation = bridge.StopAsync(timeout.Token).GetAwaiter().GetResult();
                    if (operation.Status != OperationStatus.Succeeded || !bridge.GetSnapshot().CleanupComplete)
                        throw new InvalidOperationException("native session cleanup did not complete");
                    var failedStart = bridge.StartWithGatewayAsync(
                        Path.Combine(packageRoot, "missing-client"), "Player", false,
                        21001, 21002, 21003, timeout.Token).GetAwaiter().GetResult();
                    var failedSnapshot = bridge.GetSnapshot();
                    if (failedStart.Status != OperationStatus.Failed || failedSnapshot.State != SessionState.Failed
                        || failedSnapshot.LoginPort != 0 || failedSnapshot.LogicPort != 0
                        || failedSnapshot.InstancePort != 0)
                        throw new InvalidOperationException("gateway start failure did not release native endpoints");
                    var movement = new[] { new NativeCombatProfile
                    {
                        HeroId = 1101, WeaponId = 701, ForwardSlow = 2.5f,
                        LateralSlow = 1.8f, BackwardSlow = 1f, ForwardSprint = 3.6f,
                        DashDurationMs = 1333, DashMoveMs = 967, DashSpeedMilli = 2585
                    } };
                    var profiledStart = bridge.StartWithGatewayProfilesAsync(
                        Path.Combine(packageRoot, "missing-client"), "Player", false,
                        21001, 21002, 21003, movement, timeout.Token).GetAwaiter().GetResult();
                    if (profiledStart.Status != OperationStatus.Failed
                        || bridge.GetSnapshot().State != SessionState.Failed)
                        throw new InvalidOperationException("profiled gateway start did not reach native client preflight");
                }
            }
            finally { bridge.Dispose(); }
        }

        private static async Task CheckCancellationAsync()
        {
            foreach (var cancelStatus in new[] { NativeStatus.Ok, NativeStatus.NotReady })
            {
                using (var cancellation = new CancellationTokenSource())
                {
                    var completed = false;
                    var pending = NativeBridgeService.WaitForOperationAsync(1,
                        _ => new OperationSnapshot { Status = completed ? OperationStatus.Succeeded : OperationStatus.Running },
                        _ => { completed = true; return cancelStatus; }, cancellation.Token);
                    cancellation.Cancel();
                    if ((await pending).Status != OperationStatus.Succeeded)
                        throw new InvalidOperationException("late cancellation hid a successful start");
                }
            }

            foreach (var errorCode in new uint[] { 1001, 1002 })
            {
                using (var cancellation = new CancellationTokenSource())
                {
                    var cancelAccepted = new TaskCompletionSource<bool>();
                    var finished = false;
                    var pending = NativeBridgeService.WaitForOperationAsync(1,
                        _ => new OperationSnapshot
                        {
                            Status = finished ? OperationStatus.Cancelled : OperationStatus.Running,
                            ErrorCode = finished ? errorCode : 0
                        },
                        _ => { cancelAccepted.SetResult(true); return NativeStatus.Ok; }, cancellation.Token);
                    cancellation.Cancel();
                    await cancelAccepted.Task;
                    if (pending.IsCompleted) throw new InvalidOperationException("cancellation did not wait for cleanup");
                    finished = true;
                    try
                    {
                        var result = await pending;
                        if (errorCode != 1002 || result.ErrorCode != 1002)
                            throw new InvalidOperationException("cancelled operation did not report cancellation");
                    }
                    catch (OperationCanceledException) when (errorCode == 1001) { }
                }
            }
        }

        private static void CheckLogGrowth()
        {
            ulong cursor = 7;
            var attempts = 0;
            NativeBridgeService.LogReader read = (ref ulong position, byte[] buffer, uint capacity,
                out uint required, out uint flags) =>
            {
                if (position != 7) throw new InvalidOperationException("retry consumed the log cursor");
                flags = 1;
                if (++attempts < 4)
                {
                    required = capacity + 100;
                    return NativeStatus.BufferTooSmall;
                }
                var text = Encoding.UTF8.GetBytes("7\tlog\n");
                text.CopyTo(buffer, 0);
                required = (uint)text.Length;
                position = 8;
                return NativeStatus.Ok;
            };
            bool gap;
            var result = NativeBridgeService.ReadLogsWithRetry(read, ref cursor, out gap);
            if (attempts != 4 || cursor != 8 || !gap || result != "7\tlog\n")
                throw new InvalidOperationException("growing log buffer contract failed");

            foreach (var requested in new uint[] { 4096, 16 * 1024 * 1024 + 1 })
            {
                read = (ref ulong position, byte[] buffer, uint capacity, out uint required, out uint flags) =>
                {
                    required = requested;
                    flags = 0;
                    return NativeStatus.BufferTooSmall;
                };
                try
                {
                    NativeBridgeService.ReadLogsWithRetry(read, ref cursor, out gap);
                    throw new Exception("invalid log capacity was accepted");
                }
                catch (InvalidOperationException) { }
                if (cursor != 8) throw new InvalidOperationException("failed read consumed cursor");
            }
        }
    }
}
