using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using T7R.Core;
using T7R.Desktop.Services;

namespace T7R.Desktop.ViewModels
{
    public sealed partial class AccountViewModel
    {
        private readonly Func<Func<string, CancellationToken, Task<AccountSession>>, CancellationToken, Task<AccountSession>> _setupName;
        private CancellationTokenSource _nameSyncCancellation;
        private Task _nameRefreshTask;
        private int _nameSyncGeneration;
        private bool _registrationOpen;
        public bool IsRegistrationOpen => _registrationOpen;
        public string GameName => _signedIn ? _session.GameName : null;

        private async Task<AccountSession> CompleteNameAsync(
            Func<string, CancellationToken, Task<AccountSession>> submit, int generation, CancellationToken cancellation)
        {
            _registrationOpen = true;
            _waitingForServer = false;
            SetStatus("请设置游戏名称，完成注册");
            try
            {
                return await _setupName(async (name, token) =>
                {
                    var result = await submit(name, token).ConfigureAwait(true);
                    if (!IsCurrent(generation) || token.IsCancellationRequested)
                    {
                        await RetireSessionAsync(result).ConfigureAwait(true);
                        throw new OperationCanceledException(token);
                    }
                    Validate(result, _machineId());
                    RequireGameName(result.GameName);
                    return result;
                }, cancellation).ConfigureAwait(true);
            }
            finally { if (IsCurrent(generation)) { _registrationOpen = false; Changed(); } }
        }

        private async Task<AccountSession> InitializeGameNameAsync(AccountSession session, string name, CancellationToken cancellation)
        {
            AccountGameName result;
            try { result = await _api.SetGameNameAsync(session, name, cancellation).ConfigureAwait(true); }
            catch (AccountApiException error) when (error.Code == "GAME_NAME_ALREADY_SET")
            { result = await _api.GameNameAsync(session, cancellation).ConfigureAwait(true); }
            RequireGameName(result?.GameName);
            return session.WithGameName(result.GameName);
        }
        private static void RequireGameName(string name)
        {
            if (PlayerNameRules.Validate(name).Length != 0 || name != name.Trim())
                throw new InvalidDataException("账号服务返回的游戏名称无效，请联系管理员升级服务。");
        }
        private async Task RetireSessionAsync(AccountSession session)
        {
            if (session == null) return;
            try { await _api.LogoutAsync(session, CancellationToken.None).ConfigureAwait(true); }
            catch (Exception error) { Record(DiagnosticLogLevel.Warn, "取消登录后的会话注销未完成", error); }
        }
        private void StartNameSync()
        {
            if (_nameSyncCancellation != null) return;
            _nameSyncCancellation = new CancellationTokenSource();
            _ = NameSyncLoopAsync(_nameSyncGeneration, _nameSyncCancellation.Token);
        }
        private void StopNameSync()
        {
            ++_nameSyncGeneration;
            _nameSyncCancellation?.Cancel();
            _nameSyncCancellation?.Dispose();
            _nameSyncCancellation = null;
            _nameRefreshTask = null;
        }
        private async Task NameSyncLoopAsync(int generation, CancellationToken cancellation)
        {
            try
            {
                while (!_disposed && generation == _nameSyncGeneration && _signedIn)
                {
                    await _delay(TimeSpan.FromSeconds(30), cancellation).ConfigureAwait(true);
                    cancellation.ThrowIfCancellationRequested();
                    if (_session.ExpiresAt <= DateTimeOffset.UtcNow) { ClearSession("登录已到期，请重新扫码"); return; }
                    try { await RefreshGameNameAsync(cancellation).ConfigureAwait(true); }
                    catch (Exception error)
                    {
                        if (_disposed || generation != _nameSyncGeneration) return;
                        if (error is AccountApiException problem && problem.Status == 401) { HandleTerminal(error); return; }
                        Record(DiagnosticLogLevel.Warn, "游戏名称同步未完成，保留最近确认名称", error);
                    }
                }
            }
            catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
        }
        private Task RefreshGameNameAsync(CancellationToken cancellation)
        {
            if (_nameRefreshTask == null || _nameRefreshTask.IsCompleted)
                _nameRefreshTask = ReadGameNameAsync(_session, _nameSyncGeneration, cancellation);
            return _nameRefreshTask;
        }
        private async Task ReadGameNameAsync(AccountSession session, int generation, CancellationToken cancellation)
        {
            var result = await _api.GameNameAsync(session, cancellation).ConfigureAwait(true);
            if (_disposed || generation != _nameSyncGeneration || !_signedIn || _session.SessionId != session.SessionId) return;
            RequireGameName(result?.GameName);
            if (_session.GameName == result.GameName) return;
            _session = _session.WithGameName(result.GameName);
            try { _store.Save(_session); }
            catch (Exception error) { Record(DiagnosticLogLevel.Error, "保存账号游戏名称失败", error); }
            Changed();
        }
    }
}
