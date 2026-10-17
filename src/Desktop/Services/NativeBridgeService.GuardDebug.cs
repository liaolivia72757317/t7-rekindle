using System;
using System.Runtime.InteropServices;
using T7R.Core;

namespace T7R.Desktop.Services
{
    public sealed partial class NativeBridgeService
    {
        public void SetGuardDebug(bool enabled)
        {
            var status = InvokeNative(handle => _native.SetGuardDebug(handle, enabled ? 1u : 0u));
            if (status != NativeStatus.Ok) throw new InvalidOperationException("设置格挡调试显示失败：" + status);
        }

        private sealed partial class NativeMethods
        {
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus GuardDebugDelegate(IntPtr session, uint enabled);
            internal readonly GuardDebugDelegate SetGuardDebug;
        }
    }
}
