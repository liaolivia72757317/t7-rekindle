using System;
using System.Runtime.InteropServices;
using T7R.Battle;
using T7R.Core;

namespace T7R.Desktop.Services
{
    public sealed partial class NativeBridgeService
    {
        [StructLayout(LayoutKind.Sequential, Pack = 8)]
        private struct NativeBattleAttachArgs
        {
            public uint AbiVersion;
            public uint StructSize;
            public ulong GatewayHandle;
            public IntPtr Config;
            public NativePath RustLibrary;
        }

        internal static void ValidateBattleAttachmentLayout()
        {
            if (Marshal.SizeOf(typeof(NativeBattleAttachArgs)) != 48
                || Marshal.SizeOf(typeof(BattleNativeConfig)) != BattleLimits.NativeConfigSize)
                throw new InvalidOperationException("NativeBridge battle attachment ABI layout mismatch.");
        }

        internal void AttachBattle(BattleNativeConfig config, string rustLibraryPath, ulong gatewayHandle)
        {
            if (gatewayHandle == 0) throw new ArgumentOutOfRangeException(nameof(gatewayHandle));
            var buffer = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(BattleNativeConfig)));
            var marshaled = false;
            try
            {
                Marshal.StructureToPtr(config, buffer, false);
                marshaled = true;
                using (var path = Utf8Buffer.Create(rustLibraryPath))
                {
                    var args = new NativeBattleAttachArgs
                    {
                        AbiVersion = NativeBridgeContract.AbiVersion,
                        StructSize = (uint)Marshal.SizeOf(typeof(NativeBattleAttachArgs)),
                        GatewayHandle = gatewayHandle,
                        Config = buffer,
                        RustLibrary = new NativePath
                        {
                            AbiVersion = NativeBridgeContract.AbiVersion,
                            StructSize = (uint)Marshal.SizeOf(typeof(NativePath)),
                            Data = path.Pointer,
                            Length = (uint)path.Length
                        }
                    };
                    var status = InvokeNative(handle => _native.AttachBattle(handle, ref args));
                    if (status != NativeStatus.Ok)
                        throw new InvalidOperationException("NativeBridge battle attachment failed: " + status);
                }
            }
            finally
            {
                if (marshaled) Marshal.DestroyStructure(buffer, typeof(BattleNativeConfig));
                Marshal.FreeHGlobal(buffer);
            }
        }

        internal void DetachBattle()
        {
            var status = InvokeNative(handle => _native.DetachBattle(handle));
            if (status != NativeStatus.Ok)
                throw new InvalidOperationException("NativeBridge battle detachment failed: " + status);
        }

        private sealed partial class NativeMethods
        {
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus AttachBattleDelegate(IntPtr session, ref NativeBattleAttachArgs args);
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus DetachBattleDelegate(IntPtr session);
            internal readonly AttachBattleDelegate AttachBattle;
            internal readonly DetachBattleDelegate DetachBattle;
        }
    }
}
