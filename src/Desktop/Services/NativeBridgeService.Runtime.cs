using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using T7R.Battle;
using T7R.Core;

namespace T7R.Desktop.Services
{
    public sealed partial class NativeBridgeService
    {
        private readonly SemaphoreSlim _runtimeGate = new SemaphoreSlim(1, 1);
        private readonly string _runtimeDataRoot;
        private OfflineBattleSession _offlineBattle;
        public RuntimeSessionStatus GetRuntimeStatus()
        {
            var session = Volatile.Read(ref _offlineBattle);
            return new RuntimeSessionStatus(session != null, session?.GatewayConnectionError, session?.GatewayFailure);
        }

        public async Task<OperationSnapshot> CheckAsync(string clientDirectory, CancellationToken cancellationToken)
        {
            await _runtimeGate.WaitAsync(cancellationToken).ConfigureAwait(false);
            try
            {
                var result = await SubmitAndWaitAsync(OperationKind.Check, clientDirectory, cancellationToken).ConfigureAwait(false);
                if (result.Status != OperationStatus.Succeeded) return result;
                var resources = new LocalResourceOptions(clientDirectory, Path.Combine(LocalDataRoot, "battle-cache"),
                    1, 1, ReadClientVersion(clientDirectory));
                await ClientGateway.ValidateLocalResourcesAsync(Path.Combine(_packageRoot, "t7r_battle_ffi.dll"),
                    resources).ConfigureAwait(false);
                cancellationToken.ThrowIfCancellationRequested();
                return result;
            }
            finally { _runtimeGate.Release(); }
        }

        public Task<OperationSnapshot> StartAsync(string clientDirectory, CancellationToken cancellationToken) =>
            StartAsync(clientDirectory, PlayerNameRules.DefaultName, false, cancellationToken);

        public Task<OperationSnapshot> StartAsync(string clientDirectory, string playerName, CancellationToken cancellationToken) =>
            StartAsync(clientDirectory, playerName, false, cancellationToken);

        public async Task<OperationSnapshot> StartAsync(string clientDirectory, string playerName,
            bool skipStartupAnimation, CancellationToken cancellationToken)
        {
            var error = PlayerNameRules.Validate(playerName);
            if (error.Length != 0) throw new ArgumentException(error, nameof(playerName));
            await _runtimeGate.WaitAsync(cancellationToken).ConfigureAwait(false);
            try
            {
                EnsureUsable();
                if (_offlineBattle != null) throw new InvalidOperationException("Stop the previous session before starting again.");
                var version = ReadClientVersion(clientDirectory);
                var offline = new OfflineBattleSession(this, _packageRoot, LocalDataRoot);
                _offlineBattle = offline;
                try
                {
                    var result = await offline.StartAsync(clientDirectory, version, playerName.Trim(),
                        skipStartupAnimation, cancellationToken).ConfigureAwait(false);
                    if (result.Status != OperationStatus.Succeeded) _offlineBattle = null;
                    return result;
                }
                catch (Exception startError)
                {
                    // Keep ownership when cleanup fails, so Stop/Dispose can retry.
                    try { await offline.StopAsync().ConfigureAwait(false); }
                    catch (Exception cleanupError) { throw new AggregateException(startError, cleanupError); }
                    _offlineBattle = null;
                    throw;
                }
            }
            finally { _runtimeGate.Release(); }
        }

        public async Task<OperationSnapshot> StopAsync(CancellationToken cancellationToken)
        {
            await _runtimeGate.WaitAsync(cancellationToken).ConfigureAwait(false);
            try
            {
                var offline = _offlineBattle;
                if (offline == null) return await StopClientAsync(cancellationToken).ConfigureAwait(false);
                var result = await offline.StopAsync().ConfigureAwait(false);
                _offlineBattle = null;
                lock (_sync) _pendingTitles.Clear();
                return result;
            }
            finally { _runtimeGate.Release(); }
        }

        internal Task<OperationSnapshot> StopClientAsync(CancellationToken cancellationToken) =>
            SubmitAndWaitAsync(OperationKind.Stop, null, cancellationToken);

        private string LocalDataRoot => _runtimeDataRoot;

        private static string ReadClientVersion(string clientDirectory)
        {
            var directory = new DirectoryInfo(clientDirectory);
            var root = directory.Name.Equals("Bin", StringComparison.OrdinalIgnoreCase) ? directory.Parent.FullName : directory.FullName;
            var result = ClientVersionService.Read(root);
            if (string.IsNullOrEmpty(result.Version))
                throw new InvalidOperationException("Client version is required for Rust resource validation: " + result.Diagnostic);
            return result.Version;
        }
    }
}
