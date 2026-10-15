using System;
using T7R.Core;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using T7R.Desktop.Services;
using T7R.Desktop.Views;

namespace T7R.Desktop.ViewModels
{
    public sealed partial class AccountViewModel : ObservableObject, IDisposable
    {
        private readonly IAccountApi _api;
        private readonly IAccountSessionStore _store;
        private readonly Func<string> _machineId;
        private readonly Func<CancellationToken, Task<string>> _scan;
        private readonly Action<Action> _dispatch;
        private readonly Func<TimeSpan, CancellationToken, Task> _delay;
        private CancellationTokenSource _lifetime = new CancellationTokenSource();
        private AccountSession _session;
        private AccountConnection _connection;
        private int _generation;
        private bool _signedIn, _busy, _disposed, _loginOpen, _waitingForServer;
        private string _status = "未登录";

        public AccountViewModel(IAccountApi api, IAccountSessionStore store, Func<string> machineId,
            Func<CancellationToken, Task<string>> scan, Action<Action> dispatch,
            Func<TimeSpan, CancellationToken, Task> delay = null,
            Func<Func<string, CancellationToken, Task<AccountSession>>, CancellationToken, Task<AccountSession>> setupName = null)
        {
            _api = api; _store = store; _machineId = machineId; _scan = scan; _dispatch = dispatch;
            _delay = delay ?? Task.Delay;
            _setupName = setupName ?? GameNameSetupDialog.ShowAsync;
            LoginCommand = new AsyncRelayCommand(LoginAsync, () => !_busy && !_signedIn && !_disposed);
            LogoutCommand = new AsyncRelayCommand(LogoutAsync, () => HasSession && !_disposed);
            ReconnectCommand = new AsyncRelayCommand(ReconnectAsync, () => HasSession && !_busy && _connection == null && !_disposed);
            if (_api == null) _status = "当前构建未配置账号服务";
        }

        internal static AccountViewModel CreateDefault()
        {
            string address = LauncherInformation.AuthBaseAddress;
            IAccountApi api = null;
            if (!string.IsNullOrWhiteSpace(address))
            {
                try { api = new AccountApi(address); }
                catch (ArgumentException error) { Record(DiagnosticLogLevel.Error, "账号服务配置错误", error); }
            }
            var dispatcher = Application.Current?.Dispatcher ?? System.Windows.Threading.Dispatcher.CurrentDispatcher;
            return new AccountViewModel(api, new AccountSessionStore(address), AccountSessionStore.ReadMachineId,
                QqLoginDialog.ScanAsync, action => dispatcher.BeginInvoke(action));
        }

        public IAsyncRelayCommand LoginCommand { get; }
        public IAsyncRelayCommand LogoutCommand { get; }
        public IAsyncRelayCommand ReconnectCommand { get; }
        public bool IsSignedIn => _signedIn;
        public bool IsSignedOut => !_signedIn;
        public bool HasSession => _session != null;
        public bool CanReconnect => HasSession && !_busy && _connection == null;
        public bool IsOnline => !_disposed && _signedIn && _connection != null && _heartbeatHealthy
            && _connection.LeaseExpiresAt > DateTimeOffset.UtcNow && _session.ExpiresAt > DateTimeOffset.UtcNow;
        public bool IsLoginOpen => _loginOpen;
        public bool IsWaitingForServer => _waitingForServer;
        public string AccountState => !_signedIn ? "guest" : IsOnline ? "online" : "offline";
        public string DisplayName => _signedIn ? _session.Nickname : "未登录";
        public string AccountHint => IsWaitingForServer ? Status + "，请稍候。" : _signedIn
            ? DisplayName + "（" + QqNumber + "），" + (IsOnline ? "在线" : "离线") + "。" + Status
            : "未登录，打开 QQ 登录页。" + Status;
        public string QqNumber => _signedIn ? _session.QqNumber : string.Empty;
        public string AvatarUrl => _signedIn ? _session.AvatarUrl : null;
        public string Status => _status;

        public async Task RestoreAsync()
        {
            if (_disposed || _api == null || _busy || _signedIn) return;
            int generation = Begin();
            CancellationToken cancellation = _lifetime.Token;
            _busy = true;
            SetStatus("正在恢复登录");
            try
            {
                var saved = _session ?? _store.Load();
                if (saved == null) { SetStatus("未登录"); return; }
                Validate(saved, _machineId());
                _session = saved;
                _waitingForServer = true;
                Changed();
                var current = await _api.CurrentAsync(saved, cancellation).ConfigureAwait(true);
                if (!IsCurrent(generation)) return;
                Validate(current, saved.MachineId);
                if (current.SessionId != saved.SessionId || current.AccountId != saved.AccountId)
                    throw new InvalidOperationException("账号恢复响应不匹配。");
                if (string.IsNullOrEmpty(current.GameName))
                {
                    var pending = current;
                    current = await CompleteNameAsync((name, token) => InitializeGameNameAsync(pending, name, token), generation, cancellation);
                    if (!IsCurrent(generation)) return;
                    if (current == null) { await LogoutAsync(); return; }
                }
                Accept(current);
                await ConnectAsync(generation, cancellation).ConfigureAwait(true);
            }
            catch (Exception error)
            {
                if (!IsCurrent(generation)) return;
                if (!HandleTerminal(error))
                {
                    if (error is System.IO.InvalidDataException || error is System.Security.Cryptography.CryptographicException
                        || error is Newtonsoft.Json.JsonException || error is FormatException)
                        ClearSession("登录记录已失效，请重新扫码");
                    else SetStatus(_signedIn ? "已登录 · 上线失败，请重试" : "登录验证暂未完成，请重试");
                    Record(DiagnosticLogLevel.Error, "恢复账号登录失败", error);
                }
            }
            finally { if (IsCurrent(generation)) { _busy = _waitingForServer = false; Changed(); } }
        }

        public async Task LoginAsync()
        {
            if (_disposed || _busy || _signedIn) return;
            if (_api == null) { SetStatus("当前构建未配置账号服务"); return; }
            int generation = Begin();
            CancellationToken cancellation = _lifetime.Token;
            _busy = true;
            SetStatus("等待 QQ 扫码");
            try
            {
                string machine = _machineId();
                string cookie;
                _loginOpen = true;
                Changed();
                try { cookie = await _scan(cancellation).ConfigureAwait(true); }
                finally { if (IsCurrent(generation)) { _loginOpen = false; Changed(); } }
                if (!IsCurrent(generation)) return;
                if (string.IsNullOrEmpty(cookie)) { SetStatus("未登录"); return; }
                _waitingForServer = true;
                SetStatus("正在验证登录");
                AccountSession session;
                try { session = await _api.LoginAsync(machine, cookie, cancellation).ConfigureAwait(true); }
                catch (AccountApiException error) when (error.Code == "REGISTRATION_REQUIRED")
                {
                    if (!IsCurrent(generation)) return;
                    session = await CompleteNameAsync((name, token) => _api.CompleteRegistrationAsync(machine, cookie, name, token), generation, cancellation);
                }
                if (!IsCurrent(generation)) { await RetireSessionAsync(session); return; }
                if (session == null) { await LogoutAsync(); return; }
                Validate(session, machine);
                Accept(session);
                await ConnectAsync(generation, cancellation).ConfigureAwait(true);
            }
            catch (Exception error)
            {
                if (!IsCurrent(generation)) return;
                if (!HandleTerminal(error))
                {
                    var problem = error as AccountApiException;
                    SetStatus(problem?.Code == "QQ_ACCOUNT_CONFLICT" ? "QQ 号与已有账号重名，请联系管理员"
                        : error is System.IO.InvalidDataException ? "账号服务尚未支持有效游戏名称，请联系管理员升级"
                        : problem?.Status == 429 ? "登录过于频繁，请稍后重试"
                        : _signedIn ? "已登录 · 上线失败，请重试" : "登录失败，请稍后重试");
                    Record(DiagnosticLogLevel.Error, "QQ 扫码登录失败", error);
                }
            }
            finally { if (IsCurrent(generation)) { _busy = _waitingForServer = false; Changed(); } }
        }

        private void Accept(AccountSession session)
        {
            RequireGameName(session.GameName);
            _session = session;
            _signedIn = true;
            try { _store.Save(session); }
            catch (Exception error) { Record(DiagnosticLogLevel.Error, "保存账号登录状态失败", error); }
            Changed();
            StartNameSync();
        }

        public async Task LogoutAsync()
        {
            var previous = _session;
            ClearSession("未登录");
            if (previous == null || _api == null) return;
            try { await _api.LogoutAsync(previous, CancellationToken.None).ConfigureAwait(true); }
            catch (Exception error) { Record(DiagnosticLogLevel.Warn, "账号注销请求未完成", error); }
        }

        public async Task SuspendAsync()
        {
            var previous = _session;
            var connection = _connection;
            StopNameSync();
            Begin();
            _busy = false;
            Changed();
            if (previous == null || connection == null) return;
            try { await _api.OfflineAsync(previous, connection.ConnectionId, CancellationToken.None).ConfigureAwait(true); }
            catch (Exception error) { Record(DiagnosticLogLevel.Warn, "账号下线请求未完成", error); }
        }

        private int Begin()
        {
            _generation++;
            _loginOpen = _waitingForServer = _registrationOpen = false;
            _lifetime.Cancel();
            _lifetime.Dispose();
            _lifetime = new CancellationTokenSource();
            _connection = null;
            _titles = AccountTitles.Empty;
            _titleStatus = "上线后可查看称号";
            return _generation;
        }
        private bool IsCurrent(int generation) => !_disposed && generation == _generation;
        private void Disconnect(string message)
        {
            Begin();
            _busy = false;
            SetStatus(message);
        }
        private void ClearSession(string message)
        {
            StopNameSync();
            Begin();
            _session = null;
            _signedIn = _busy = false;
            try { _store.Clear(); }
            catch (Exception error) { Record(DiagnosticLogLevel.Error, "清理本机登录记录失败", error); }
            SetStatus(message);
        }
        private bool HandleTerminal(Exception error)
        {
            var problem = error as AccountApiException;
            if (problem?.Status == 401) { ClearSession("登录已失效，请重新扫码"); return true; }
            if (problem?.Code == "ONLINE_TAKEN_OVER") { Disconnect("账号已在其他连接上线"); return true; }
            if (problem?.Code == "NOT_ONLINE") { Disconnect("已登录 · 离线"); return true; }
            if (problem?.Code == "QQ_GROUP_NOT_MEMBER" || problem?.Code == "QQ_GROUP_UNAVAILABLE")
            { Disconnect(problem.Message); return true; }
            return false;
        }
        private static void Validate(AccountSession session, string machine)
        {
            if (session == null || !Guid.TryParse(session.SessionId, out _) || !Guid.TryParse(session.AccountId, out _)
                || session.MachineId != machine || session.ExpiresAt <= DateTimeOffset.UtcNow
                || session.SessionToken == null || !Regex.IsMatch(session.SessionToken, "\\A[A-Za-z0-9_-]{43}\\z")
                || session.QqNumber == null || !Regex.IsMatch(session.QqNumber, "\\A[1-9][0-9]{4,19}\\z")
                || session.LoginName != session.QqNumber || string.IsNullOrWhiteSpace(session.Nickname))
                throw new System.IO.InvalidDataException("账号登录记录无效。");
        }
        private void SetStatus(string value) { _status = value; Changed(); }
        private void Changed()
        {
            OnPropertyChanged(string.Empty);
            LoginCommand.NotifyCanExecuteChanged(); LogoutCommand.NotifyCanExecuteChanged(); ReconnectCommand.NotifyCanExecuteChanged();
        }
        private static void Record(DiagnosticLogLevel level, string operation, Exception error) =>
            new LogService().Write(level, operation + "（" + error.GetType().Name + "）", source: typeof(LogService).FullName);
        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;
            StopNameSync();
            _registrationOpen = false;
            _generation++;
            _lifetime.Cancel();
            _lifetime.Dispose();
            _api?.Dispose();
            _busy = _loginOpen = _waitingForServer = false;
            Changed();
        }
    }
}
