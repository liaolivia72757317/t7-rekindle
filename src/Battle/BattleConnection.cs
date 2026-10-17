using System;
using System.IO;
using System.Net.Security;
using System.Threading;
using System.Threading.Tasks;
using T7R.Core;

namespace T7R.Battle
{
    // One reader, ordered writers. Cancelling a partial frame closes the stream;
    // continuing on it would reinterpret body bytes as the next length prefix.
    public sealed class BattleConnection : IDisposable
    {
        public const int MaxPendingWrites = 16;
        public const long MaxPendingBytes = 8 * 1024 * 1024;
        private readonly Stream _stream;
        private readonly SemaphoreSlim _writer = new SemaphoreSlim(1, 1);
        private int _disposed, _reading, _pendingWrites;
        private long _pendingBytes;

        private BattleConnection(Stream stream)
        {
            _stream = stream ?? throw new ArgumentNullException(nameof(stream));
            if (!stream.CanRead || !stream.CanWrite) throw new ArgumentException("A duplex stream is required.", nameof(stream));
        }

        public static BattleConnection ForLan(Stream stream) { return new BattleConnection(stream); }

        public static BattleConnection ForOnline(SslStream stream)
        {
            if (stream == null) throw new ArgumentNullException(nameof(stream));
            if (!stream.IsAuthenticated || !stream.IsEncrypted || !stream.IsSigned)
                throw new ArgumentException("Online battle transport requires authenticated TLS.", nameof(stream));
            return new BattleConnection(stream);
        }

        public async Task<BattleFrame> ReadAsync(CancellationToken cancellationToken)
        {
            EnsureOpen();
            if (Interlocked.CompareExchange(ref _reading, 1, 0) != 0)
                throw new InvalidOperationException("Only one battle reader is allowed.");
            try
            {
                using (cancellationToken.Register(Dispose))
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    var prefix = new byte[4];
                    if (!await ReadExactlyAsync(prefix, true, cancellationToken).ConfigureAwait(false))
                    {
                        Dispose();
                        return null;
                    }
                    var body = new byte[BattleFrameCodec.DecodeLength(prefix)];
                    await ReadExactlyAsync(body, false, cancellationToken).ConfigureAwait(false);
                    return BattleFrameCodec.DecodeBody(body);
                }
            }
            catch { Dispose(); cancellationToken.ThrowIfCancellationRequested(); throw; }
            finally { Volatile.Write(ref _reading, 0); }
        }

        public async Task WriteAsync(BattleFrame frame, CancellationToken cancellationToken)
        {
            if (frame == null) throw new ArgumentNullException(nameof(frame));
            EnsureOpen();
            long size = 4L + BattleFrame.HeaderSize + frame.PayloadLength;
            var count = Interlocked.Increment(ref _pendingWrites);
            var bytes = Interlocked.Add(ref _pendingBytes, size);
            bool entered = false;
            try
            {
                if (count > MaxPendingWrites || bytes > MaxPendingBytes)
                    throw new IOException("Battle send queue is full.");
                // Cancellation while waiting has not touched the stream yet.
                await _writer.WaitAsync(cancellationToken).ConfigureAwait(false);
                entered = true;
                EnsureOpen();
                using (cancellationToken.Register(Dispose))
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    var encoded = BattleFrameCodec.Encode(frame);
                    await _stream.WriteAsync(encoded, 0, encoded.Length, cancellationToken).ConfigureAwait(false);
                }
            }
            catch
            {
                if (entered) Dispose();
                cancellationToken.ThrowIfCancellationRequested();
                throw;
            }
            finally
            {
                if (entered) _writer.Release();
                Interlocked.Decrement(ref _pendingWrites);
                Interlocked.Add(ref _pendingBytes, -size);
            }
        }

        public void Dispose()
        {
            if (Interlocked.Exchange(ref _disposed, 1) == 0) _stream.Dispose();
            // No WaitHandle is allocated; disposing the semaphore during an
            // in-flight writer would prevent its finally block from releasing.
        }

        private async Task<bool> ReadExactlyAsync(byte[] bytes, bool allowEof, CancellationToken cancellationToken)
        {
            int offset = 0;
            while (offset < bytes.Length)
            {
                int count = await _stream.ReadAsync(bytes, offset, bytes.Length - offset, cancellationToken).ConfigureAwait(false);
                if (count == 0)
                {
                    if (allowEof && offset == 0) return false;
                    throw new EndOfStreamException("Truncated battle frame.");
                }
                offset += count;
            }
            return true;
        }

        private void EnsureOpen()
        {
            if (Volatile.Read(ref _disposed) != 0) throw new ObjectDisposedException(nameof(BattleConnection));
        }
    }
}
