using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
using T7R.Core;

namespace T7R.Battle
{
    [StructLayout(LayoutKind.Sequential)]
    internal struct GatewayNativeSnapshot
    {
        internal uint AbiVersion, StructSize;
        internal ulong Epoch;
        internal uint State;
        internal ushort LoginPort, LogicPort, InstancePort, Reserved;
        internal uint ActiveConnections, PendingRequests, ConnectionErrors;
        internal uint RulesVersion, ResourceVersion, MapId, Reserved2;
    }

    internal sealed partial class ClientGatewayNative : IDisposable
    {
        private readonly object _sync = new object();
        private readonly LibraryHandle _library;
        private readonly GatewayHandle _handle;
        private readonly StartCall _start;
        private readonly HandleCall _stop;
        private readonly SnapshotCall _snapshot;
        private readonly ReadCall _read, _error;
        private readonly CompleteCall _complete;
        private readonly byte[] _requestBuffer = new byte[4096];
        private HandleCall _dispatchBattle;
        private BattleReturnedCall _battleReturned;
        private BattleReturnedCall _battlePreparationOpen;
        private BattleReturnedCall _battleReserveRearm;
        private bool _battleStarted;
        private long? _battleOutputBusySince;
        private bool _disposed;

        internal ClientGatewayNative(string libraryPath, byte[] resources) : this(libraryPath, resources, null) { }
        internal ClientGatewayNative(string libraryPath, LocalResourceOptions resources) : this(libraryPath, null,
            resources ?? throw new ArgumentNullException(nameof(resources))) { }
        internal bool WasResourceCacheHit { get; }
        internal ulong HandleValue
        {
            get { lock (_sync) { EnsureOpen(); return unchecked((ulong)_handle.DangerousGetHandle().ToInt64()); } }
        }

        private ClientGatewayNative(string libraryPath, byte[] resources, LocalResourceOptions localResources)
        {
            if (!NativeBridgeContract.IsUtf8PathAcceptable(libraryPath))
                throw new ArgumentException("Expected an absolute native library path.", nameof(libraryPath));
            if (localResources == null && (resources == null || resources.Length == 0 || resources.Length > 64 * 1024 * 1024))
                throw new ArgumentException("Expected a bounded resource bundle.", nameof(resources));
            if (IntPtr.Size != 8 || Marshal.SizeOf<GatewayNativeSnapshot>() != 56)
                throw new PlatformNotSupportedException("Client gateway requires the x64 ABI.");
            _library = new LibraryHandle(libraryPath);
            try
            {
                if (Function<VersionCall>("t7r_runtime_abi_version")() != 1)
                    throw new InvalidOperationException("Rust runtime ABI mismatch.");
                var release = Function<ReleaseCall>("t7r_gateway_release");
                _start = Function<StartCall>("t7r_gateway_start");
                _stop = Function<HandleCall>("t7r_gateway_stop");
                _snapshot = Function<SnapshotCall>("t7r_gateway_snapshot");
                _read = Function<ReadCall>("t7r_gateway_read_request");
                _error = Function<ReadCall>("t7r_gateway_error");
                _complete = Function<CompleteCall>("t7r_gateway_complete");
                ulong value;
                if (localResources == null)
                    Check(Function<CreateCall>("t7r_gateway_create")(resources, (uint)resources.Length, out value));
                else
                {
                    var request = localResources.Serialize();
                    var error = new byte[4096];
                    var status = Function<CreateLocalCall>("t7r_gateway_create_local")(request, (uint)request.Length,
                        out value, out var hit, error, (uint)error.Length, out var length);
                    if (length > error.Length) throw new InvalidOperationException("Invalid resource error length.");
                    if (status != 0) throw new InvalidOperationException("Local resource preparation failed: " +
                        new UTF8Encoding(false, true).GetString(error, 0, (int)length) + " (" + status + ")");
                    WasResourceCacheHit = hit == 1;
                }
                _handle = new GatewayHandle(value, _library, release);
            }
            catch { _library.Dispose(); throw; }
        }

        internal void Start(ulong epoch) { lock (_sync) { EnsureOpen(); Check(_start(_handle, epoch)); } }
        internal void Stop() { lock (_sync) { EnsureOpen(); _battleStarted = false; _battleOutputBusySince = null; _battleReturned = null; _battlePreparationOpen = null; _battleReserveRearm = null; Check(_stop(_handle)); } }
        internal void StartBattle(BattleNativeConfig config)
        {
            lock (_sync)
            {
                EnsureOpen();
                var dispatch = Function<HandleCall>("t7r_gateway_battle_dispatch_next");
                var returned = Function<BattleReturnedCall>("t7r_gateway_battle_returned_to_room");
                var preparation = Function<BattleReturnedCall>("t7r_gateway_battle_preparation_open");
                var reserveRearm = Function<BattleReturnedCall>("t7r_gateway_battle_reserve_rearm");
                Check(Function<BattleStartCall>("t7r_gateway_battle_start_staged")(_handle, ref config));
                _dispatchBattle = dispatch;
                _battleReturned = returned;
                _battlePreparationOpen = preparation;
                _battleReserveRearm = reserveRearm;
                _battleStarted = true;
                _battleOutputBusySince = null;
            }
        }
        internal void StopBattle()
        {
            lock (_sync)
            {
                EnsureOpen();
                _battleStarted = false;
                _battleOutputBusySince = null;
                _battleReturned = null;
                _battlePreparationOpen = null;
                _battleReserveRearm = null;
                Check(Function<HandleCall>("t7r_gateway_battle_stop")(_handle));
            }
        }
        internal void SelectMap(uint mapId)
        {
            lock (_sync)
            {
                EnsureOpen();
                if (_battleStarted) throw new InvalidOperationException("Map selection requires a stopped battle.");
                Check(Function<SelectMapCall>("t7r_gateway_select_map")(_handle, mapId));
            }
        }
        internal bool TryDispatchBattleOutput()
        {
            lock (_sync)
            {
                EnsureOpen();
                if (!_battleStarted) return false;
                var status = _dispatchBattle(_handle);
                if (status == 8) // BUSY: the complete pending output remains owned by Rust.
                {
                    var now = Stopwatch.GetTimestamp();
                    if (!_battleOutputBusySince.HasValue) _battleOutputBusySince = now;
                    if ((now - _battleOutputBusySince.Value) / (double)Stopwatch.Frequency >= 5)
                        throw new TimeoutException("Client gateway output backpressure exceeded 5 seconds; " + LastError());
                    return false;
                }
                _battleOutputBusySince = null;
                if (status == 5) return false;
                if (status != 0)
                    throw new InvalidOperationException("Client gateway status: " + status + "; " + LastError());
                return true;
            }
        }
        internal bool BattleReturnedToRoom()
        {
            lock (_sync)
            {
                EnsureOpen();
                if (!_battleStarted) throw new InvalidOperationException("Room return requires a running match.");
                Check(_battleReturned(_handle, out var returned));
                if (returned > 1) throw new InvalidOperationException("Invalid battle return state.");
                return returned == 1;
            }
        }
        internal ulong BattleClockMs()
        {
            lock (_sync)
            {
                EnsureOpen();
                Check(Function<BattleClockCall>("t7r_gateway_battle_clock")(_handle, out var value));
                return value;
            }
        }
        internal bool BattlePreparationOpen()
        {
            lock (_sync)
            {
                EnsureOpen();
                if (!_battleStarted) return false;
                Check(_battlePreparationOpen(_handle, out var open));
                if (open > 1) throw new InvalidOperationException("Invalid battle preparation state.");
                return open == 1;
            }
        }
        internal bool TryReserveBattleRearm()
        {
            lock (_sync)
            {
                EnsureOpen();
                if (!_battleStarted) return false;
                Check(_battleReserveRearm(_handle, out var reserved));
                if (reserved > 1) throw new InvalidOperationException("Invalid battle rearm reservation.");
                return reserved == 1;
            }
        }
        internal GatewayNativeSnapshot Snapshot()
        {
            lock (_sync)
            {
                EnsureOpen();
                var value = new GatewayNativeSnapshot { AbiVersion = 1, StructSize = 56 };
                Check(_snapshot(_handle, ref value));
                return value;
            }
        }
        internal byte[] ResourceBundleJson()
        {
            lock (_sync)
            {
                EnsureOpen();
                var read = Function<ReadCall>("t7r_gateway_resource_json");
                var status = read(_handle, null, 0, out var required);
                if (status != 4 || required == 0 || required > 64 * 1024 * 1024)
                    throw new InvalidOperationException("Invalid gateway resource size probe: " + status);
                var bytes = new byte[checked((int)required)];
                Check(read(_handle, bytes, required, out var received));
                if (received != required)
                    throw new InvalidOperationException("Gateway resource size changed during copy.");
                return bytes;
            }
        }
        internal byte[] ReadRequest()
        {
            lock (_sync)
            {
                EnsureOpen();
                var status = _read(_handle, _requestBuffer, (uint)_requestBuffer.Length, out var length);
                if (status == 5) return null;
                Check(status);
                if (length > _requestBuffer.Length) throw new InvalidOperationException("Invalid gateway buffer length.");
                var buffer = new byte[length];
                Array.Copy(_requestBuffer, buffer, (int)length);
                return buffer;
            }
        }
        internal bool Complete(byte[] response)
        {
            lock (_sync)
            {
                EnsureOpen();
                var status = _complete(_handle, response, (uint)response.Length);
                if (status == 6) return false;
                Check(status);
                return true;
            }
        }
        internal string LastError()
        {
            lock (_sync)
            {
                EnsureOpen();
                var bytes = new byte[4096];
                Check(_error(_handle, bytes, (uint)bytes.Length, out var length));
                return new UTF8Encoding(false, true).GetString(bytes, 0, checked((int)length));
            }
        }
        public void Dispose()
        {
            lock (_sync)
            {
                if (_disposed) return;
                _disposed = true;
                _handle.Dispose();
                _library.Dispose();
            }
        }
        private void EnsureOpen() { if (_disposed) throw new ObjectDisposedException(nameof(ClientGatewayNative)); }
        private static void Check(int status)
        {
            if (status != 0) throw new InvalidOperationException("Client gateway status: " + status);
        }
        private T Function<T>(string name) where T : Delegate
        {
            var address = GetProcAddress(_library, name);
            if (address == IntPtr.Zero) throw new EntryPointNotFoundException(name);
            return Marshal.GetDelegateForFunctionPointer<T>(address);
        }
        private sealed class LibraryHandle : SafeHandleZeroOrMinusOneIsInvalid
        {
            internal LibraryHandle(string path) : base(true)
            {
                SetHandle(LoadLibraryEx(path, IntPtr.Zero, 0x100 | 0x800));
                if (IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error(), "Loading Rust gateway failed.");
            }
            protected override bool ReleaseHandle() => FreeLibrary(handle);
        }
        private sealed class GatewayHandle : SafeHandleZeroOrMinusOneIsInvalid
        {
            private readonly LibraryHandle _library;
            private readonly ReleaseCall _release;
            internal GatewayHandle(ulong value, LibraryHandle library, ReleaseCall release) : base(true)
            {
                bool retained = false;
                library.DangerousAddRef(ref retained);
                _library = library; _release = release;
                SetHandle(new IntPtr(unchecked((long)value)));
            }
            protected override bool ReleaseHandle()
            {
                var status = _release(unchecked((ulong)handle.ToInt64()));
                _library.DangerousRelease();
                return status == 0;
            }
        }
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate uint VersionCall();
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate int CreateCall(byte[] data, uint length, out ulong handle);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate int CreateLocalCall(byte[] data, uint length,
            out ulong handle, out uint cacheHit, [Out] byte[] error, uint capacity, out uint errorLength);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate int ReleaseCall(ulong handle);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate int HandleCall(GatewayHandle handle);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate int StartCall(GatewayHandle handle, ulong epoch);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate int BattleStartCall(GatewayHandle handle, ref BattleNativeConfig config);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate int BattleClockCall(GatewayHandle handle, out ulong clockMs);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate int SelectMapCall(GatewayHandle handle, uint mapId);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate int BattleReturnedCall(GatewayHandle handle, out uint returned);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate int SnapshotCall(GatewayHandle handle, ref GatewayNativeSnapshot value);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate int ReadCall(GatewayHandle handle, [Out] byte[] data, uint capacity, out uint length);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate int CompleteCall(GatewayHandle handle, byte[] data, uint length);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern IntPtr LoadLibraryEx(string path, IntPtr file, uint flags);
        [DllImport("kernel32.dll", CharSet = CharSet.Ansi, ExactSpelling = true)] private static extern IntPtr GetProcAddress(LibraryHandle library, string name);
        [DllImport("kernel32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool FreeLibrary(IntPtr library);
    }
}
