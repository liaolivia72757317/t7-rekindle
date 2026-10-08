using System;
using System.Runtime.InteropServices;
using T7.Rekindle.Core;

namespace T7.Rekindle.Desktop.Services
{
    public sealed partial class NativeBridgeService
    {
        public GraphicsSettingsSnapshot GetGraphicsSettings()
        {
            var snapshot = new NativeGraphicsSnapshot
            {
                AbiVersion = NativeBridgeContract.AbiVersion,
                StructSize = (uint)Marshal.SizeOf(typeof(NativeGraphicsSnapshot))
            };
            var status = InvokeNative(handle => _native.GetGraphics(handle, ref snapshot));
            if (status != NativeStatus.Ok) throw new InvalidOperationException("Graphics read failed: " + status);
            var values = snapshot.State == GraphicsSyncState.Unavailable || snapshot.State == GraphicsSyncState.Failed
                ? null : snapshot.Values.ToManaged();
            return new GraphicsSettingsSnapshot(snapshot.Revision, snapshot.State, values);
        }
        public void ApplyGraphicsSettings(ulong revision, GraphicsSettingsValues values)
        {
            if (values == null || !values.IsValid) throw new ArgumentException("Invalid graphics settings.", nameof(values));
            var snapshot = new NativeGraphicsSnapshot
            {
                AbiVersion = NativeBridgeContract.AbiVersion,
                StructSize = (uint)Marshal.SizeOf(typeof(NativeGraphicsSnapshot)),
                Revision = revision, State = GraphicsSyncState.Ready, Values = NativeGraphicsValues.FromManaged(values)
            };
            var status = InvokeNative(handle => _native.ApplyGraphics(handle, ref snapshot));
            if (status == NativeStatus.NotReady) throw new InvalidOperationException("游戏设置已改变或尚未就绪，请等待同步后重试。");
            if (status != NativeStatus.Ok) throw new InvalidOperationException("提交画面设置失败：" + status);
        }
        private sealed partial class NativeMethods
        {
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus GraphicsDelegate(IntPtr session, ref NativeGraphicsSnapshot snapshot);
            internal readonly GraphicsDelegate GetGraphics;
            internal readonly GraphicsDelegate ApplyGraphics;
        }
    }
}
