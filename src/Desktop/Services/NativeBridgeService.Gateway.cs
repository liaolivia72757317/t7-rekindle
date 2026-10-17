using System;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using T7R.Battle;
using T7R.Core;

namespace T7R.Desktop.Services
{
    public sealed partial class NativeBridgeService
    {
        [StructLayout(LayoutKind.Sequential, Pack = 8)]
        private struct NativeGatewayStartOptions
        {
            public NativeStartOptions Start;
            public ushort LoginPort;
            public ushort LogicPort;
            public ushort InstancePort;
            public ushort Reserved;
        }

        [StructLayout(LayoutKind.Sequential, Pack = 8)]
        private struct NativeGatewayProfileStartOptions
        {
            public NativeGatewayStartOptions Gateway;
            public IntPtr Profiles;
            public uint ProfileCount;
            public uint Reserved;
        }

        internal static void ValidateGatewayStartLayout()
        {
            if (Marshal.SizeOf(typeof(NativeGatewayStartOptions)) != 56
                || (int)Marshal.OffsetOf(typeof(NativeGatewayStartOptions), nameof(NativeGatewayStartOptions.LoginPort)) != 48
                || Marshal.SizeOf(typeof(NativeCombatProfile)) != 36
                || Marshal.SizeOf(typeof(NativeGatewayProfileStartOptions)) != 72
                || (int)Marshal.OffsetOf(typeof(NativeGatewayProfileStartOptions),
                    nameof(NativeGatewayProfileStartOptions.Profiles)) != 56
                || (int)Marshal.OffsetOf(typeof(NativeGatewayProfileStartOptions),
                    nameof(NativeGatewayProfileStartOptions.ProfileCount)) != 64)
                throw new InvalidOperationException("NativeBridge gateway start ABI layout mismatch.");
        }

        internal async Task<OperationSnapshot> StartWithGatewayAsync(string clientDirectory, string playerName,
            bool skipStartupAnimation, ushort loginPort, ushort logicPort, ushort instancePort,
            CancellationToken cancellationToken)
        {
            return await StartWithGatewayProfilesAsync(clientDirectory, playerName, skipStartupAnimation,
                loginPort, logicPort, instancePort, null, cancellationToken).ConfigureAwait(false);
        }

        internal async Task<OperationSnapshot> StartWithGatewayProfilesAsync(string clientDirectory, string playerName,
            bool skipStartupAnimation, ushort loginPort, ushort logicPort, ushort instancePort,
            NativeCombatProfile[] profiles, CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (loginPort == 0 || logicPort == 0 || instancePort == 0 || loginPort == logicPort
                || loginPort == instancePort || logicPort == instancePort)
                throw new ArgumentException("Gateway ports must be nonzero and distinct.");
            var error = PlayerNameRules.Validate(playerName);
            if (error.Length != 0) throw new ArgumentException(error, nameof(playerName));
            if (profiles != null && (profiles.Length == 0 || profiles.Length > 2048))
                throw new ArgumentException("Expected a bounded combat profile table.", nameof(profiles));

            ulong operationId;
            using (var path = Utf8Buffer.Create(clientDirectory))
            using (var name = Utf8Buffer.CreateName(playerName.Trim()))
            {
                var options = new NativeGatewayStartOptions
                {
                    Start = new NativeStartOptions
                    {
                        Start = new NativeStartArgs
                        {
                            AbiVersion = NativeBridgeContract.AbiVersion,
                            StructSize = (uint)Marshal.SizeOf(profiles == null
                                ? typeof(NativeGatewayStartOptions) : typeof(NativeGatewayProfileStartOptions)),
                            ClientDirectory = path.Pointer,
                            ClientDirectoryLength = (uint)path.Length,
                            PlayerName = name.Pointer,
                            PlayerNameLength = (uint)name.Length
                        },
                        Flags = skipStartupAnimation ? NativeStartFlags.SkipStartupAnimation : NativeStartFlags.None
                    },
                    LoginPort = loginPort,
                    LogicPort = logicPort,
                    InstancePort = instancePort
                };
                NativeStatus status;
                var pin = default(GCHandle);
                try
                {
                    if (profiles != null) pin = GCHandle.Alloc(profiles, GCHandleType.Pinned);
                    lock (_sync)
                    {
                        EnsureUsable();
                        if (profiles == null)
                            status = _native.SubmitStartGateway(_session.DangerousGetHandle(), ref options, out operationId);
                        else
                        {
                            var profiled = new NativeGatewayProfileStartOptions
                            {
                                Gateway = options, Profiles = pin.AddrOfPinnedObject(),
                                ProfileCount = checked((uint)profiles.Length)
                            };
                            status = _native.SubmitStartGatewayProfiles(_session.DangerousGetHandle(), ref profiled,
                                out operationId);
                        }
                    }
                }
                finally { if (pin.IsAllocated) pin.Free(); }
                if (status != NativeStatus.Ok)
                    throw new InvalidOperationException("NativeBridge gateway start submit failed: " + status);
            }

            return await WaitForOperationAsync(operationId, GetOperation,
                id => InvokeNative(handle => _native.Cancel(handle, id)), cancellationToken).ConfigureAwait(false);
        }

        private sealed partial class NativeMethods
        {
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus SubmitGatewayStartDelegate(IntPtr session,
                ref NativeGatewayStartOptions args, out ulong operationId);

            internal readonly SubmitGatewayStartDelegate SubmitStartGateway;
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus SubmitGatewayProfileStartDelegate(IntPtr session,
                ref NativeGatewayProfileStartOptions args, out ulong operationId);
            internal readonly SubmitGatewayProfileStartDelegate SubmitStartGatewayProfiles;
        }
    }
}
