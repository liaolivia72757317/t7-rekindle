using System;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop.Services;

namespace T7.ManagedHarness
{
    internal static class NativeBridgeServiceTests
    {
        internal static void Run()
        {
            CheckCancellationAsync().GetAwaiter().GetResult();
            CheckLogGrowth();
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
