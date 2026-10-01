using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop.Services;

namespace T7.ManagedHarness
{
    internal sealed class FakeLauncherBridge : INativeBridge
    {
        public SessionSnapshot Snapshot { get; set; } = new SessionSnapshot { State = SessionState.Idle, CleanupComplete = true };
        public bool HoldStart { get; set; }
        public string Failure { get; set; }
        public bool Cancelled { get; private set; }
        public string StartedName { get; private set; }
        public string StartedDirectory { get; private set; }
        public int StartCount { get; private set; }
        public int CheckCount { get; private set; }
        public TaskCompletionSource<OperationSnapshot> PendingCheck { get; set; }
        public TaskCompletionSource<OperationSnapshot> PendingStart { get; set; }
        public Task<OperationSnapshot> CheckAsync(string directory, CancellationToken token)
        {
            CheckCount++;
            if (PendingCheck != null)
            {
                Snapshot = new SessionSnapshot { State = SessionState.Checking };
                return PendingCheck.Task;
            }
            Snapshot = new SessionSnapshot { State = SessionState.Idle, CleanupComplete = true };
            return Task.FromResult(new OperationSnapshot { Status = OperationStatus.Succeeded });
        }
        public Task<OperationSnapshot> StartAsync(string directory, CancellationToken token) => StartAsync(directory, PlayerNameRules.DefaultName, token);
        public Task<OperationSnapshot> StartAsync(string directory, string name, CancellationToken token)
        {
            StartCount++;
            StartedDirectory = directory;
            StartedName = name;
            if (PendingStart != null)
            {
                Snapshot = new SessionSnapshot { State = SessionState.StartingRuntime };
                return PendingStart.Task;
            }
            if (Failure != null)
            {
                Snapshot = new SessionSnapshot { State = SessionState.Failed, ErrorCode = 1000, CleanupComplete = true };
                return Task.FromResult(new OperationSnapshot { Status = OperationStatus.Failed, Error = Failure });
            }
            if (HoldStart)
            {
                Snapshot = new SessionSnapshot { State = SessionState.StartingRuntime };
                var pending = new TaskCompletionSource<OperationSnapshot>();
                token.Register(() =>
                {
                    Cancelled = true;
                    Snapshot = new SessionSnapshot { State = SessionState.Idle, CleanupComplete = true };
                    pending.TrySetCanceled();
                });
                return pending.Task;
            }
            Snapshot = new SessionSnapshot { State = SessionState.Running };
            return Task.FromResult(new OperationSnapshot { Status = OperationStatus.Succeeded });
        }
        public Task<OperationSnapshot> StopAsync(CancellationToken token)
        {
            Snapshot = new SessionSnapshot { State = SessionState.Idle, CleanupComplete = true };
            return Task.FromResult(new OperationSnapshot { Status = OperationStatus.Succeeded });
        }
        public SessionSnapshot GetSnapshot() => Snapshot;
        public LogReadResult ReadLogRecords(ref ulong cursor) => new LogReadResult { Records = new List<LogRecord>() };
    }

    internal sealed class FakeDesktopInteraction : IDesktopInteraction
    {
        public bool ConfirmResult { get; set; } = true;
        public int ConfirmCount { get; private set; }
        public string Text { get; private set; }
        public bool IsMarkdown { get; private set; }
        public string DirectorySelection { get; set; }
        public Exception DirectoryError { get; set; }
        public LauncherUpdateInfo Update { get; private set; }
        public Action OnShowUpdate { get; set; }
        public string SelectDirectory(string initialDirectory)
        {
            if (DirectoryError != null) throw DirectoryError;
            return DirectorySelection;
        }
        public bool Confirm(string message) { ConfirmCount++; Text = message; return ConfirmResult; }
        public void ShowText(string title, string text) { Text = text; IsMarkdown = false; }
        public void ShowMarkdown(string title, string markdown) { Text = markdown; IsMarkdown = true; }
        public void CopyText(string text) { Text = text; }
        public void OpenDirectory(string path) { Text = path; }
        public void ShowUpdate(LauncherUpdateInfo info) { Update = info; OnShowUpdate?.Invoke(); }
    }
}
