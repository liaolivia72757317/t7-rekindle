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
        public Guid OutputDevice { get; private set; }
        public int OutputDeviceSetCount { get; private set; }
        public void SetOutputDevice(Guid identifier) { OutputDevice = identifier; OutputDeviceSetCount++; }
        public SessionSnapshot Snapshot { get; set; } = new SessionSnapshot { State = SessionState.Idle, CleanupComplete = true };
        public bool HoldStart { get; set; }
        public string Failure { get; set; }
        public bool Cancelled { get; private set; }
        public string StartedName { get; private set; }
        public string StartedDirectory { get; private set; }
        public bool StartedSkipStartupAnimation { get; private set; }
        public int StartCount { get; private set; }
        public int CheckCount { get; private set; }
        public TaskCompletionSource<OperationSnapshot> PendingCheck { get; set; }
        public TaskCompletionSource<OperationSnapshot> PendingStart { get; set; }
        public IReadOnlyList<string> LogLines { get; set; } = Array.Empty<string>();
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
            => StartAsync(directory, name, false, token);
        public Task<OperationSnapshot> StartAsync(string directory, string name, bool skipStartupAnimation, CancellationToken token)
        {
            StartCount++;
            StartedDirectory = directory;
            StartedName = name;
            StartedSkipStartupAnimation = skipStartupAnimation;
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
        public GraphicsSettingsSnapshot Graphics { get; set; } = new GraphicsSettingsSnapshot(0, GraphicsSyncState.Unavailable, null);
        public int GraphicsApplyCount { get; private set; }
        public GraphicsSettingsValues AppliedGraphics { get; private set; }
        public ulong AppliedGraphicsRevision { get; private set; }
        public Exception GraphicsError { get; set; }
        public GraphicsSettingsSnapshot GetGraphicsSettings() => Graphics;
        public void ApplyGraphicsSettings(ulong revision, GraphicsSettingsValues values)
        {
            if (GraphicsError != null) throw GraphicsError;
            GraphicsApplyCount++; AppliedGraphics = values; AppliedGraphicsRevision = revision;
            Graphics = new GraphicsSettingsSnapshot(revision, GraphicsSyncState.Applying, Graphics.Values);
        }
        public AudioSettingsSnapshot Audio { get; set; } = new AudioSettingsSnapshot(0, AudioSyncState.Unavailable, null);
        public int AudioApplyCount { get; private set; }
        public AudioSettingsValues AppliedAudio { get; private set; }
        public ulong AppliedAudioRevision { get; private set; }
        public Exception AudioError { get; set; }
        public AudioSettingsSnapshot GetAudioSettings() => Audio;
        public void ApplyAudioSettings(ulong revision, AudioSettingsValues values)
        {
            if (AudioError != null) throw AudioError;
            AudioApplyCount++; AppliedAudio = values; AppliedAudioRevision = revision;
            Audio = new AudioSettingsSnapshot(revision, AudioSyncState.Applying, Audio.Values);
        }
        public LogReadResult ReadLogRecords(ref ulong cursor)
        {
            var records = new List<LogRecord>();
            while (cursor < (ulong)LogLines.Count)
            {
                var text = LogLines[(int)cursor];
                records.Add(new LogRecord { Cursor = ++cursor, Text = text });
            }
            return new LogReadResult { NextCursor = cursor, Records = records };
        }
    }

    internal sealed class FakeDesktopInteraction : IDesktopInteraction
    {
        public bool ConfirmResult { get; set; } = true;
        public int ConfirmCount { get; private set; }
        public int EnvironmentInfoCount { get; private set; }
        public string Title { get; private set; }
        public string Text { get; private set; }
        public bool IsMarkdown { get; private set; }
        public Exception MarkdownError { get; set; }
        public string DirectorySelection { get; set; }
        public Exception DirectoryError { get; set; }
        public Exception LogDirectoryError { get; set; }
        public Exception AddressError { get; set; }
        public string SelectDirectory(string initialDirectory)
        {
            if (DirectoryError != null) throw DirectoryError;
            return DirectorySelection;
        }
        public bool Confirm(string message) { ConfirmCount++; Text = message; return ConfirmResult; }
        public void ShowText(string title, string text) { Text = text; IsMarkdown = false; }
        public void ShowMarkdown(string title, string markdown)
        {
            if (MarkdownError != null) throw MarkdownError;
            Title = title;
            Text = markdown;
            IsMarkdown = true;
        }
        public void CopyText(string text) { Text = text; }
        public void OpenDirectory(string path)
        {
            if (LogDirectoryError != null) throw LogDirectoryError;
            Text = path;
        }
        public void OpenAddress(string address)
        {
            if (AddressError != null) throw AddressError;
            Text = address;
        }
        public void ShowEnvironmentInfo() { EnvironmentInfoCount++; }
    }
}
