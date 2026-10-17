using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
using System.Linq;
using System.IO;
using Newtonsoft.Json.Linq;
using T7R.Core;

namespace T7R.Desktop.Services
{
    [StructLayout(LayoutKind.Sequential, Pack = 8)]
    public struct NativeTitleRequest
    {
        public uint AbiVersion, StructSize;
        public ulong RequestId, Epoch;
        public uint TitleId, Reserved;
    }
    public interface INativeTitles
    {
        void PublishTitles(ulong epoch, string json, ulong requestId = 0, bool success = true);
        NativeTitleRequest ReadTitleRequest();
    }
    public sealed partial class NativeBridgeService : INativeTitles
    {
        internal sealed class TitlePublication
        {
            internal TitlePublication(ulong publicationId, ulong epoch, string json, ulong requestId, bool success)
            {
                PublicationId = publicationId; Epoch = epoch; Json = json; RequestId = requestId; Success = success;
            }
            internal ulong PublicationId { get; }
            internal ulong Epoch { get; }
            internal string Json { get; }
            internal ulong RequestId { get; }
            internal bool Success { get; }
        }

        internal sealed class TitlePublicationQueue
        {
            private readonly Queue<TitlePublication> _pending = new Queue<TitlePublication>();
            private ulong _nextPublicationId, _epoch;

            internal void Publish(ulong epoch, string json, ulong requestId, bool success, Action<TitlePublication> publishNative)
            {
                if (epoch < _epoch) return;
                var publication = new TitlePublication(checked(++_nextPublicationId), epoch, json, requestId, success);
                publishNative(publication);
                if (epoch != _epoch) _pending.Clear();
                _epoch = epoch;
                _pending.Enqueue(publication);
            }

            internal void Forward(NativeTitleRequest acknowledgement, Action<TitlePublication> publishGateway)
            {
                if (acknowledgement.Epoch != _epoch || !_pending.Any(value => value.PublicationId == acknowledgement.RequestId)) return;
                // Native applies an epoch's publications in FIFO order and exposes only the last acknowledgement.
                while (_pending.Count != 0 && _pending.Peek().PublicationId <= acknowledgement.RequestId)
                {
                    publishGateway(_pending.Peek());
                    _pending.Dequeue();
                }
            }

            internal void Clear() { _pending.Clear(); _epoch = 0; }
        }

        private readonly TitlePublicationQueue _pendingTitles = new TitlePublicationQueue();
        public void PublishTitles(ulong epoch, string json, ulong requestId = 0, bool success = true)
        {
            var descriptions = TitleCatalogue(json);
            lock (_sync) _pendingTitles.Publish(epoch, json, requestId, success, publication => PublishNativeTitles(publication, descriptions));
        }

        private void PublishNativeTitles(TitlePublication publication, byte[] descriptions)
        {
            var bytes = Encoding.UTF8.GetBytes(publication.Json);
            var pointer = Marshal.AllocHGlobal(bytes.Length);
            try
            {
                Marshal.Copy(bytes, 0, pointer, bytes.Length);
                var path = new NativePath { AbiVersion = NativeBridgeContract.AbiVersion,
                    StructSize = (uint)Marshal.SizeOf(typeof(NativePath)), Data = pointer, Length = (uint)bytes.Length };
                // Snapshot acknowledgements also need a unique ID; Rust retains the original request ID separately.
                var status = InvokeNative(handle => _native.PublishTitles(handle, publication.Epoch, ref path,
                    publication.PublicationId, publication.Success ? 1u : 0u,
                    descriptions, (uint)descriptions.Length));
                if (status != NativeStatus.Ok) throw new InvalidOperationException("称号同步失败：" + status);
            }
            finally { Marshal.FreeHGlobal(pointer); }
        }
        internal static byte[] TitleCatalogue(string json)
        {
            var items = (JArray)JObject.Parse(json)["titles"];
            if (items == null || items.Count > 64) throw new InvalidDataException("称号目录最多包含 64 条记录。");
            if (items.Count == 0) return Array.Empty<byte>();
            var encoding = Encoding.GetEncoding(936, EncoderFallback.ExceptionFallback, DecoderFallback.ExceptionFallback);
            var result = new byte[8 + items.Count * 164];
            using (var stream = new MemoryStream(result))
            using (var writer = new BinaryWriter(stream))
            {
                writer.Write(1u); writer.Write(items.Count);
                int previous = 0;
                foreach (var item in items.OrderBy(t => (int)t["titleId"]))
                {
                    int id = (int)item["titleId"];
                    if (id <= previous || (id != 1007 && id != 1008 && id < 1000000))
                        throw new InvalidDataException("称号 ID 重复或不在支持的范围内。");
                    var name = (string)item["name"];
                    var description = (string)item["description"];
                    var rule = (string)item["rule"];
                    if (string.IsNullOrWhiteSpace(name) || description == null || rule == null
                        || name.Any(char.IsControl) || description.Any(char.IsControl) || rule.Any(char.IsControl))
                        throw new InvalidDataException("称号名称和说明须使用纯文本。");
                    writer.Write(id);
                    WriteTitleText(writer, encoding, name, 32);
                    WriteTitleText(writer, encoding, description + "\n" + rule, 128);
                    previous = id;
                }
            }
            return result;
        }
        private static void WriteTitleText(BinaryWriter writer, Encoding encoding, string text, int capacity)
        {
            byte[] value;
            try { value = encoding.GetBytes(text); }
            catch (EncoderFallbackException error) { throw new InvalidDataException("称号含有 GBK 未收录的字符。", error); }
            if (value.Length >= capacity) throw new InvalidDataException("称号名称或说明超过客户端容量。");
            writer.Write(value); writer.Write(new byte[capacity - value.Length]);
        }
        public NativeTitleRequest ReadTitleRequest()
        {
            lock (_sync)
            {
                var request = new NativeTitleRequest { AbiVersion = NativeBridgeContract.AbiVersion,
                    StructSize = (uint)Marshal.SizeOf(typeof(NativeTitleRequest)) };
                var status = InvokeNative(handle => _native.GetTitleRequest(handle, ref request));
                if (status != NativeStatus.Ok) throw new InvalidOperationException("读取称号请求失败：" + status);
                if (_offlineBattle != null)
                    _pendingTitles.Forward(request, pending => _offlineBattle.PublishTitles(pending.Epoch, pending.Json, pending.RequestId, pending.Success));
                return _offlineBattle?.ReadTitleRequest() ?? new NativeTitleRequest { AbiVersion = 1, StructSize = 32 };
            }
        }
        private sealed partial class NativeMethods
        {
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus PublishTitlesDelegate(IntPtr session, ulong epoch, ref NativePath json, ulong requestId, uint success,
                [In] byte[] descriptions, uint descriptionLength);
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus GetTitleRequestDelegate(IntPtr session, ref NativeTitleRequest request);
            internal readonly PublishTitlesDelegate PublishTitles;
            internal readonly GetTitleRequestDelegate GetTitleRequest;
        }
    }
}
