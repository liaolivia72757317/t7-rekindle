using System;
using System.Collections.Generic;

namespace T7.Rekindle.Core
{
    public enum SessionState : uint
    {
        Idle = 0,
        Checking = 1,
        StartingRuntime = 2,
        StartingClient = 3,
        AdaptingClient = 4,
        Running = 5,
        Cancelling = 6,
        StoppingClient = 7,
        StoppingRuntime = 8,
        FailedCleaning = 9,
        Failed = 10
    }

    public enum OperationKind : uint
    {
        None = 0,
        Check = 1,
        Start = 2,
        Stop = 3
    }

    public enum OperationStatus : uint
    {
        Unknown = 0,
        Queued = 1,
        Running = 2,
        Succeeded = 3,
        Cancelled = 4,
        Failed = 5
    }

    public enum NativeStatus : int
    {
        Ok = 0,
        InvalidArgument = 1,
        InvalidAbi = 2,
        InvalidHandle = 3,
        Busy = 4,
        NotFound = 5,
        BufferTooSmall = 6,
        Cancelled = 7,
        Failed = 8,
        NotReady = 9,
        InternalError = 10
    }

    public sealed class SessionSnapshot
    {
        public SessionState State { get; set; }
        public OperationKind Operation { get; set; }
        public ulong OperationId { get; set; }
        public ulong Revision { get; set; }
        public ushort LoginPort { get; set; }
        public ushort LogicPort { get; set; }
        public ushort InstancePort { get; set; }
        public uint ErrorCode { get; set; }
        // False while native FailedCleaning still owns a client/runtime
        // resource; callers must wait or submit a Stop retry before release.
        public bool CleanupComplete { get; set; }
        public string Phase { get; set; } = string.Empty;
    }

    public sealed class OperationSnapshot
    {
        public ulong OperationId { get; set; }
        public OperationKind Kind { get; set; }
        public OperationStatus Status { get; set; }
        public uint ErrorCode { get; set; }
        public string Error { get; set; } = string.Empty;
    }

    public sealed class NativeError
    {
        public ulong OperationId { get; set; }
        public uint ErrorCode { get; set; }
        public string Message { get; set; } = string.Empty;
    }

    public sealed class LogRecord
    {
        public ulong Cursor { get; set; }
        public string Text { get; set; } = string.Empty;
    }

    public sealed class LogReadResult
    {
        public ulong NextCursor { get; set; }
        public ulong EarliestCursor { get; set; }
        public bool Gap { get; set; }
        public IReadOnlyList<LogRecord> Records { get; set; } = Array.Empty<LogRecord>();
    }
}
