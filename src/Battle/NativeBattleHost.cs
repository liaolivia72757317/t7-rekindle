using System;
using System.IO;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using T7R.Core;

namespace T7R.Battle
{
    // Headless host of the same Rust gateway used by the desktop session.
    public sealed class NativeBattleHost : IDisposable
    {
        private static long _nextEpoch = DateTime.UtcNow.Ticks;
        private readonly object _sync = new object();
        private BattleRearmCoordinator _rearm = new BattleRearmCoordinator();
        private readonly string _library, _clientDirectory, _writableRoot;
        private ClientGateway _gateway;
        private CancellationTokenSource _cancellation;
        private Task _monitor;
        private string _failure = string.Empty;
        private bool _disposed;

        public NativeBattleHost(string packageRoot, string clientDirectory, string writableRoot)
        {
            ValidatePath(packageRoot, nameof(packageRoot));
            ValidatePath(clientDirectory, nameof(clientDirectory));
            ValidatePath(writableRoot, nameof(writableRoot));
            _library = Path.Combine(packageRoot, "t7r_battle_ffi.dll");
            _clientDirectory = clientDirectory;
            _writableRoot = writableRoot;
        }

        public void Start()
        {
            lock (_sync)
            {
                EnsureOpen();
                if (_gateway != null) throw new InvalidOperationException("Battle host is already started.");
                _failure = string.Empty;
                _rearm = new BattleRearmCoordinator();
                try
                {
                    var directory = new DirectoryInfo(_clientDirectory);
                    var root = directory.Name.Equals("Bin", StringComparison.OrdinalIgnoreCase) ? directory.Parent.FullName : directory.FullName;
                    string version;
                    using (var reader = new StreamReader(Path.Combine(root, "update.lst"), new UTF8Encoding(false, true)))
                        version = reader.ReadLine()?.Trim();
                    var resources = new LocalResourceOptions(_clientDirectory, Path.Combine(_writableRoot, "battle-cache"), 1, 1, version);
                    _gateway = ClientGateway.CreateLocalProfiledAsync(_library, resources, PlayerNameRules.DefaultName,
                        Path.Combine(_writableRoot, "player-profile.json")).GetAwaiter().GetResult();
                    var epoch = NextEpoch();
                    _gateway.StartAsync(epoch).GetAwaiter().GetResult();
                    _gateway.StartLocalBattleAsync(epoch, 1, unchecked((uint)epoch)).GetAwaiter().GetResult();
                    _cancellation = new CancellationTokenSource();
                    var gateway = _gateway;
                    var token = _cancellation.Token;
                    gateway.SetProfileChangedHandler(cancellation => RearmAsync(gateway, false, cancellation));
                    _monitor = Task.Run(() => MonitorAsync(gateway, token));
                }
                catch (Exception error)
                {
                    _failure = error.Message;
                    try { StopCore(); }
                    catch (Exception cleanup) { throw new AggregateException(error, cleanup); }
                    throw new InvalidOperationException("Rust battle host startup failed: " + error.Message, error);
                }
            }
        }

        private static ulong NextEpoch() => checked((ulong)Interlocked.Increment(ref _nextEpoch));

        private async Task MonitorAsync(ClientGateway gateway, CancellationToken cancellation)
        {
            while (true)
            {
                await Task.Delay(100, cancellation).ConfigureAwait(false);
                if (gateway.Completion.IsCompleted) await gateway.Completion.ConfigureAwait(false);
                await RearmAsync(gateway, true, cancellation).ConfigureAwait(false);
            }
        }

        private Task RearmAsync(ClientGateway gateway, bool returned, CancellationToken cancellation) =>
            _rearm.TryRearmAsync(
                () => ReferenceEquals(_gateway, gateway)
                    && (returned ? gateway.BattleReturnedToRoom() : gateway.TryReserveBattleRearm()),
                async token =>
                {
                    await gateway.StopBattleAsync().ConfigureAwait(false);
                    token.ThrowIfCancellationRequested();
                    var epoch = NextEpoch();
                    await gateway.StartLocalBattleAsync(epoch, 1, unchecked((uint)epoch)).ConfigureAwait(false);
                }, cancellation);

        public void Stop() { lock (_sync) { EnsureOpen(); StopCore(); } }

        private void StopCore()
        {
            // Drain both monitor and profile-triggered rebuilds before gateway disposal takes its lifecycle lock.
            _rearm.StopAsync(() => _gateway?.SetProfileChangedHandler(null)).GetAwaiter().GetResult();
            _cancellation?.Cancel();
            try
            {
                if (_monitor != null)
                {
                    try { _monitor.GetAwaiter().GetResult(); }
                    catch (OperationCanceledException) when (_cancellation.IsCancellationRequested) { }
                }
            }
            finally
            {
                _monitor = null;
                _cancellation?.Dispose(); _cancellation = null;
                var gateway = _gateway;
                if (gateway != null) { gateway.Dispose(); _gateway = null; }
            }
        }

        public BattleHostSnapshot GetSnapshot()
        {
            lock (_sync)
            {
                EnsureOpen();
                var value = _gateway?.GetSnapshot();
                var failed = _failure.Length != 0 || value?.HasFailed == true || _monitor?.IsFaulted == true || _gateway?.Completion.IsFaulted == true;
                var running = !failed && value?.IsRunning == true;
                return new BattleHostSnapshot(new NativeBattleHostSnapshot
                {
                    State = failed ? BattleHostState.Failed : running ? BattleHostState.Running : BattleHostState.Stopped,
                    LoginPort = value?.LoginPort ?? 0, LogicPort = value?.LogicPort ?? 0, InstancePort = value?.InstancePort ?? 0,
                    Connections = value?.ActiveConnections ?? 0
                }, failed ? "failed" : running ? "running" : "stopped");
            }
        }

        public string GetError()
        {
            lock (_sync)
            {
                EnsureOpen();
                return _monitor?.Exception?.GetBaseException().Message ?? _gateway?.Completion.Exception?.GetBaseException().Message
                    ?? (_failure.Length != 0 ? _failure : _gateway?.GetError() ?? string.Empty);
            }
        }

        public void Dispose()
        {
            lock (_sync)
            {
                if (_disposed) return;
                StopCore();
                _disposed = true;
            }
        }
        private void EnsureOpen() { if (_disposed) throw new ObjectDisposedException(nameof(NativeBattleHost)); }
        private static void ValidatePath(string value, string name)
        {
            if (!NativeBridgeContract.IsUtf8PathAcceptable(value)) throw new ArgumentException("Expected an absolute UTF-8 path.", name);
        }
    }
}
