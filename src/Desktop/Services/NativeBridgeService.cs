using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using T7.Rekindle.Core;

namespace T7.Rekindle.Desktop.Services
{
    public interface INativeBridge
    {
        Task<OperationSnapshot> CheckAsync(string clientDirectory, CancellationToken cancellationToken);
        Task<OperationSnapshot> StartAsync(string clientDirectory, CancellationToken cancellationToken);
        Task<OperationSnapshot> StartAsync(string clientDirectory, string playerName, CancellationToken cancellationToken);
        Task<OperationSnapshot> StopAsync(CancellationToken cancellationToken);
        SessionSnapshot GetSnapshot();
        LogReadResult ReadLogRecords(ref ulong cursor);
    }

    public sealed class NativeBridgeService : INativeBridge
    {
        private const uint LoadLibrarySearchDllLoadDir = 0x00000100;
        private const uint LoadLibrarySearchSystem32 = 0x00000800;
        private const uint MaxNativeTextBytes = 16 * 1024 * 1024;
        private readonly string _packageRoot;
        private readonly object _sync = new object();
        private IntPtr _module;
        private NativeMethods _native;
        private NativeSessionHandle _session;
        private bool _disposed;

        public NativeBridgeService(string packageRoot)
        {
            NativeAbiLayout.Validate();
            _packageRoot = Path.GetFullPath(packageRoot ?? throw new ArgumentNullException(nameof(packageRoot)));
            var bridgePath = Path.Combine(_packageRoot, NativeBridgeContract.LibraryName);
            _module = LoadLibraryEx(bridgePath, IntPtr.Zero, LoadLibrarySearchDllLoadDir | LoadLibrarySearchSystem32);
            if (_module == IntPtr.Zero)
            {
                throw new Win32Exception(Marshal.GetLastWin32Error(), "加载 NativeBridge 失败。");
            }

            var sessionCreated = false;
            try
            {
                _native = NativeMethods.Load(_module);
                uint nativeAbi;
                uint nativeSnapshotSize;
                var abiStatus = _native.GetAbi(out nativeAbi, out nativeSnapshotSize);
                if (abiStatus != NativeStatus.Ok || nativeAbi != NativeBridgeContract.AbiVersion
                    || nativeSnapshotSize != (uint)Marshal.SizeOf(typeof(NativeSnapshot)))
                {
                    throw new InvalidOperationException("NativeBridge ABI mismatch.");
                }

                var create = new NativeCreateArgs
                {
                    AbiVersion = NativeBridgeContract.AbiVersion,
                    StructSize = (uint)Marshal.SizeOf(typeof(NativeCreateArgs))
                };
                using (var root = Utf8Buffer.Create(_packageRoot))
                {
                    create.PackageRoot = root.Pointer;
                    create.PackageRootLength = (uint)root.Length;
                    IntPtr session;
                    var status = _native.Create(ref create, out session);
                    if (status != NativeStatus.Ok)
                    {
                        throw new InvalidOperationException("NativeBridge create failed: " + status);
                    }
                    if (session == IntPtr.Zero)
                    {
                        _native.Release(session);
                        throw new InvalidOperationException("NativeBridge create returned a null session.");
                    }
                    try
                    {
                        _session = new NativeSessionHandle(_native.Release, session);
                    }
                    catch
                    {
                        _native.Release(session);
                        throw;
                    }
                    sessionCreated = true;
                }
            }
            catch
            {
                if (!sessionCreated && _module != IntPtr.Zero)
                {
                    FreeLibrary(_module);
                    _module = IntPtr.Zero;
                }
                throw;
            }
        }

        public Task<OperationSnapshot> CheckAsync(string clientDirectory, CancellationToken cancellationToken)
        {
            return SubmitAndWaitAsync(OperationKind.Check, clientDirectory, cancellationToken);
        }

        public Task<OperationSnapshot> StartAsync(string clientDirectory, CancellationToken cancellationToken)
        {
            return SubmitAndWaitAsync(OperationKind.Start, clientDirectory, cancellationToken);
        }

        public Task<OperationSnapshot> StartAsync(string clientDirectory, string playerName, CancellationToken cancellationToken)
        {
            var error = PlayerNameRules.Validate(playerName);
            if (error.Length != 0) throw new ArgumentException(error, nameof(playerName));
            return SubmitAndWaitAsync(OperationKind.Start, clientDirectory, cancellationToken, playerName.Trim());
        }

        public Task<OperationSnapshot> StopAsync(CancellationToken cancellationToken)
        {
            return SubmitAndWaitAsync(OperationKind.Stop, null, cancellationToken);
        }

        public SessionSnapshot GetSnapshot()
        {
            var native = new NativeSnapshot
            {
                AbiVersion = NativeBridgeContract.AbiVersion,
                StructSize = (uint)Marshal.SizeOf(typeof(NativeSnapshot)),
                Phase = new byte[64]
            };
            var status = InvokeGetSnapshot(ref native);
            if (status != NativeStatus.Ok)
            {
                throw new InvalidOperationException("NativeBridge snapshot failed: " + status);
            }

            return native.ToManaged();
        }

        public string ReadLogs(ref ulong cursor)
        {
            bool ignoredGap;
            return ReadLogsInternal(ref cursor, out ignoredGap);
        }

        public LogReadResult ReadLogRecords(ref ulong cursor)
        {
            bool gap;
            var text = ReadLogsInternal(ref cursor, out gap);
            var records = new List<LogRecord>();
            foreach (var line in text.Split(new[] { '\n' }, StringSplitOptions.RemoveEmptyEntries))
            {
                var separator = line.IndexOf('\t');
                ulong recordCursor;
                if (separator <= 0 || !ulong.TryParse(line.Substring(0, separator), out recordCursor))
                {
                    continue;
                }

                records.Add(new LogRecord
                {
                    Cursor = recordCursor,
                    Text = line.Substring(separator + 1)
                });
            }

            return new LogReadResult
            {
                NextCursor = cursor,
                // When rotation leaves no records after the gap, the native
                // cursor itself is the first readable position.
                EarliestCursor = gap ? (records.Count > 0 ? records[0].Cursor : cursor) : 0,
                Gap = gap,
                Records = records
            };
        }

        private string ReadLogsInternal(ref ulong cursor, out bool gap)
        {
            return ReadLogsWithRetry(InvokeReadLogs, ref cursor, out gap);
        }

        internal delegate NativeStatus LogReader(ref ulong cursor, byte[] buffer, uint capacity,
            out uint required, out uint flags);

        internal static string ReadLogsWithRetry(LogReader read, ref ulong cursor, out bool gap)
        {
            var buffer = new byte[4096];
            while (true)
            {
                uint required;
                uint flags;
                var nextCursor = cursor;
                var status = read(ref nextCursor, buffer, (uint)buffer.Length, out required, out flags);
                if (status == NativeStatus.BufferTooSmall)
                {
                    if (required <= buffer.Length || required > MaxNativeTextBytes)
                    {
                        throw new InvalidOperationException("NativeBridge log capacity is invalid or exceeds the managed limit.");
                    }
                    buffer = new byte[Math.Max(required, Math.Min(MaxNativeTextBytes, (uint)buffer.Length * 2))];
                    continue;
                }

                if (status != NativeStatus.Ok || required > buffer.Length)
                {
                    throw new InvalidOperationException("NativeBridge logs failed: " + status);
                }

                gap = (flags & 1u) != 0;
                cursor = nextCursor;
                return Encoding.UTF8.GetString(buffer, 0, (int)required);
            }
        }

        private async Task<OperationSnapshot> SubmitAndWaitAsync(OperationKind kind, string clientDirectory, CancellationToken cancellationToken,
            string playerName = null)
        {
            cancellationToken.ThrowIfCancellationRequested();
            ulong operationId = 0;
            using (var path = clientDirectory == null ? null : Utf8Buffer.Create(clientDirectory))
            using (var name = playerName == null ? null : Utf8Buffer.CreateName(playerName))
            {
                NativePath nativePath = default(NativePath);
                if (path != null)
                {
                    nativePath = new NativePath
                    {
                        AbiVersion = NativeBridgeContract.AbiVersion,
                        StructSize = (uint)Marshal.SizeOf(typeof(NativePath)),
                        Data = path.Pointer,
                        Length = (uint)path.Length
                    };
                }

                NativeStatus status;
                if (kind == OperationKind.Check)
                {
                    status = InvokeSubmitPath(OperationKind.Check, ref nativePath, out operationId);
                }
                else if (kind == OperationKind.Start && name != null)
                {
                    var args = new NativeStartArgs
                    {
                        AbiVersion = NativeBridgeContract.AbiVersion,
                        StructSize = (uint)Marshal.SizeOf(typeof(NativeStartArgs)),
                        ClientDirectory = path.Pointer,
                        ClientDirectoryLength = (uint)path.Length,
                        PlayerName = name.Pointer,
                        PlayerNameLength = (uint)name.Length
                    };
                    status = InvokeNative(handle => _native.SubmitStartNamed(handle, ref args, out operationId));
                }
                else if (kind == OperationKind.Start)
                {
                    status = InvokeSubmitPath(OperationKind.Start, ref nativePath, out operationId);
                }
                else
                {
                    status = InvokeSubmitStop(out operationId);
                }
                if (status != NativeStatus.Ok)
                {
                    throw new InvalidOperationException("NativeBridge submit failed: " + status);
                }
            }

            return await WaitForOperationAsync(operationId, GetOperation,
                id => InvokeNative(handle => _native.Cancel(handle, id)), cancellationToken).ConfigureAwait(false);
        }

        internal static async Task<OperationSnapshot> WaitForOperationAsync(ulong operationId,
            Func<ulong, OperationSnapshot> read, Func<ulong, NativeStatus> cancel, CancellationToken cancellationToken)
        {
            var cancellationSent = false;
            while (true)
            {
                var operation = read(operationId);
                if (operation.Status == OperationStatus.Succeeded || operation.Status == OperationStatus.Cancelled || operation.Status == OperationStatus.Failed)
                {
                    // Report the native outcome, including success that won a
                    // cancellation race. A cleanup failure must remain visible.
                    if (cancellationSent && operation.Status == OperationStatus.Cancelled && operation.ErrorCode == 1001)
                    {
                        throw new OperationCanceledException(cancellationToken);
                    }
                    return operation;
                }
                try
                {
                    await Task.Delay(50, cancellationSent ? CancellationToken.None : cancellationToken).ConfigureAwait(false);
                }
                catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
                {
                    NativeStatus status;
                    try { status = cancel(operationId); }
                    catch (ObjectDisposedException)
                    {
                        // Release owns cleanup after disposal; no UI operation remains.
                        throw new OperationCanceledException(cancellationToken);
                    }
                    if (status != NativeStatus.Ok && status != NativeStatus.NotReady)
                    {
                        throw new InvalidOperationException("NativeBridge cancel failed: " + status);
                    }
                    // Cancel acknowledges a request, not completion. Continue
                    // polling without the cancelled token until cleanup finishes.
                    cancellationSent = true;
                }
            }
        }

        private OperationSnapshot GetOperation(ulong operationId)
        {
            var native = new NativeOperation
            {
                AbiVersion = NativeBridgeContract.AbiVersion,
                StructSize = (uint)Marshal.SizeOf(typeof(NativeOperation))
            };
            var status = InvokeGetOperation(operationId, ref native);
            if (status != NativeStatus.Ok)
            {
                throw new InvalidOperationException("NativeBridge operation failed: " + status);
            }

            var text = string.Empty;
            if (native.ErrorCode != 0)
            {
                uint required;
                uint errorCode;
                var bytes = new byte[1024];
                var errorStatus = InvokeGetError(operationId, bytes, (uint)bytes.Length, out required, out errorCode);
                if (errorStatus == NativeStatus.BufferTooSmall)
                {
                    if (required > MaxNativeTextBytes)
                    {
                        throw new InvalidOperationException("NativeBridge error text exceeds the managed limit.");
                    }
                    bytes = new byte[required];
                    errorStatus = InvokeGetError(operationId, bytes, (uint)bytes.Length, out required, out errorCode);
                }
                if (errorStatus != NativeStatus.Ok)
                {
                    throw new InvalidOperationException("NativeBridge error read failed: " + errorStatus);
                }
                text = Encoding.UTF8.GetString(bytes, 0, (int)Math.Min(required, (uint)bytes.Length));
            }

            return new OperationSnapshot
            {
                OperationId = native.OperationId,
                Kind = (OperationKind)native.Kind,
                Status = (OperationStatus)native.Status,
                ErrorCode = native.ErrorCode,
                Error = text
            };
        }

        private void EnsureUsable()
        {
            if (_disposed || _native == null || _session == null || _session.IsInvalid)
            {
                throw new ObjectDisposedException(nameof(NativeBridgeService));
            }
        }

        public void Dispose()
        {
            lock (_sync)
            {
                if (_disposed)
                {
                    return;
                }

                _disposed = true;
                _session?.Dispose();
                _session = null;
                // t7_native_release is deliberately non-blocking: the native
                // worker may still execute code inside this DLL while it finishes
                // client/Python/socket cleanup.  Keep the module loaded until the
                // host process exits instead of unloading code out from under it.
            }
        }

        private T InvokeNative<T>(Func<IntPtr, T> action)
        {
            lock (_sync)
            {
                EnsureUsable();
                return action(_session.DangerousGetHandle());
            }
        }

        private NativeStatus InvokeGetSnapshot(ref NativeSnapshot snapshot)
        {
            lock (_sync)
            {
                EnsureUsable();
                return _native.GetSnapshot(_session.DangerousGetHandle(), ref snapshot);
            }
        }

        private NativeStatus InvokeReadLogs(ref ulong cursor, byte[] buffer, uint capacity,
            out uint required, out uint flags)
        {
            lock (_sync)
            {
                EnsureUsable();
                return _native.ReadLogs(_session.DangerousGetHandle(), ref cursor, buffer, capacity, out required, out flags);
            }
        }

        private NativeStatus InvokeSubmitPath(OperationKind kind, ref NativePath path, out ulong operationId)
        {
            lock (_sync)
            {
                EnsureUsable();
                return kind == OperationKind.Check
                    ? _native.SubmitCheck(_session.DangerousGetHandle(), ref path, out operationId)
                    : _native.SubmitStart(_session.DangerousGetHandle(), ref path, out operationId);
            }
        }

        private NativeStatus InvokeSubmitStop(out ulong operationId)
        {
            lock (_sync)
            {
                EnsureUsable();
                return _native.SubmitStop(_session.DangerousGetHandle(), out operationId);
            }
        }

        private NativeStatus InvokeGetOperation(ulong operationId, ref NativeOperation operation)
        {
            lock (_sync)
            {
                EnsureUsable();
                return _native.GetOperation(_session.DangerousGetHandle(), operationId, ref operation);
            }
        }

        private NativeStatus InvokeGetError(ulong operationId, byte[] buffer, uint capacity,
            out uint required, out uint errorCode)
        {
            lock (_sync)
            {
                EnsureUsable();
                return _native.GetError(_session.DangerousGetHandle(), operationId, buffer, capacity,
                    out required, out errorCode);
            }
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr LoadLibraryEx(string fileName, IntPtr file, uint flags);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool FreeLibrary(IntPtr module);

        [StructLayout(LayoutKind.Sequential, Pack = 8)]
        private struct NativePath
        {
            public uint AbiVersion;
            public uint StructSize;
            public IntPtr Data;
            public uint Length;
        }

        [StructLayout(LayoutKind.Sequential, Pack = 8)]
        private struct NativeCreateArgs
        {
            public uint AbiVersion;
            public uint StructSize;
            public IntPtr PackageRoot;
            public uint PackageRootLength;
            public uint Flags;
        }

        [StructLayout(LayoutKind.Sequential, Pack = 8, CharSet = CharSet.Ansi)]
        private struct NativeSnapshot
        {
            public uint AbiVersion;
            public uint StructSize;
            public uint State;
            public uint Operation;
            public ulong OperationId;
            public ulong Revision;
            public ushort LoginPort;
            public ushort LogicPort;
            public ushort InstancePort;
            public ushort Reserved;
            public uint ErrorCode;
            public uint Flags;
            public ulong LogCursor;
            [MarshalAs(UnmanagedType.ByValArray, SizeConst = 64)]
            public byte[] Phase;

            public SessionSnapshot ToManaged()
            {
                var phase = Phase == null ? string.Empty : Encoding.ASCII.GetString(Phase).TrimEnd('\0');
                return new SessionSnapshot
                {
                    State = (SessionState)State,
                    Operation = (OperationKind)Operation,
                    OperationId = OperationId,
                    Revision = Revision,
                    LoginPort = LoginPort,
                    LogicPort = LogicPort,
                    InstancePort = InstancePort,
                    ErrorCode = ErrorCode,
                    CleanupComplete = (Flags & 1u) != 0,
                    Phase = phase
                };
            }
        }

        [StructLayout(LayoutKind.Sequential, Pack = 8)]
        private struct NativeOperation
        {
            public uint AbiVersion;
            public uint StructSize;
            public ulong OperationId;
            public uint Kind;
            public uint Status;
            public uint ErrorCode;
            public uint Reserved;
        }

        private sealed class Utf8Buffer : IDisposable
        {
            private readonly byte[] _bytes;
            private GCHandle _handle;

            private Utf8Buffer(string value)
            {
                _bytes = Encoding.UTF8.GetBytes(value ?? string.Empty);
                _handle = GCHandle.Alloc(_bytes, GCHandleType.Pinned);
            }

            public IntPtr Pointer => _handle.AddrOfPinnedObject();
            public int Length => _bytes.Length;

            public static Utf8Buffer Create(string value)
            {
                if (!NativeBridgeContract.IsUtf8PathAcceptable(value))
                {
                    throw new ArgumentException("路径为空、包含 NUL 或过长。", nameof(value));
                }
                return new Utf8Buffer(value);
            }

            public static Utf8Buffer CreateName(string value)
            {
                var error = PlayerNameRules.Validate(value);
                if (error.Length != 0) throw new ArgumentException(error, nameof(value));
                return new Utf8Buffer(value);
            }

            public void Dispose()
            {
                if (_handle.IsAllocated)
                {
                    _handle.Free();
                }
            }
        }

        private sealed class NativeSessionHandle : SafeHandle
        {
            private readonly NativeMethods.ReleaseDelegate _release;

            public NativeSessionHandle(NativeMethods.ReleaseDelegate release, IntPtr value) : base(IntPtr.Zero, true)
            {
                _release = release ?? throw new ArgumentNullException(nameof(release));
                SetHandle(value);
            }

            public override bool IsInvalid => handle == IntPtr.Zero || handle == new IntPtr(-1);

            protected override bool ReleaseHandle()
            {
                return _release(handle) == NativeStatus.Ok;
            }
        }

        private sealed class NativeMethods
        {
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus GetAbiDelegate(out uint version, out uint snapshotSize);
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus CreateDelegate(ref NativeCreateArgs args, out IntPtr session);
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus ReleaseDelegate(IntPtr session);
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus SubmitPathDelegate(IntPtr session, ref NativePath path, out ulong operationId);
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus SubmitStartNamedDelegate(IntPtr session, ref NativeStartArgs args, out ulong operationId);
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus SubmitStopDelegate(IntPtr session, out ulong operationId);
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus CancelDelegate(IntPtr session, ulong operationId);
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus GetSnapshotDelegate(IntPtr session, ref NativeSnapshot snapshot);
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus GetOperationDelegate(IntPtr session, ulong operationId, ref NativeOperation operation);
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus GetErrorDelegate(IntPtr session, ulong operationId, byte[] buffer, uint capacity,
                out uint required, out uint errorCode);
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus ReadLogsDelegate(IntPtr session, ref ulong cursor, byte[] buffer, uint capacity,
                out uint required, out uint flags);

            internal readonly GetAbiDelegate GetAbi;
            internal readonly CreateDelegate Create;
            internal readonly ReleaseDelegate Release;
            internal readonly SubmitPathDelegate SubmitCheck;
            internal readonly SubmitPathDelegate SubmitStart;
            internal readonly SubmitStartNamedDelegate SubmitStartNamed;
            internal readonly SubmitStopDelegate SubmitStop;
            internal readonly CancelDelegate Cancel;
            internal readonly GetSnapshotDelegate GetSnapshot;
            internal readonly GetOperationDelegate GetOperation;
            internal readonly GetErrorDelegate GetError;
            internal readonly ReadLogsDelegate ReadLogs;

            private NativeMethods(IntPtr module)
            {
                GetAbi = Resolve<GetAbiDelegate>(module, "t7_native_get_abi");
                Create = Resolve<CreateDelegate>(module, "t7_native_create");
                Release = Resolve<ReleaseDelegate>(module, "t7_native_release");
                SubmitCheck = Resolve<SubmitPathDelegate>(module, "t7_native_submit_check");
                SubmitStart = Resolve<SubmitPathDelegate>(module, "t7_native_submit_start");
                SubmitStartNamed = Resolve<SubmitStartNamedDelegate>(module, "t7_native_submit_start_named");
                SubmitStop = Resolve<SubmitStopDelegate>(module, "t7_native_submit_stop");
                Cancel = Resolve<CancelDelegate>(module, "t7_native_cancel");
                GetSnapshot = Resolve<GetSnapshotDelegate>(module, "t7_native_get_snapshot");
                GetOperation = Resolve<GetOperationDelegate>(module, "t7_native_get_operation");
                GetError = Resolve<GetErrorDelegate>(module, "t7_native_get_error");
                ReadLogs = Resolve<ReadLogsDelegate>(module, "t7_native_read_logs");
            }

            internal static NativeMethods Load(IntPtr module)
            {
                return new NativeMethods(module);
            }

            private static T Resolve<T>(IntPtr module, string name) where T : class
            {
                var address = GetProcAddress(module, name);
                if (address == IntPtr.Zero)
                {
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "NativeBridge export missing: " + name);
                }

                return (T)(object)Marshal.GetDelegateForFunctionPointer(address, typeof(T));
            }

            [DllImport("kernel32.dll", CharSet = CharSet.Ansi, SetLastError = true)]
            private static extern IntPtr GetProcAddress(IntPtr module, string name);
        }
    }
}
