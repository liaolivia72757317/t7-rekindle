using System;
using System.Runtime.InteropServices;
using T7.Rekindle.Core;

namespace T7.Rekindle.Desktop.Services
{
    public sealed partial class NativeBridgeService
    {
        public void SetOutputDevice(Guid identifier)
        {
            var status = InvokeNative(handle => _native.SetOutputDevice(handle, identifier.ToByteArray(), 16));
            if (status != NativeStatus.Ok) throw new InvalidOperationException("设置输出设备失败：" + status);
        }
        private sealed partial class NativeMethods
        {
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus OutputDeviceDelegate(IntPtr session, [In] byte[] identifier, uint length);
            internal readonly OutputDeviceDelegate SetOutputDevice;
        }
    }
}
