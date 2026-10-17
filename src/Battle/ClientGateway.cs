using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using T7R.Core;

namespace T7R.Battle
{
    public sealed class ClientGatewaySnapshot
    {
        internal ClientGatewaySnapshot(GatewayNativeSnapshot value)
        {
            Epoch = value.Epoch; IsRunning = value.State == 1; HasFailed = value.State == 2;
            LoginPort = value.LoginPort; LogicPort = value.LogicPort; InstancePort = value.InstancePort;
            ActiveConnections = value.ActiveConnections; PendingRequests = value.PendingRequests;
            ConnectionErrors = value.ConnectionErrors;
            RulesVersion = value.RulesVersion; ResourceVersion = value.ResourceVersion; MapId = value.MapId;
        }
        public ulong Epoch { get; }
        public bool IsRunning { get; }
        public bool HasFailed { get; }
        public ushort LoginPort { get; }
        public ushort LogicPort { get; }
        public ushort InstancePort { get; }
        public uint ActiveConnections { get; }
        public uint PendingRequests { get; }
        public uint ConnectionErrors { get; }
        public uint RulesVersion { get; }
        public uint ResourceVersion { get; }
        public uint MapId { get; }
    }

    // Runs inside the launcher. Native code owns sockets; this pump does no game
    // wire encoding, account login, or implicit online/offline fallback.
    public sealed partial class ClientGateway : IDisposable
    {
        private readonly ClientGatewayNative _native;
        private readonly IPlatformBackend _backend;
        private readonly SemaphoreSlim _lifecycle = new SemaphoreSlim(1, 1);
        private CancellationTokenSource _cancellation;
        private Task _worker;
        private bool _disposed;
        private string _lastBackendError = string.Empty;

        public ClientGateway(string nativeLibraryPath, byte[] resources, IPlatformBackend backend)
        {
            _backend = backend ?? throw new ArgumentNullException(nameof(backend));
            _native = new ClientGatewayNative(nativeLibraryPath, resources);
        }
        private ClientGateway(string nativeLibraryPath, LocalResourceOptions resources, IPlatformBackend backend)
        {
            _backend = backend;
            _native = new ClientGatewayNative(nativeLibraryPath, resources);
        }
        private ClientGateway(ClientGatewayNative native, IPlatformBackend backend)
        {
            _native = native ?? throw new ArgumentNullException(nameof(native));
            _backend = backend ?? throw new ArgumentNullException(nameof(backend));
        }
        public static Task<ClientGateway> CreateLocalAsync(string nativeLibraryPath, LocalResourceOptions resources,
            IPlatformBackend backend)
        {
            if (resources == null) throw new ArgumentNullException(nameof(resources));
            if (backend == null) throw new ArgumentNullException(nameof(backend));
            return Task.Run(() => new ClientGateway(nativeLibraryPath, resources, backend));
        }
        public static Task ValidateLocalResourcesAsync(string nativeLibraryPath, LocalResourceOptions resources)
        {
            if (resources == null) throw new ArgumentNullException(nameof(resources));
            return Task.Run(() =>
            {
                // Construction validates resources without starting listeners or loading a profile.
                using (new ClientGatewayNative(nativeLibraryPath, resources)) { }
            });
        }
        public static Task<ClientGateway> CreateLocalProfiledAsync(string nativeLibraryPath,
            LocalResourceOptions resources, string playerName, string offlineProfilePath)
        {
            if (resources == null) throw new ArgumentNullException(nameof(resources));
            return Task.Run(() =>
            {
                var native = new ClientGatewayNative(nativeLibraryPath, resources);
                try
                {
                    var backend = new LocalPlatformBackend(playerName, native.ResourceBundleJson(), offlineProfilePath);
                    return new ClientGateway(native, backend);
                }
                catch { native.Dispose(); throw; }
            });
        }
        public bool WasResourceCacheHit => _native.WasResourceCacheHit;
        public ClientGatewaySnapshot GetSnapshot() => new ClientGatewaySnapshot(_native.Snapshot());
        public string GetError() => _native.LastError();
        public string LastBackendError => Volatile.Read(ref _lastBackendError);
        public Task Completion => _worker ?? Task.CompletedTask;

        public async Task StartAsync(ulong epoch)
        {
            await _lifecycle.WaitAsync().ConfigureAwait(false);
            try
            {
                EnsureOpen();
                if (_worker != null) throw new InvalidOperationException("Gateway is already started.");
                await Task.Run(() => _native.Start(epoch)).ConfigureAwait(false);
                _cancellation = new CancellationTokenSource();
                Volatile.Write(ref _lastBackendError, string.Empty);
                var cancellation = _cancellation.Token;
                _worker = Task.Run(() => PumpAsync(epoch, cancellation));
            }
            finally { _lifecycle.Release(); }
        }

        public async Task StopAsync()
        {
            await _lifecycle.WaitAsync().ConfigureAwait(false);
            try { EnsureOpen(); await StopCoreAsync().ConfigureAwait(false); }
            finally { _lifecycle.Release(); }
        }

        private async Task StopCoreAsync()
        {
            if (_worker == null) { _native.Stop(); _battleActive = false; return; }
            _cancellation.Cancel();
            try
            {
                try { _native.Stop(); }
                finally { await _worker.ConfigureAwait(false); }
            }
            finally { _cancellation.Dispose(); _cancellation = null; _worker = null; _battleActive = false; }
        }

        private async Task PumpAsync(ulong epoch, CancellationToken cancellation)
        {
            using (var lifetime = CancellationTokenSource.CreateLinkedTokenSource(cancellation))
            {
                var pending = new List<Task>();
                try
                {
                    while (true)
                    {
                        cancellation.ThrowIfCancellationRequested();
                        for (int batch = 0; batch < 32 && _battleActive; ++batch)
                            if (!_native.TryDispatchBattleOutput()) break;
                        foreach (var completed in pending.Where(task => task.IsCompleted).ToArray())
                        {
                            pending.Remove(completed);
                            await completed.ConfigureAwait(false);
                        }
                        var state = _native.Snapshot();
                        cancellation.ThrowIfCancellationRequested();
                        if (state.State != 1) throw new InvalidOperationException("Rust gateway worker stopped: " + _native.LastError());
                        if (pending.Count == 32) { await Task.WhenAny(pending).ConfigureAwait(false); continue; }
                        var bytes = _native.ReadRequest();
                        if (bytes == null) { await Task.Delay(10, cancellation).ConfigureAwait(false); continue; }
                        pending.Add(CompleteRequestAsync(bytes, epoch, lifetime.Token));
                    }
                }
                catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
                finally
                {
                    try
                    {
                        lifetime.Cancel();
                        try { await Task.WhenAll(pending).ConfigureAwait(false); }
                        catch (OperationCanceledException) when (lifetime.IsCancellationRequested) { }
                    }
                    finally { _native.Stop(); }
                }
            }
        }

        public void Dispose()
        {
            _lifecycle.Wait();
            try
            {
                if (_disposed) return;
                try { StopCoreAsync().GetAwaiter().GetResult(); }
                finally { _disposed = true; _native.Dispose(); }
            }
            finally { _lifecycle.Release(); }
        }
        private void EnsureOpen() { if (_disposed) throw new ObjectDisposedException(nameof(ClientGateway)); }
    }
}
