using System;
using System.IO;
using System.Net.Http;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;

namespace T7.Rekindle.Desktop.Services
{
    internal sealed class UpdateDownloadProgress
    {
        internal UpdateDownloadProgress(long received, long total, string source, string notice)
        {
            BytesReceived = received;
            TotalBytes = total;
            Source = source;
            Notice = notice;
        }
        public long BytesReceived { get; }
        public long TotalBytes { get; }
        public string Source { get; }
        public string Notice { get; }
        public double Percent => TotalBytes > 0 ? 100.0 * BytesReceived / TotalBytes : 0;
    }

    internal sealed class UpdateDownloadService
    {
        private const string INSTALLER_FILENAME = "T7-Rekindle-Setup.exe";
        private readonly HttpClient _client;
        private readonly string _directory;
        private readonly TimeSpan _networkTimeout;

        internal UpdateDownloadService(HttpClient client, string directory, TimeSpan? networkTimeout = null)
        {
            _client = client ?? throw new ArgumentNullException(nameof(client));
            _directory = directory ?? throw new ArgumentNullException(nameof(directory));
            _networkTimeout = networkTimeout ?? TimeSpan.FromSeconds(30);
        }

        internal async Task<string> DownloadAsync(LauncherUpdateAsset asset, IProgress<UpdateDownloadProgress> progress,
            CancellationToken cancellation, UpdateDownloadControl control = null)
        {
            if (asset == null) throw new ArgumentNullException(nameof(asset));
            cancellation.ThrowIfCancellationRequested();
            control = control ?? new UpdateDownloadControl();
            await control.WaitWhilePausedAsync(cancellation).ConfigureAwait(false);
            var directory = Path.Combine(_directory, Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            var partial = Path.Combine(directory, INSTALLER_FILENAME + ".part");
            var completed = false;
            try
            {
                try
                {
                    await DownloadFromAsync(asset.DownloadAddress, asset.Source, string.Empty, asset, partial,
                        progress, cancellation, control).ConfigureAwait(false);
                }
                catch (UpdateNetworkException error) when (asset.FallbackAddress != null && !cancellation.IsCancellationRequested)
                {
                    File.Delete(partial);
                    await DownloadFromAsync(asset.FallbackAddress, "GitHub", "R2 下载失败，已切换到 GitHub：" + error.Message,
                        asset, partial, progress, cancellation, control).ConfigureAwait(false);
                }
                cancellation.ThrowIfCancellationRequested();
                var destination = Path.Combine(directory, INSTALLER_FILENAME);
                File.Move(partial, destination);
                completed = true;
                return destination;
            }
            finally
            {
                if (!completed)
                {
                    File.Delete(partial);
                    Directory.Delete(directory);
                }
            }
        }

        private async Task DownloadFromAsync(string address, string source, string notice, LauncherUpdateAsset asset,
            string partial, IProgress<UpdateDownloadProgress> progress, CancellationToken cancellation, UpdateDownloadControl control)
        {
            await control.WaitWhilePausedAsync(cancellation).ConfigureAwait(false);
            progress?.Report(new UpdateDownloadProgress(0, asset.Size, source, notice));
            using (var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellation))
            using (var response = await GetResponseAsync(address, timeout, cancellation).ConfigureAwait(false))
            using (timeout.Token.Register(response.Dispose))
            {
                var length = response.Content.Headers.ContentLength;
                if (length.HasValue && length.Value != asset.Size)
                    throw new InvalidDataException("安装包大小与更新清单不一致，请重新检查更新。");
                using (var input = await ReadNetworkAsync(() => response.Content.ReadAsStreamAsync(), timeout, cancellation)
                    .ConfigureAwait(false))
                using (var output = new FileStream(partial, FileMode.CreateNew, FileAccess.Write, FileShare.None, 65536, true))
                using (var hash = SHA256.Create())
                {
                    var buffer = new byte[65536];
                    long received = 0;
                    var lastReport = DateTime.UtcNow;
                    while (true)
                    {
                        await control.WaitWhilePausedAsync(cancellation).ConfigureAwait(false);
                        var read = await ReadNetworkAsync(() => input.ReadAsync(buffer, 0, buffer.Length, timeout.Token),
                            timeout, cancellation).ConfigureAwait(false);
                        await control.WaitWhilePausedAsync(cancellation).ConfigureAwait(false);
                        cancellation.ThrowIfCancellationRequested();
                        if (timeout.IsCancellationRequested)
                            throw new UpdateNetworkException("下载数据读取超时。");
                        if (read == 0) break;
                        if (read > asset.Size - received)
                            throw new InvalidDataException("安装包超过清单声明的大小，下载已停止。");
                        await output.WriteAsync(buffer, 0, read, cancellation).ConfigureAwait(false);
                        hash.TransformBlock(buffer, 0, read, null, 0);
                        received += read;
                        if (received == asset.Size || (DateTime.UtcNow - lastReport).TotalMilliseconds >= 100)
                        {
                            progress?.Report(new UpdateDownloadProgress(received, asset.Size, source, notice));
                            lastReport = DateTime.UtcNow;
                        }
                    }
                    hash.TransformFinalBlock(new byte[0], 0, 0);
                    var actual = BitConverter.ToString(hash.Hash).Replace("-", "").ToLowerInvariant();
                    if (received != asset.Size || actual != asset.Sha256)
                        throw new InvalidDataException("安装包大小或 SHA-256 校验失败，文件已丢弃。请重新检查更新。");
                    await output.FlushAsync(cancellation).ConfigureAwait(false);
                }
            }
        }

        private async Task<HttpResponseMessage> GetResponseAsync(string address, CancellationTokenSource timeout,
            CancellationToken cancellation)
        {
            var current = ReleaseMetadata.HttpsAddress(address);
            for (var redirects = 0; redirects <= 5; redirects++)
            {
                using (var request = new HttpRequestMessage(HttpMethod.Get, current))
                {
                    request.Headers.UserAgent.ParseAdd("T7-Rekindle-Updater/1.0");
                    request.Headers.AcceptEncoding.ParseAdd("identity");
                    var response = await ReadNetworkAsync(() => _client.SendAsync(request,
                        HttpCompletionOption.ResponseHeadersRead, timeout.Token), timeout, cancellation).ConfigureAwait(false);
                    var status = (int)response.StatusCode;
                    if (status == 301 || status == 302 || status == 303 || status == 307 || status == 308)
                    {
                        using (response)
                        {
                            if (response.Headers.Location == null || redirects == 5)
                                throw new UpdateNetworkException("下载地址重定向异常。");
                            current = ReleaseMetadata.HttpsAddress(new Uri(current, response.Headers.Location).AbsoluteUri);
                        }
                        continue;
                    }
                    if (status != 200)
                    {
                        response.Dispose();
                        throw new UpdateNetworkException("下载服务返回 HTTP " + status + "。");
                    }
                    return response;
                }
            }
            throw new UpdateNetworkException("下载地址重定向异常。");
        }

        private async Task<T> ReadNetworkAsync<T>(Func<Task<T>> operation, CancellationTokenSource timeout,
            CancellationToken cancellation)
        {
            cancellation.ThrowIfCancellationRequested();
            timeout.CancelAfter(_networkTimeout);
            try { return await operation().ConfigureAwait(false); }
            catch (Exception error) when (error is HttpRequestException || error is IOException
                || error is OperationCanceledException || error is ObjectDisposedException)
            {
                if (cancellation.IsCancellationRequested) throw new OperationCanceledException(cancellation);
                throw new UpdateNetworkException(timeout.IsCancellationRequested ? "下载连接或数据读取超时。" : "下载连接中断。", error);
            }
            finally { timeout.CancelAfter(Timeout.InfiniteTimeSpan); }
        }

        private sealed class UpdateNetworkException : IOException
        {
            internal UpdateNetworkException(string message, Exception inner = null) : base(message, inner) { }
        }
    }
}
