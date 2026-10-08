using System;
using System.Runtime.InteropServices;
using T7.Rekindle.Core;

namespace T7.Rekindle.Desktop.Services
{
    public sealed partial class NativeBridgeService
    {
        public AudioSettingsSnapshot GetAudioSettings()
        {
            var snapshot = new NativeAudioSnapshot
            {
                AbiVersion = NativeBridgeContract.AbiVersion,
                StructSize = (uint)Marshal.SizeOf(typeof(NativeAudioSnapshot))
            };
            var status = InvokeNative(handle => _native.GetAudio(handle, ref snapshot));
            if (status != NativeStatus.Ok) throw new InvalidOperationException("Audio read failed: " + status);
            var values = snapshot.State == AudioSyncState.Unavailable || snapshot.State == AudioSyncState.Failed
                ? null : snapshot.Values.ToManaged();
            return new AudioSettingsSnapshot(snapshot.Revision, snapshot.State, values);
        }
        public void ApplyAudioSettings(ulong revision, AudioSettingsValues values)
        {
            if (values == null || !values.IsValid) throw new ArgumentException("Invalid audio settings.", nameof(values));
            var snapshot = new NativeAudioSnapshot
            {
                AbiVersion = NativeBridgeContract.AbiVersion,
                StructSize = (uint)Marshal.SizeOf(typeof(NativeAudioSnapshot)),
                Revision = revision, State = AudioSyncState.Ready, Values = NativeAudioValues.FromManaged(values)
            };
            var status = InvokeNative(handle => _native.ApplyAudio(handle, ref snapshot));
            if (status == NativeStatus.NotReady) throw new InvalidOperationException("游戏设置已改变或尚未就绪，请等待同步后重试。");
            if (status != NativeStatus.Ok) throw new InvalidOperationException("提交声音设置失败：" + status);
        }
        private sealed partial class NativeMethods
        {
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus AudioDelegate(IntPtr session, ref NativeAudioSnapshot snapshot);
            internal readonly AudioDelegate GetAudio;
            internal readonly AudioDelegate ApplyAudio;
        }
    }
}
