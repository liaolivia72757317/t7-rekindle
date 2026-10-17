using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Threading;
using System.Threading.Tasks;
using T7R.Battle;
using T7R.Core;

namespace T7R.BattleHarness
{
    internal static class RelayTests
    {
        private static readonly byte[] Empty = new byte[0];

        internal static async Task Run()
        {
            foreach (var bad in new[] {
                Frame(BattleMessageKind.GameData, BattleChannel.Login, 1, new byte[] { 1 }),
                Frame(BattleMessageKind.CloseChannel, BattleChannel.Login, 1),
                Frame(BattleMessageKind.OpenChannel, BattleChannel.Login, 1, new byte[] { 1 }),
                Frame(BattleMessageKind.OpenChannel, BattleChannel.Login, 0),
                new BattleFrame(BattleMessageKind.OpenChannel, BattleChannel.Login, 9, 2, 3, 1, Empty),
                new BattleFrame(BattleMessageKind.OpenChannel, BattleChannel.Login, 1, 9, 3, 1, Empty),
                new BattleFrame(BattleMessageKind.OpenChannel, BattleChannel.Login, 1, 2, 9, 1, Empty),
                Frame(BattleMessageKind.Admit, BattleChannel.None, 1) })
            {
                using (var pair = await Pair.Create((role, token) => throw new InvalidOperationException("Unexpected channel open")))
                {
                    await pair.Client.WriteAsync(bad, pair.Timeout.Token);
                    await ThrowsAsync<InvalidDataException>(() => pair.Running);
                }
            }
            await EchoChannels();
            await ChannelLifetime();
            using (var pair = await Pair.Create((role, token) => throw new IOException("backend offline")))
            {
                await pair.Client.WriteAsync(Frame(BattleMessageKind.OpenChannel, BattleChannel.Login, 1), pair.Timeout.Token);
                await ThrowsAsync<IOException>(() => pair.Running);
            }
            using (var pair = await Pair.Create((role, token) => throw new InvalidOperationException()))
            {
                pair.Timeout.Cancel();
                await ThrowsAsync<OperationCanceledException>(() => pair.Running);
            }
        }

        private static async Task ChannelLifetime()
        {
            var stream = new WaitingStream();
            using (var pair = await Pair.Create((role, token) => Task.FromResult<Stream>(stream)))
            {
                await pair.Client.WriteAsync(Frame(BattleMessageKind.OpenChannel, BattleChannel.Login, 1), pair.Timeout.Token);
                await pair.Client.WriteAsync(Frame(BattleMessageKind.OpenChannel, BattleChannel.Login, 2), pair.Timeout.Token);
                await ThrowsAsync<InvalidDataException>(() => pair.Running);
                Program.Check(stream.IsClosed, "duplicate open closes owned backend");
                await ThrowsAsync<InvalidOperationException>(() => pair.RunAgain());
            }
            using (var pair = await Pair.Create((role, token) => Task.FromResult<Stream>(new MemoryStream())))
            {
                await pair.Client.WriteAsync(Frame(BattleMessageKind.OpenChannel, BattleChannel.Instance, 1), pair.Timeout.Token);
                var closed = await pair.Client.ReadAsync(pair.Timeout.Token);
                Program.Check(closed.Kind == BattleMessageKind.CloseChannel && closed.Channel == BattleChannel.Instance
                    && closed.PayloadLength == 0, "backend EOF sends channel close");
                await pair.Client.WriteAsync(Frame(BattleMessageKind.CloseChannel, BattleChannel.Instance, 2), pair.Timeout.Token);
                await pair.Client.WriteAsync(Frame(BattleMessageKind.GameData, BattleChannel.Instance, 3, new byte[] { 1 }), pair.Timeout.Token);
                pair.Client.Dispose();
                await pair.Running;
            }
            stream = new WaitingStream();
            using (var pair = await Pair.Create((role, token) => Task.FromResult<Stream>(stream)))
            {
                await pair.Client.WriteAsync(Frame(BattleMessageKind.OpenChannel, BattleChannel.Logic, 1), pair.Timeout.Token);
                await pair.Client.WriteAsync(Frame(BattleMessageKind.GameData, BattleChannel.Logic, 2,
                    new byte[BattleGameRelay.MaxDataSize + 1]), pair.Timeout.Token);
                await ThrowsAsync<InvalidDataException>(() => pair.Running);
                Program.Check(stream.IsClosed, "oversize game chunk closes backend");
            }
            using (var pair = await Pair.Create((role, token) => Task.FromResult<Stream>(new FailedReader())))
            {
                await pair.Client.WriteAsync(Frame(BattleMessageKind.OpenChannel, BattleChannel.Instance, 1), pair.Timeout.Token);
                await ThrowsAsync<IOException>(() => pair.Running);
            }
        }

        private static async Task EchoChannels()
        {
            var listener = new TcpListener(IPAddress.Loopback, 0);
            listener.Start();
            try
            {
                int port = ((IPEndPoint)listener.LocalEndpoint).Port;
                using (var pair = await Pair.Create((role, token) => Connect(port, token)))
                {
                    var echoes = new List<Task>();
                    ulong sequence = 0;
                    foreach (var role in Roles())
                    {
                        await pair.Client.WriteAsync(Frame(BattleMessageKind.OpenChannel, role, ++sequence), pair.Timeout.Token);
                        echoes.Add(Echo(await listener.AcceptTcpClientAsync(), pair.Timeout.Token));
                    }
                    foreach (var role in Roles())
                        await pair.Client.WriteAsync(Frame(BattleMessageKind.GameData, role, ++sequence, new[] { (byte)role }), pair.Timeout.Token);
                    var replies = new HashSet<BattleChannel>();
                    for (ulong expected = 1; expected <= 3; ++expected)
                    {
                        var frame = await pair.Client.ReadAsync(pair.Timeout.Token);
                        Program.Check(frame.Sequence == expected && frame.RoomId == 1 && frame.MatchId == 2 && frame.PlayerId == 3,
                            "relay serializes concurrent response envelopes");
                        Program.Check(frame.Kind == BattleMessageKind.GameData && frame.GetPayload().SequenceEqual(new[] { (byte)frame.Channel }),
                            "relay preserves channel bytes");
                        replies.Add(frame.Channel);
                    }
                    Program.Check(replies.Count == 3, "three independent game channels");
                    // Replayed sequence must fail before touching the backend a second time.
                    await pair.Client.WriteAsync(Frame(BattleMessageKind.GameData, BattleChannel.Login, sequence, new byte[] { 9 }), pair.Timeout.Token);
                    await ThrowsAsync<InvalidDataException>(() => pair.Running);
                    await Task.WhenAll(echoes);
                }
            }
            finally { listener.Stop(); }
        }

        internal static async Task NativeAuth(BattleHostSnapshot snapshot)
        {
            var ports = new[] { 0, snapshot.LoginPort, snapshot.LogicPort, snapshot.InstancePort };
            using (var pair = await Pair.Create((role, token) => Connect(ports[(int)role], token)))
            {
                var auth = new byte[29]; auth[0] = 0x55; auth[1] = 0x0e; auth[2] = 3; auth[23] = 3;
                ulong sequence = 0;
                foreach (var role in Roles())
                {
                    await pair.Client.WriteAsync(Frame(BattleMessageKind.OpenChannel, role, ++sequence), pair.Timeout.Token);
                    await pair.Client.WriteAsync(Frame(BattleMessageKind.GameData, role, ++sequence, auth), pair.Timeout.Token);
                }
                var received = new HashSet<BattleChannel>();
                while (received.Count < 3)
                {
                    var response = await pair.Client.ReadAsync(pair.Timeout.Token);
                    Program.Check(response.Kind == BattleMessageKind.GameData && response.PayloadLength > 0, "relayed native AUTH response");
                    if (received.Add(response.Channel)) Program.Check(response.GetPayload()[0] == 0x55, "original TPDU reply");
                }
                foreach (var role in Roles())
                    await pair.Client.WriteAsync(Frame(BattleMessageKind.CloseChannel, role, ++sequence), pair.Timeout.Token);
                // Preserve in-flight Rust replies while completing the relay's input stream.
                pair.CompleteWrites();
                await pair.Running;
            }
        }

        private static BattleChannel[] Roles() { return new[] { BattleChannel.Login, BattleChannel.Logic, BattleChannel.Instance }; }
        private static BattleFrame Frame(BattleMessageKind kind, BattleChannel role, ulong sequence, byte[] bytes = null)
        {
            return new BattleFrame(kind, role, 1, 2, 3, sequence, bytes ?? Empty);
        }
        private static async Task<Stream> Connect(int port, CancellationToken token)
        {
            var socket = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp);
            try
            {
                using (token.Register(socket.Dispose))
                {
                    await Task.Factory.FromAsync(socket.BeginConnect, socket.EndConnect, IPAddress.Loopback, port, null);
                    token.ThrowIfCancellationRequested();
                }
                socket.NoDelay = true;
                return new NetworkStream(socket, true);
            }
            catch { socket.Dispose(); throw; }
        }
        private static async Task Echo(TcpClient client, CancellationToken token)
        {
            using (client)
            {
                var stream = client.GetStream();
                var bytes = new byte[128];
                int count;
                while ((count = await stream.ReadAsync(bytes, 0, bytes.Length, token)) != 0)
                    await stream.WriteAsync(bytes, 0, count, token);
            }
        }
        private static async Task ThrowsAsync<T>(Func<Task> action) where T : Exception
        {
            try { await action(); } catch (T) { return; }
            throw new InvalidOperationException("Expected " + typeof(T).Name);
        }

        private sealed class Pair : IDisposable
        {
            private TcpClient _client, _peer;
            private BattleGameRelay _relay;
            internal BattleConnection Client;
            internal Task Running;
            internal readonly CancellationTokenSource Timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
            internal Task RunAgain() { return _relay.RunAsync(Timeout.Token); }

            internal static async Task<Pair> Create(Func<BattleChannel, CancellationToken, Task<Stream>> open)
            {
                var pair = new Pair();
                var listener = new TcpListener(IPAddress.Loopback, 0);
                listener.Start();
                try
                {
                    pair._client = new TcpClient();
                    await pair._client.ConnectAsync(IPAddress.Loopback, ((IPEndPoint)listener.LocalEndpoint).Port);
                    pair._peer = await listener.AcceptTcpClientAsync();
                    pair.Client = BattleConnection.ForLan(pair._client.GetStream());
                    pair._relay = new BattleGameRelay(BattleConnection.ForLan(pair._peer.GetStream()), 1, 2, 3, open);
                    pair.Running = pair._relay.RunAsync(pair.Timeout.Token);
                    return pair;
                }
                catch { pair.Dispose(); throw; }
                finally { listener.Stop(); }
            }
            internal void CompleteWrites() { _client.Client.Shutdown(SocketShutdown.Send); }

            public void Dispose()
            {
                _relay?.Dispose(); Client?.Dispose(); _peer?.Dispose(); _client?.Dispose(); Timeout.Dispose();
            }
        }

        private sealed class WaitingStream : MemoryStream
        {
            private readonly TaskCompletionSource<int> _end = new TaskCompletionSource<int>(TaskCreationOptions.RunContinuationsAsynchronously);
            internal bool IsClosed { get; private set; }
            public override Task<int> ReadAsync(byte[] bytes, int offset, int count, CancellationToken cancellationToken) { return _end.Task; }
            protected override void Dispose(bool disposing)
            {
                IsClosed = true;
                _end.TrySetResult(0);
                base.Dispose(disposing);
            }
        }

        private sealed class FailedReader : MemoryStream
        {
            public override Task<int> ReadAsync(byte[] bytes, int offset, int count, CancellationToken cancellationToken)
            {
                return Task.FromException<int>(new IOException("backend read failed"));
            }
        }
    }
}
