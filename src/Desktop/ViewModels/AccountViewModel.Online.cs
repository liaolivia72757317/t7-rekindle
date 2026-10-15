using System;
using T7R.Core;
using System.Threading;
using System.Threading.Tasks;
using T7R.Desktop.Services;

namespace T7R.Desktop.ViewModels
{
    public sealed partial class AccountViewModel
    {
        private bool _heartbeatHealthy, _pushHealthy;

        public async Task ReconnectAsync()
        {
            if (_disposed || _busy || _session == null || _connection != null) return;
            if (!_signedIn) { await RestoreAsync().ConfigureAwait(true); return; }
            int generation = Begin();
            _busy = true;
            try
            {
                StartNameSync();
                await RefreshGameNameAsync(_lifetime.Token).ConfigureAwait(true);
                if (IsCurrent(generation)) await ConnectAsync(generation, _lifetime.Token).ConfigureAwait(true);
            }
            catch (Exception error)
            {
                if (IsCurrent(generation) && !HandleTerminal(error)) { SetStatus("已登录 · 上线失败，请重试"); Record(DiagnosticLogLevel.Error, "账号上线失败", error); }
            }
            finally { if (IsCurrent(generation)) { _busy = _waitingForServer = false; Changed(); } }
        }

        private async Task ConnectAsync(int generation, CancellationToken cancellation)
        {
            _waitingForServer = true;
            SetStatus("已登录 · 正在上线");
            var connection = await _api.OnlineAsync(_session, cancellation).ConfigureAwait(true);
            if (!IsCurrent(generation)) return;
            if (connection == null || !Guid.TryParse(connection.ConnectionId, out _) || connection.HeartbeatIntervalSeconds <= 0
                || connection.HeartbeatIntervalSeconds > 60) throw new System.IO.InvalidDataException("在线响应格式错误。");
            _connection = connection;
            _heartbeatHealthy = true;
            _pushHealthy = false;
            UpdateOnlineStatus();
            _ = RefreshTitlesAsync();
            _ = HeartbeatLoopAsync(_session, connection, generation, cancellation);
            _ = PushLoopAsync(_session, connection, generation, cancellation);
        }

        private async Task HeartbeatLoopAsync(AccountSession session, AccountConnection connection, int generation,
            CancellationToken cancellation)
        {
            try
            {
                while (IsCurrent(generation))
                {
                    await _delay(TimeSpan.FromSeconds(connection.HeartbeatIntervalSeconds), cancellation).ConfigureAwait(true);
                    if (!IsCurrent(generation)) return;
                    if (session.ExpiresAt <= DateTimeOffset.UtcNow) { ClearSession("登录已到期，请重新扫码"); return; }
                    try
                    {
                        var renewed = await _api.HeartbeatAsync(session, connection.ConnectionId, cancellation).ConfigureAwait(true);
                        if (!IsCurrent(generation)) return;
                        if (renewed == null || renewed.ConnectionId != connection.ConnectionId || renewed.HeartbeatIntervalSeconds <= 0
                            || renewed.HeartbeatIntervalSeconds > 60) throw new System.IO.InvalidDataException("在线连接不匹配。");
                        _connection = renewed;
                        connection = renewed;
                        _heartbeatHealthy = true;
                    }
                    catch (Exception error)
                    {
                        if (!IsCurrent(generation) || HandleTerminal(error)) return;
                        _heartbeatHealthy = false;
                        _titles = AccountTitles.Empty;
                        Record(DiagnosticLogLevel.Warn, "账号心跳暂未完成", error);
                    }
                    UpdateOnlineStatus();
                    if (IsOnline) _ = RefreshTitlesAsync();
                }
            }
            catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
        }

        private async Task PushLoopAsync(AccountSession session, AccountConnection connection, int generation,
            CancellationToken cancellation)
        {
            var delays = new[] { 1, 2, 5, 10, 30 };
            int attempt = 0;
            try
            {
                while (IsCurrent(generation))
                {
                    try
                    {
                        await _api.ListenAsync(session, connection.ConnectionId,
                            () => _dispatch(() =>
                            {
                                if (!IsCurrent(generation)) return;
                                _pushHealthy = true; attempt = 0; UpdateOnlineStatus();
                            }),
                            kick => _dispatch(() => OnKicked(kick, generation)), cancellation).ConfigureAwait(true);
                    }
                    catch (Exception error)
                    {
                        if (!IsCurrent(generation) || HandleTerminal(error)) return;
                        Record(DiagnosticLogLevel.Warn, "账号推送连接暂未完成", error);
                    }
                    if (!IsCurrent(generation)) return;
                    _pushHealthy = false;
                    UpdateOnlineStatus();
                    await _delay(TimeSpan.FromSeconds(delays[Math.Min(attempt++, delays.Length - 1)]), cancellation).ConfigureAwait(true);
                }
            }
            catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
        }

        private void OnKicked(AccountKick kick, int generation)
        {
            if (!IsCurrent(generation) || kick == null || kick.SessionId != _session?.SessionId
                || kick.ConnectionId != _connection?.ConnectionId) return;
            if (kick.Reason == "ONLINE_TAKEN_OVER") { Disconnect("账号已在其他连接上线"); return; }
            if (kick.Reason == "QQ_GROUP_NOT_MEMBER")
            {
                string group = string.IsNullOrEmpty(kick.GroupNumber) ? string.Empty : "（群号：" + kick.GroupNumber + "）";
                Disconnect("当前 QQ 未加入指定群" + group + "，请加入后重新上线。");
                return;
            }
            string reason = kick.Reason == "MACHINE_CHANGED" ? "账号已在其他机器登录"
                : kick.Reason == "ACCOUNT_DISABLED" ? "账号已被停用"
                : "账号已下线，请重新扫码登录";
            ClearSession(reason);
        }

        private void UpdateOnlineStatus() => SetStatus(!_heartbeatHealthy ? "已登录 · 网络连接异常"
            : _pushHealthy ? "已登录 · 在线" : "已登录 · 在线，推送连接中");
    }
}
