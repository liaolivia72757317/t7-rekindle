using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using T7.Rekindle.Desktop.Services;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class UpdateDownloadTests
    {
        internal static void Run()
        {
            var root = Path.Combine(Path.GetTempPath(), "T7-update-tests-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            try { RunAsync(root).GetAwaiter().GetResult(); }
            finally { Directory.Delete(root, true); }
        }

        private static async Task RunAsync(string root)
        {
            await TestPausedStartAsync(root);
            await TestPauseDuringDownloadAsync(root, false);
            await TestPauseDuringDownloadAsync(root, true);
            var progress = new RecordedProgress();
            using (var handler = new UpdateResponseHandler((request, token) => Task.FromResult(UpdateFixtures.Bytes())))
            using (var client = new HttpClient(handler))
            {
                var path = await new UpdateDownloadService(client, root).DownloadAsync(UpdateFixtures.Asset(), progress,
                    CancellationToken.None);
                Assert(File.Exists(path) && Path.GetExtension(path) == ".exe"
                    && Convert.ToBase64String(File.ReadAllBytes(path)) == Convert.ToBase64String(UpdateFixtures.Payload),
                    "verified download bytes were not preserved");
                Assert(progress.Values.Count >= 2 && progress.Values[progress.Values.Count - 1].Percent == 100,
                    "download did not report completion");
            }
            var reads = 0;
            using (var handler = new UpdateResponseHandler((request, token) =>
            {
                if (request.RequestUri.Host == "github.com")
                    return Task.FromResult(UpdateFixtures.Bytes());
                return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
                {
                    Content = new StreamContent(new AsyncReadStream((buffer, offset, cancellation) =>
                    {
                        if (reads++ > 0) throw new IOException("connection interrupted");
                        buffer[offset] = UpdateFixtures.Payload[0];
                        return Task.FromResult(1);
                    }))
                });
            }))
            using (var client = new HttpClient(handler))
            {
                progress.Values.Clear();
                await new UpdateDownloadService(client, root).DownloadAsync(UpdateFixtures.Asset(), progress, CancellationToken.None);
                Assert(handler.RequestCount == 2 && progress.Values.Exists(value => value.Source == "GitHub" && value.BytesReceived == 0),
                    "interrupted mirror download did not restart the exact GitHub asset");
            }
            foreach (var asset in new[] { UpdateFixtures.Asset(new string('0', 64)), UpdateFixtures.Asset(size: 1),
                                         UpdateFixtures.Asset(size: UpdateFixtures.Payload.Length + 1) })
            {
                using (var handler = new UpdateResponseHandler((request, token) => Task.FromResult(UpdateFixtures.Bytes())))
                using (var client = new HttpClient(handler))
                {
                    await ExpectAsync<InvalidDataException>(() => new UpdateDownloadService(client, root)
                        .DownloadAsync(asset, progress, CancellationToken.None));
                    Assert(handler.RequestCount == 1, "integrity failure triggered network fallback");
                }
            }
            foreach (var eofOnCancellation in new[] { false, true })
            {
                var enteredRead = new TaskCompletionSource<bool>();
                using (var cancellation = new CancellationTokenSource())
                using (var handler = new UpdateResponseHandler((request, token) =>
                    Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
                    {
                        Content = new StreamContent(new AsyncReadStream(async (buffer, offset, readToken) =>
                        {
                            enteredRead.TrySetResult(true);
                            if (eofOnCancellation)
                            {
                                var eof = new TaskCompletionSource<int>();
                                using (readToken.Register(() => eof.TrySetResult(0))) return await eof.Task;
                            }
                            await Task.Delay(Timeout.Infinite, readToken);
                            return 0;
                        }))
                    })))
                using (var client = new HttpClient(handler))
                {
                    var task = new UpdateDownloadService(client, root).DownloadAsync(UpdateFixtures.Asset(), progress, cancellation.Token);
                    await enteredRead.Task;
                    cancellation.Cancel();
                    await ExpectAsync<OperationCanceledException>(() => task);
                    Assert(handler.RequestCount == 1, "user cancellation tried the fallback source");
                }
            }
            using (var handler = new UpdateResponseHandler(async (request, token) =>
            {
                if (request.RequestUri.Host == "github.com") return UpdateFixtures.Bytes();
                await Task.Delay(Timeout.Infinite, token);
                return UpdateFixtures.Bytes();
            }))
            using (var client = new HttpClient(handler))
            {
                await new UpdateDownloadService(client, root, TimeSpan.FromMilliseconds(50))
                    .DownloadAsync(UpdateFixtures.Asset(), progress, CancellationToken.None);
                Assert(handler.RequestCount == 2, "connection timeout did not try fallback");
            }
            using (var handler = new UpdateResponseHandler((request, token) => Task.FromResult(
                request.RequestUri.Host == "github.com" ? UpdateFixtures.Bytes()
                    : new HttpResponseMessage(HttpStatusCode.OK)
                    {
                        Content = new StreamContent(new AsyncReadStream(async (buffer, offset, readToken) =>
                        {
                            var eof = new TaskCompletionSource<int>();
                            using (readToken.Register(() => eof.TrySetResult(0))) return await eof.Task;
                        }))
                    })))
            using (var client = new HttpClient(handler))
            {
                await new UpdateDownloadService(client, root, TimeSpan.FromMilliseconds(50))
                    .DownloadAsync(UpdateFixtures.Asset(), progress, CancellationToken.None);
                Assert(handler.RequestCount == 2, "a stalled body reported as EOF did not try fallback");
            }
            using (var handler = new UpdateResponseHandler((request, token) =>
            {
                var response = new HttpResponseMessage(HttpStatusCode.Redirect);
                response.Headers.Location = new Uri("http://updates.example.com/file.exe");
                return Task.FromResult(response);
            }))
            using (var client = new HttpClient(handler))
                await ExpectAsync<InvalidDataException>(() => new UpdateDownloadService(client, root)
                    .DownloadAsync(UpdateFixtures.Asset(), progress, CancellationToken.None));

            var blockedRoot = Path.Combine(root, "not-a-directory");
            File.WriteAllText(blockedRoot, "fixture");
            using (var handler = new UpdateResponseHandler((request, token) => Task.FromResult(UpdateFixtures.Bytes())))
            using (var client = new HttpClient(handler))
            {
                await ExpectAsync<IOException>(() => new UpdateDownloadService(client, blockedRoot)
                    .DownloadAsync(UpdateFixtures.Asset(), progress, CancellationToken.None));
                Assert(handler.RequestCount == 0, "local storage error was treated as a network error");
            }
            Assert(Directory.GetFiles(root, "*.part", SearchOption.AllDirectories).Length == 0,
                "failed or cancelled download left a partial installer");
        }

        private static async Task TestPausedStartAsync(string root)
        {
            var control = new UpdateDownloadControl();
            control.Pause();
            using (var handler = new UpdateResponseHandler((request, token) => Task.FromResult(UpdateFixtures.Bytes())))
            using (var client = new HttpClient(handler))
            {
                var task = new UpdateDownloadService(client, root).DownloadAsync(UpdateFixtures.Asset(), null,
                    CancellationToken.None, control);
                Assert(!task.IsCompleted && handler.RequestCount == 0, "paused download opened a connection");
                control.Resume();
                var path = await task;
                Assert(File.Exists(path) && handler.RequestCount == 1, "resuming a paused download did not start exactly once");
            }
        }

        private static async Task TestPauseDuringDownloadAsync(string root, bool cancel)
        {
            var control = new UpdateDownloadControl();
            var enteredSecondRead = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var finishSecondRead = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var reads = 0;
            var progress = new RecordedProgress();
            using (var cancellation = new CancellationTokenSource())
            using (var handler = new UpdateResponseHandler((request, token) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StreamContent(new AsyncReadStream(async (buffer, offset, readToken) =>
                {
                    var read = Interlocked.Increment(ref reads);
                    if (read == 1) { buffer[offset] = UpdateFixtures.Payload[0]; return 1; }
                    if (read != 2) return 0;
                    enteredSecondRead.TrySetResult(true);
                    await finishSecondRead.Task;
                    Buffer.BlockCopy(UpdateFixtures.Payload, 1, buffer, offset, UpdateFixtures.Payload.Length - 1);
                    return UpdateFixtures.Payload.Length - 1;
                }))
            })))
            using (var client = new HttpClient(handler))
            {
                var task = new UpdateDownloadService(client, root, TimeSpan.FromMilliseconds(100))
                    .DownloadAsync(UpdateFixtures.Asset(), progress, cancellation.Token, control);
                await enteredSecondRead.Task;
                control.Pause();
                finishSecondRead.TrySetResult(true);
                await Task.Delay(250);
                Assert(!task.IsCompleted && reads == 2 && handler.RequestCount == 1
                    && progress.Values[progress.Values.Count - 1].Percent < 100,
                    "paused download kept reading, completed or counted pause time as a network timeout");
                if (cancel)
                {
                    cancellation.Cancel();
                    await ExpectAsync<OperationCanceledException>(() => task);
                    Assert(handler.RequestCount == 1, "cancelling a paused download tried fallback");
                }
                else
                {
                    control.Resume();
                    var path = await task;
                    Assert(handler.RequestCount == 1 && reads == 3
                        && Convert.ToBase64String(File.ReadAllBytes(path)) == Convert.ToBase64String(UpdateFixtures.Payload),
                        "resuming a download restarted it or lost previously received bytes");
                }
                Assert(Directory.GetFiles(root, "*.part", SearchOption.AllDirectories).Length == 0,
                    "paused download left a partial installer after completion or cancellation");
            }
        }

        internal static async Task ExpectAsync<T>(Func<Task> action) where T : Exception
        {
            try { await action(); }
            catch (T) { return; }
            throw new InvalidOperationException("expected " + typeof(T).Name);
        }

        private sealed class RecordedProgress : IProgress<UpdateDownloadProgress>
        {
            internal List<UpdateDownloadProgress> Values { get; } = new List<UpdateDownloadProgress>();
            public void Report(UpdateDownloadProgress value) => Values.Add(value);
        }

        private sealed class AsyncReadStream : Stream
        {
            private readonly Func<byte[], int, CancellationToken, Task<int>> _read;
            internal AsyncReadStream(Func<byte[], int, CancellationToken, Task<int>> read) => _read = read;
            public override bool CanRead => true;
            public override bool CanSeek => false;
            public override bool CanWrite => false;
            public override long Length => throw new NotSupportedException();
            public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }
            public override void Flush() { }
            public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();
            public override Task<int> ReadAsync(byte[] buffer, int offset, int count, CancellationToken token) => _read(buffer, offset, token);
            public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
            public override void SetLength(long value) => throw new NotSupportedException();
            public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        }
    }
}
