using System;
using System.IO;
using System.Linq;
using System.Net.Security;
using System.Net;
using System.Net.Sockets;
using System.Threading;
using System.Threading.Tasks;
using T7R.Battle;
using T7R.Core;

namespace T7R.BattleHarness
{
    internal static class FrameTests
    {
        internal static async Task Run()
        {
            var payload = new byte[] { 11, 22, 33 };
            var frame = new BattleFrame(BattleMessageKind.GameData, BattleChannel.Instance,
                0x0102030405060708, 2, 3, ulong.MaxValue, payload);
            payload[0] = 99;
            Program.Check(frame.GetPayload()[0] == 11, "payload ownership");
            var encoded = BattleFrameCodec.Encode(frame);
            Program.Check(encoded[3] == 39 && encoded[8] == 1 && encoded[15] == 8, "big-endian format");
            var decoded = BattleFrameCodec.DecodeBody(encoded.Skip(4).ToArray());
            Program.Check(decoded.RoomId == frame.RoomId && decoded.Sequence == ulong.MaxValue
                && decoded.Channel == BattleChannel.Instance && decoded.GetPayload().SequenceEqual(frame.GetPayload()), "frame round trip");
            var copy = decoded.GetPayload(); copy[0] = 99;
            Program.Check(decoded.GetPayload()[0] == 11, "payload read ownership");
            Program.Throws<InvalidDataException>(() => BattleFrameCodec.DecodeLength(new byte[] { 255, 255, 255, 255 }));
            Program.Throws<InvalidDataException>(() => BattleFrameCodec.DecodeLength(new byte[4]));
            Program.Throws<ArgumentException>(() => BattleFrameCodec.DecodeLength(new byte[3]));
            foreach (int index in new[] { 0, 1, 2, 3 })
            {
                var invalid = encoded.Skip(4).ToArray(); invalid[index] = 255;
                Program.Throws<InvalidDataException>(() => BattleFrameCodec.DecodeBody(invalid));
            }
            Program.Throws<ArgumentException>(() => new BattleFrame(BattleMessageKind.GameData, BattleChannel.None, 1, 1, 1, 1, payload));
            Program.Throws<ArgumentOutOfRangeException>(() => new BattleFrame(BattleMessageKind.WorldSample, BattleChannel.None,
                1, 1, 1, 1, new byte[BattleFrame.MaxFrameSize]));
            using (var ssl = new SslStream(new MemoryStream()))
                Program.Throws<ArgumentException>(() => BattleConnection.ForOnline(ssl));

            using (var stream = new FragmentedStream(encoded.Concat(encoded).ToArray()))
            using (var connection = BattleConnection.ForLan(stream))
            {
                Program.Check((await connection.ReadAsync(CancellationToken.None)).Sequence == frame.Sequence, "fragmented read");
                Program.Check((await connection.ReadAsync(CancellationToken.None)).PlayerId == 3, "coalesced second frame");
                Program.Check(await connection.ReadAsync(CancellationToken.None) == null, "clean EOF");
            }
            foreach (int length in new[] { 1, 3, 4, encoded.Length - 1 })
            using (var connection = BattleConnection.ForLan(new FragmentedStream(encoded.Take(length).ToArray())))
            {
                await ThrowsAsync<EndOfStreamException>(() => connection.ReadAsync(CancellationToken.None));
                await ThrowsAsync<ObjectDisposedException>(() => connection.ReadAsync(CancellationToken.None));
            }
            using (var stream = new MemoryStream())
            using (var connection = BattleConnection.ForLan(stream))
            {
                await Task.WhenAll(Enumerable.Range(0, 8).Select(_ => connection.WriteAsync(frame, CancellationToken.None)));
                Program.Check(stream.Length == encoded.Length * 8L, "serialized writes");
                stream.Position = 0;
                for (int i = 0; i < 8; ++i) Program.Check((await connection.ReadAsync(CancellationToken.None)).Sequence == frame.Sequence, "uninterleaved write");
            }
            using (var connection = BattleConnection.ForLan(new MemoryStream()))
            using (var cancellation = new CancellationTokenSource())
            {
                cancellation.Cancel();
                await ThrowsAsync<OperationCanceledException>(() => connection.ReadAsync(cancellation.Token));
                await ThrowsAsync<ObjectDisposedException>(() => connection.ReadAsync(CancellationToken.None));
            }
            await SendLimits(frame);
            await TcpRoundTrip(frame);
        }

        private static async Task SendLimits(BattleFrame frame)
        {
            using (var stream = new GatedStream())
            using (var connection = BattleConnection.ForLan(stream))
            {
                var pending = Enumerable.Range(0, BattleConnection.MaxPendingWrites)
                    .Select(_ => connection.WriteAsync(frame, CancellationToken.None)).ToArray();
                await ThrowsAsync<IOException>(() => connection.WriteAsync(frame, CancellationToken.None));
                stream.Release();
                await Task.WhenAll(pending);
                await connection.WriteAsync(frame, CancellationToken.None);
            }
            using (var stream = new GatedStream())
            using (var connection = BattleConnection.ForLan(stream))
            using (var cancelled = new CancellationTokenSource())
            {
                var first = connection.WriteAsync(frame, CancellationToken.None);
                var second = connection.WriteAsync(frame, cancelled.Token);
                cancelled.Cancel();
                await ThrowsAsync<OperationCanceledException>(() => second);
                stream.Release();
                await first;
                await connection.WriteAsync(frame, CancellationToken.None);
            }
            var large = new BattleFrame(BattleMessageKind.WorldSample, BattleChannel.None, 1, 2, 3, 4,
                new byte[BattleFrame.MaxFrameSize - BattleFrame.HeaderSize]);
            using (var stream = new GatedStream())
            using (var connection = BattleConnection.ForLan(stream))
            {
                var first = connection.WriteAsync(large, CancellationToken.None);
                await ThrowsAsync<IOException>(() => connection.WriteAsync(large, CancellationToken.None));
                stream.Release();
                await first;
            }
            using (var stream = new GatedStream())
            using (var connection = BattleConnection.ForLan(stream))
            using (var cancelled = new CancellationTokenSource())
            {
                var first = connection.WriteAsync(frame, cancelled.Token);
                cancelled.Cancel();
                await ThrowsAsync<OperationCanceledException>(() => first);
                await ThrowsAsync<ObjectDisposedException>(() => connection.WriteAsync(frame, CancellationToken.None));
            }
        }

        private static async Task TcpRoundTrip(BattleFrame frame)
        {
            var listener = new TcpListener(IPAddress.Loopback, 0);
            listener.Start();
            try
            {
                using (var client = new TcpClient())
                using (var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5)))
                {
                    await client.ConnectAsync(IPAddress.Loopback, ((IPEndPoint)listener.LocalEndpoint).Port);
                    using (var peer = await listener.AcceptTcpClientAsync())
                    using (var local = BattleConnection.ForLan(client.GetStream()))
                    using (var remote = BattleConnection.ForLan(peer.GetStream()))
                    {
                        var receiving = remote.ReadAsync(timeout.Token);
                        await local.WriteAsync(frame, timeout.Token);
                        var received = await receiving;
                        Program.Check(received.RoomId == frame.RoomId && received.MatchId == frame.MatchId, "TCP match envelope");
                        await remote.WriteAsync(received, timeout.Token);
                        Program.Check((await local.ReadAsync(timeout.Token)).GetPayload().SequenceEqual(frame.GetPayload()), "TCP round trip");
                    }
                }
            }
            finally { listener.Stop(); }
        }

        private static async Task ThrowsAsync<T>(Func<Task> action) where T : Exception
        {
            try { await action(); } catch (T) { return; }
            throw new InvalidOperationException("Expected " + typeof(T).Name);
        }

        private sealed class FragmentedStream : MemoryStream
        {
            public FragmentedStream(byte[] bytes) : base(bytes) { }
            public override Task<int> ReadAsync(byte[] bytes, int offset, int count, CancellationToken cancellationToken)
            {
                return base.ReadAsync(bytes, offset, Math.Min(2, count), cancellationToken);
            }
        }

        private sealed class GatedStream : MemoryStream
        {
            private readonly TaskCompletionSource<bool> _gate = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            public void Release() { _gate.TrySetResult(true); }
            public override async Task WriteAsync(byte[] bytes, int offset, int count, CancellationToken cancellationToken)
            {
                await _gate.Task;
                await base.WriteAsync(bytes, offset, count, cancellationToken);
            }
            protected override void Dispose(bool disposing)
            {
                if (disposing) _gate.TrySetException(new ObjectDisposedException(nameof(GatedStream)));
                base.Dispose(disposing);
            }
        }
    }
}
