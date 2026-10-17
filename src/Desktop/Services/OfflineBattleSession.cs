using System;
using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using T7R.Battle;
using T7R.Core;

namespace T7R.Desktop.Services
{
    internal sealed class OfflineBattleSession
    {
        private static long _nextEpoch = DateTime.UtcNow.Ticks;
        private readonly NativeBridgeService _bridge;
        private readonly string _packageRoot;
        private readonly string _localDataRoot;
        private BattleRearmCoordinator _rearm = new BattleRearmCoordinator();
        private readonly SemaphoreSlim _stopGate = new SemaphoreSlim(1, 1);
        private ClientGateway _gateway;
        private bool _nativeSubmitted;
        private bool _attached;
        private CancellationTokenSource _rearmCancellation;
        private Task _rearmTask;
        private TaskCompletionSource<bool> _attachmentReady;
        private string _gatewayConnectionError;
        private MatchMapPreferences _matchMaps;

        internal OfflineBattleSession(NativeBridgeService bridge, string packageRoot, string localDataRoot = null)
        {
            _bridge = bridge ?? throw new ArgumentNullException(nameof(bridge));
            _packageRoot = Path.GetFullPath(packageRoot ?? throw new ArgumentNullException(nameof(packageRoot)));
            _localDataRoot = localDataRoot == null
                ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "T7-Rekindle")
                : Path.GetFullPath(localDataRoot);
        }

        internal async Task<OperationSnapshot> StartAsync(string clientDirectory, string clientVersion,
            string playerName, bool skipStartupAnimation, CancellationToken cancellationToken)
        {
            if (_gateway != null) throw new InvalidOperationException("Offline battle session is already started.");
            _rearm = new BattleRearmCoordinator();
            _attachmentReady = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            if (string.IsNullOrEmpty(clientVersion))
                throw new InvalidOperationException("Client version is required for Rust resource validation.");
            var rustLibrary = Path.Combine(_packageRoot, "t7r_battle_ffi.dll");
            var resources = new LocalResourceOptions(clientDirectory, Path.Combine(_localDataRoot, "battle-cache"),
                1, 1, clientVersion);
            var epoch = checked((ulong)Interlocked.Increment(ref _nextEpoch));
            try
            {
                _gateway = await ClientGateway.CreateLocalProfiledAsync(rustLibrary, resources, playerName,
                    Path.Combine(_localDataRoot, "player-profile.json")).ConfigureAwait(false);
                cancellationToken.ThrowIfCancellationRequested();
                await _gateway.StartAsync(epoch).ConfigureAwait(false);
                var config = await _gateway.StartLocalBattleAsync(epoch, 1, unchecked((uint)epoch)).ConfigureAwait(false);
                _gateway.SetProfileChangedHandler(OnProfileChangedAsync);
                _gateway.SetMatchMapSelectedHandler(OnMatchMapSelectedAsync);
                var profiles = _gateway.ClientCombatProfiles();
                cancellationToken.ThrowIfCancellationRequested();
                var ports = _gateway.GetSnapshot();
                if (!ports.IsRunning || ports.LoginPort == 0 || ports.LogicPort == 0 || ports.InstancePort == 0)
                    throw new InvalidOperationException("Rust gateway did not bind all client endpoints.");
                _matchMaps = new MatchMapPreferences(clientDirectory, _localDataRoot);
                _matchMaps.Restore();
                _nativeSubmitted = true;
                var result = await _bridge.StartWithGatewayProfilesAsync(clientDirectory, playerName,
                    skipStartupAnimation, ports.LoginPort, ports.LogicPort, ports.InstancePort,
                    profiles, cancellationToken).ConfigureAwait(false);
                if (result.Status != OperationStatus.Succeeded)
                {
                    await StopAsync().ConfigureAwait(false);
                    return result;
                }
                cancellationToken.ThrowIfCancellationRequested();
                _bridge.AttachBattle(config, rustLibrary, _gateway.NativeHandle);
                _attached = true;
                _attachmentReady.TrySetResult(true);
                var rearmCancellation = new CancellationTokenSource();
                _rearmCancellation = rearmCancellation;
                _rearmTask = Task.Run(() => MonitorRoomReturnAsync(rearmCancellation.Token));
                return result;
            }
            catch (Exception startError)
            {
                try { await StopAsync().ConfigureAwait(false); }
                catch (Exception cleanupError) { throw new AggregateException(startError, cleanupError); }
                throw;
            }
        }

        private async Task MonitorRoomReturnAsync(CancellationToken cancellationToken)
        {
            uint connectionErrors = 0;
            while (true)
            {
                await Task.Delay(100, cancellationToken).ConfigureAwait(false);
                _matchMaps?.Poll();
                if (_gateway.Completion.IsFaulted) await _gateway.Completion.ConfigureAwait(false);
                var snapshot = _gateway.GetSnapshot();
                if (snapshot.ConnectionErrors > connectionErrors)
                {
                    connectionErrors = snapshot.ConnectionErrors;
                    Volatile.Write(ref _gatewayConnectionError, _gateway.GetError());
                }
                await RearmBattleAsync(true, cancellationToken).ConfigureAwait(false);
            }
        }

        private async Task OnProfileChangedAsync(CancellationToken cancellationToken)
        {
            // A client can save its lobby profile before native startup finishes attaching the staged match.
            if (await _attachmentReady.Task.ConfigureAwait(false))
                await RearmBattleAsync(false, cancellationToken).ConfigureAwait(false);
        }

        private async Task OnMatchMapSelectedAsync(uint mapId, CancellationToken cancellationToken)
        {
            if (!await _attachmentReady.Task.ConfigureAwait(false))
                throw new InvalidOperationException("Native battle attachment was stopped.");
            if (_gateway.GetSnapshot().MapId == mapId) return;
            bool rebuilt = false;
            await _rearm.TryRearmAsync(
                () => _attached && _gateway.TryReserveBattleRearm(),
                async token => { await RebuildBattleAsync(token, mapId).ConfigureAwait(false); rebuilt = true; },
                cancellationToken).ConfigureAwait(false);
            if (!rebuilt) throw new InvalidOperationException("Map selection is closed for this match.");
        }

        private Task RearmBattleAsync(bool afterRoomReturn, CancellationToken cancellationToken) =>
            _rearm.TryRearmAsync(
                () => _attached && (afterRoomReturn ? _gateway.BattleReturnedToRoom() : _gateway.TryReserveBattleRearm()),
                token => RebuildBattleAsync(token), cancellationToken);

        private async Task RebuildBattleAsync(CancellationToken cancellationToken, uint? mapId = null)
        {
            _bridge.DetachBattle();
            _attached = false;
            await _gateway.StopBattleAsync().ConfigureAwait(false);
            if (mapId.HasValue) await _gateway.SelectMapAsync(mapId.Value).ConfigureAwait(false);
            cancellationToken.ThrowIfCancellationRequested();
            var epoch = checked((ulong)Interlocked.Increment(ref _nextEpoch));
            var config = await _gateway.StartLocalBattleAsync(epoch, 1, unchecked((uint)epoch))
                .ConfigureAwait(false);
            try
            {
                cancellationToken.ThrowIfCancellationRequested();
                _bridge.AttachBattle(config, Path.Combine(_packageRoot, "t7r_battle_ffi.dll"),
                    _gateway.NativeHandle);
                _attached = true;
            }
            catch
            {
                await _gateway.StopBattleAsync().ConfigureAwait(false);
                throw;
            }
        }

        internal async Task<OperationSnapshot> StopAsync()
        {
            await _stopGate.WaitAsync().ConfigureAwait(false);
            try { return await StopCoreAsync().ConfigureAwait(false); }
            finally { _stopGate.Release(); }
        }

        private async Task<OperationSnapshot> StopCoreAsync()
        {
            await _rearm.StopAsync(() =>
            {
                _attachmentReady?.TrySetResult(false);
                _gateway?.SetProfileChangedHandler(null);
                _gateway?.SetMatchMapSelectedHandler(null);
            }).ConfigureAwait(false);
            var errors = new List<Exception>();
            OperationSnapshot result = null;
            Exception rearmFailure = null;
            var monitor = _rearmTask;
            if (monitor != null)
            {
                _rearmCancellation.Cancel();
                try { await monitor.ConfigureAwait(false); }
                catch (OperationCanceledException) when (_rearmCancellation.IsCancellationRequested) { }
                catch (Exception error) { rearmFailure = error; }
                _rearmTask = null;
                _rearmCancellation.Dispose();
                _rearmCancellation = null;
            }
            if (_attached)
            {
                try { _bridge.DetachBattle(); _attached = false; }
                catch (Exception error) { errors.Add(error); }
            }
            if (_nativeSubmitted)
            {
                try
                {
                    result = await _bridge.StopClientAsync(CancellationToken.None).ConfigureAwait(false);
                    if (result.Status != OperationStatus.Succeeded)
                        throw new InvalidOperationException("NativeBridge stop did not complete: " + result.Error);
                    _nativeSubmitted = false;
                }
                catch (Exception error) { errors.Add(error); }
            }
            var gateway = _gateway;
            _matchMaps?.Capture();
            _matchMaps = null;
            if (gateway != null)
            {
                try { await gateway.StopBattleAsync().ConfigureAwait(false); }
                catch (Exception error) { errors.Add(error); }
                try { await gateway.StopAsync().ConfigureAwait(false); }
                catch (Exception error) { errors.Add(error); }
                try { gateway.Dispose(); _gateway = null; }
                catch (Exception error) { errors.Add(error); }
            }
            if (errors.Count != 0) throw new AggregateException("Offline battle cleanup failed.", errors);
            if (rearmFailure != null)
                return new OperationSnapshot
                {
                    Status = OperationStatus.Failed,
                    Error = "Rust battle rearm failed: " + rearmFailure.GetBaseException().Message
                };
            return result ?? new OperationSnapshot { Status = OperationStatus.Succeeded };
        }

        internal Exception GatewayFailure
        {
            get
            {
                var completion = _gateway?.Completion;
                if (completion != null && completion.IsFaulted) return completion.Exception;
                var monitor = _rearmTask;
                return monitor != null && monitor.IsFaulted ? monitor.Exception : null;
            }
        }

        internal string GatewayConnectionError => Volatile.Read(ref _gatewayConnectionError);

        internal void PublishTitles(ulong epoch, string json, ulong requestId, bool success) =>
            _gateway.PublishTitles(epoch, json, requestId, success);

        internal NativeTitleRequest ReadTitleRequest()
        {
            var value = _gateway.ReadTitleRequest();
            return new NativeTitleRequest { AbiVersion = value.AbiVersion, StructSize = value.StructSize,
                RequestId = value.RequestId, Epoch = value.Epoch, TitleId = value.TitleId };
        }
    }
}
