using System;
using System.Runtime.InteropServices;
using System.Text;

namespace T7R.Battle
{
    [StructLayout(LayoutKind.Sequential, Pack = 8)]
    internal struct GatewayTitleRequest
    {
        internal uint AbiVersion, StructSize;
        internal ulong RequestId, Epoch;
        internal uint TitleId, Reserved;
    }

    public sealed partial class ClientGateway
    {
        internal void PublishTitles(ulong epoch, string json, ulong requestId, bool success) =>
            _native.PublishTitles(epoch, Encoding.UTF8.GetBytes(json), requestId, success);
        internal GatewayTitleRequest ReadTitleRequest() => _native.ReadTitleRequest();
    }

    internal sealed partial class ClientGatewayNative
    {
        internal void PublishTitles(ulong epoch, byte[] json, ulong requestId, bool success)
        {
            lock (_sync)
            {
                EnsureOpen();
                Check(Function<PublishTitlesCall>("t7r_gateway_publish_titles")(_handle, epoch, json,
                    checked((uint)json.Length), requestId, success ? 1u : 0u));
            }
        }
        internal GatewayTitleRequest ReadTitleRequest()
        {
            lock (_sync)
            {
                EnsureOpen();
                if (Marshal.SizeOf<GatewayTitleRequest>() != 32) throw new PlatformNotSupportedException("Title request layout mismatch.");
                var value = new GatewayTitleRequest { AbiVersion = 1, StructSize = 32 };
                Check(Function<TitleRequestCall>("t7r_gateway_title_request")(_handle, ref value));
                return value;
            }
        }
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int PublishTitlesCall(GatewayHandle handle, ulong epoch, [In] byte[] json, uint length, ulong requestId, uint success);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int TitleRequestCall(GatewayHandle handle, ref GatewayTitleRequest value);
    }
}
