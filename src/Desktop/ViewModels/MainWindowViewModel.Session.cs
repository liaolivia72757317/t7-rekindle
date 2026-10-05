using System;
using System.Threading;
using System.Threading.Tasks;
using T7.Rekindle.Core;

namespace T7.Rekindle.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel
    {
        private CancellationTokenSource _operationCancellation;
        private OperationKind _activeKind;
        private bool _operationActive;
        private string _failureMessage = string.Empty;
        private string _completedMessage = string.Empty;
        private string _lastAction = "启动";
        private string _statusText = "首次使用";
        private string _diagnosticText = "请在设置中填写玩家名称与游戏目录。";
        private string _statusTone = "Neutral";
        private string _statusSymbol = "·";
        private string _mainActionText = "启动游戏";
        private DateTime _operationStartedAt;
        private bool _hasRunningSession;

        internal event Action GameSessionEnded;

        public string StatusText { get => _statusText; private set => SetProperty(ref _statusText, value); }
        public string DiagnosticText { get => _diagnosticText; private set => SetProperty(ref _diagnosticText, value); }
        public string StatusTone { get => _statusTone; private set => SetProperty(ref _statusTone, value); }
        public string StatusSymbol { get => _statusSymbol; private set => SetProperty(ref _statusSymbol, value); }
        public string MainActionText { get => _mainActionText; private set => SetProperty(ref _mainActionText, value); }
        public bool ShowProgress => StatusTone == "Working";
        public string EndpointText => _snapshot.LoginPort == 0 ? "本地服务未运行。"
            : string.Format("login={0}  logic={1}  instance={2}", _snapshot.LoginPort, _snapshot.LogicPort, _snapshot.InstancePort);
        public string StageText => _operationActive && DateTime.UtcNow - _operationStartedAt > TimeSpan.FromSeconds(30)
            ? "等待时间较长，可展开日志查看进展" : StatusText;

        private Task CheckAsync()
        {
            var directory = ClientDirectory;
            var version = _validationVersion;
            return ExecuteAsync(async token =>
            {
                var result = await _inspectDirectory(directory);
                if (version == _validationVersion)
                {
                    _directoryResult = result;
                    if (result.IsValid) SetProperty(ref _clientDirectory, result.Root, nameof(ClientDirectory));
                }
                if (!result.IsValid) throw new InvalidOperationException(result.Message);
                token.ThrowIfCancellationRequested();
                var check = await _bridge.CheckAsync(result.Directory, token);
                if (version == _validationVersion && check.Status == OperationStatus.Succeeded) SaveValidatedFields();
                return check;
            }, OperationKind.Check, "检查");
        }

        private Task StartAsync()
        {
            SelectedPage = 0;
            var directory = ClientDirectory;
            var name = PlayerName.Trim();
            var skipStartupAnimation = SkipStartupAnimation;
            return ExecuteAsync(async token =>
            {
                var result = await _inspectDirectory(directory);
                _directoryResult = result;
                if (!result.IsValid) throw new InvalidOperationException(result.Message);
                token.ThrowIfCancellationRequested();
                SetProperty(ref _clientDirectory, result.Root, nameof(ClientDirectory));
                SetProperty(ref _playerName, name, nameof(PlayerName));
                if (!SaveValidatedFields()) throw new InvalidOperationException(SettingsFeedback);
                var check = await _bridge.CheckAsync(result.Directory, token);
                if (check.Status != OperationStatus.Succeeded) return check;
                token.ThrowIfCancellationRequested();
                return await _bridge.StartAsync(result.Directory, name, skipStartupAnimation, token);
            }, OperationKind.Start, "启动");
        }

        private async Task StopAsync()
        {
            if (!_interaction.Confirm("将关闭本次启动的游戏进程。\n尚未保存的游戏进度可能丢失。")) return;
            await StopSessionAsync();
        }

        private Task StopSessionAsync() => ExecuteAsync(_ => _bridge.StopAsync(CancellationToken.None), OperationKind.Stop, "结束");

        private async Task ExecuteAsync(Func<CancellationToken, Task<OperationSnapshot>> action, OperationKind kind, string name)
        {
            if (_operationActive) return;
            var version = _validationVersion;
            var cancellation = new CancellationTokenSource();
            _operationCancellation = cancellation;
            _operationActive = true;
            _activeKind = kind;
            _operationStartedAt = DateTime.UtcNow;
            _lastAction = name;
            _failureMessage = string.Empty;
            _completedMessage = string.Empty;
            UpdatePresentation();
            AppendLog("INFO", name + "操作已提交。");
            try
            {
                var pending = action(cancellation.Token);
                Refresh();
                var result = await pending;
                if (_disposed || (kind == OperationKind.Check && version != _validationVersion)) return;
                if (result.Status == OperationStatus.Cancelled)
                    _completedMessage = name + "已取消";
                else if (result.Status != OperationStatus.Succeeded)
                    _failureMessage = string.IsNullOrEmpty(result.Error) ? "操作未完成，请查看详细日志。" : result.Error;
                else if (kind == OperationKind.Check) _completedMessage = "客户端检查通过";
                else if (kind == OperationKind.Stop) _completedMessage = "游戏已结束";
                AppendLog(_failureMessage.Length == 0 ? "INFO" : "ERROR",
                    _failureMessage.Length == 0 ? name + "操作完成。" : _failureMessage);
            }
            catch (OperationCanceledException)
            {
                if (kind != OperationKind.Check || version == _validationVersion) _completedMessage = name + "已取消";
                AppendLog("INFO", name + "操作已取消。");
            }
            catch (Exception error)
            {
                _log.Error(name + "失败", error);
                if (kind != OperationKind.Check || version == _validationVersion) _failureMessage = error.Message;
                AppendLog("ERROR", name + "失败：" + error.Message);
            }
            finally
            {
                _operationActive = false;
                _activeKind = OperationKind.None;
                _operationCancellation = null;
                cancellation.Dispose();
                if (!_disposed)
                {
                    Refresh(kind == OperationKind.Check && version != _validationVersion);
                    if (_failureMessage.Length != 0) LogsExpanded = true;
                    UpdatePresentation();
                }
            }
        }

        private void Cancel()
        {
            if (!CanCancel) return;
            _operationCancellation.Cancel();
            AppendLog("INFO", "已请求取消，等待本次会话清理完成。");
            UpdatePresentation();
        }

        internal void Refresh(bool ignoreFailure = false)
        {
            try
            {
                var previous = _snapshot;
                _snapshot = _bridge.GetSnapshot();
                if (_snapshot.State == SessionState.Failed && previous.State != SessionState.Failed && !_operationActive
                    && !ignoreFailure && _failureMessage.Length == 0)
                {
                    _failureMessage = _snapshot.ErrorCode == 1003 ? "游戏进程异常退出。"
                        : "本地会话出现错误（错误码 " + _snapshot.ErrorCode + "）。";
                    _lastAction = "运行";
                    LogsExpanded = true;
                }
                if (_snapshot.State == SessionState.FailedCleaning && previous.State != SessionState.FailedCleaning)
                    LogsExpanded = true;
                if (_snapshot.State == SessionState.Idle && _snapshot.Phase == "client-exited")
                    _completedMessage = "游戏已正常退出";
                if (_snapshot.State == SessionState.Running) _hasRunningSession = true;
                var gameEnded = _hasRunningSession && (_snapshot.State == SessionState.Idle || _snapshot.State == SessionState.Failed);
                if (gameEnded)
                {
                    _hasRunningSession = false;
                    IsHomeSelected = true;
                }
                UpdatePresentation();
                if (gameEnded) GameSessionEnded?.Invoke();
            }
            catch (Exception error)
            {
                _failureMessage = "读取启动状态失败：" + error.Message;
                _log.Error("读取启动状态失败", error);
                LogsExpanded = true;
                UpdatePresentation();
            }
        }

        private void UpdatePresentation()
        {
            var state = _snapshot.State;
            if (state == SessionState.FailedCleaning)
                Present("清理尚未完成", "本次会话仍有资源未释放，请查看日志或重试清理。", "Danger", "!", "等待清理");
            else if (state == SessionState.Running)
                Present("游戏进程运行中", "已检测到本次启动的游戏进程；此状态不代表已进入游戏画面。", "Success", "✓", "运行中");
            else if (state == SessionState.Cancelling || (_operationCancellation?.IsCancellationRequested ?? false))
                Present("正在取消…", "正在清理本次启动创建的资源，请稍候。", "Working", "…", "取消中…");
            else if (state == SessionState.StoppingClient || state == SessionState.StoppingRuntime || _activeKind == OperationKind.Stop)
                Present("正在结束游戏…", "正在结束本次游戏会话与本地服务，请稍候。", "Working", "…", "结束中…");
            else if (_activeKind == OperationKind.Start || state == SessionState.StartingRuntime
                || state == SessionState.StartingClient || state == SessionState.AdaptingClient)
                Present("正在启动…", state == SessionState.StartingClient ? "正在创建客户端进程…"
                    : state == SessionState.AdaptingClient ? "正在完成客户端启动适配…"
                    : state == SessionState.StartingRuntime ? "正在准备本地服务…" : "正在检查启动配置…", "Working", "…", "启动中…");
            else if (_isValidating || state == SessionState.Checking || _activeKind == OperationKind.Check)
                Present("正在检查客户端…", "正在核对目录与启动条件，请稍候。", "Working", "…", "检查中…");
            else if (_directoryHasDraft)
                Present("请完成目录编辑", "在设置中按 Enter 或移出输入框后检查目录。", "Neutral", "", "前往设置");
            else if (!_directoryResult.IsValid || HasPlayerNameError)
                Present(!_directoryResult.IsValid ? "未找到游戏客户端" : "请检查玩家名称",
                    !_directoryResult.IsValid ? "请在设置中选择有效目录，并填写玩家名称。 " + _directoryResult.Message : PlayerNameError,
                    !_directoryResult.IsValid ? "Warning" : "Danger", "!", "前往设置");
            else if (_hasSaveError)
                Present("配置保存失败", "目录与名称尚未保存，请检查写入权限后重试。", "Danger", "!", "重试保存");
            else if (_failureMessage.Length != 0)
                Present(_lastAction == "运行" ? "游戏异常退出" : _lastAction + "失败", SummarizeError(_failureMessage) + " 请查看日志，排查后重试。", "Danger", "!", "重试启动");
            else if (_completedMessage.Length != 0)
                Present("准备就绪", string.Empty, "Success", "✓", "启动游戏");
            else Present("准备就绪", string.Empty, "Success", "✓", "启动游戏");

            foreach (var property in new[] { nameof(IsBusy), nameof(CanClose), nameof(AreSessionFieldsLocked), nameof(CanStart),
                nameof(CanCancel), nameof(CanStop), nameof(StopButtonText), nameof(CancelButtonText), nameof(ShowProgress),
                nameof(EndpointText), nameof(StageText), nameof(DirectoryMessage), nameof(IsDirectoryValid),
                nameof(HasDirectoryError), nameof(DirectorySymbol), nameof(HasSessionLog), nameof(MainActionIcon),
                nameof(HomeStatusDetail), nameof(DirectoryFieldError), nameof(DirectoryProgressText), nameof(NameFieldError), nameof(IsManagedGameRunning) }) OnPropertyChanged(property);
            BrowseCommand?.NotifyCanExecuteChanged();
            CheckCommand?.NotifyCanExecuteChanged();
            StartCommand?.NotifyCanExecuteChanged();
            MainActionCommand?.NotifyCanExecuteChanged();
            CancelCommand?.NotifyCanExecuteChanged();
            StopCommand?.NotifyCanExecuteChanged();
        }

        private void Present(string title, string description, string tone, string symbol, string action)
        {
            StatusText = title;
            DiagnosticText = description;
            StatusTone = tone;
            StatusSymbol = symbol;
            MainActionText = action;
        }

        private static string SummarizeError(string message)
        {
            if (message.Contains("unsupported client/protocol baseline")) return "客户端版本与当前启动器支持的版本不一致。";
            var line = message.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);
            var summary = line.Length == 0 ? message : line[0];
            return summary.Length > 160 ? summary.Substring(0, 160) + "…" : summary;
        }

        public async Task<bool> RequestCloseAsync()
        {
            if (CanClose) return true;
            if (!_interaction.Confirm("启动器正在托管游戏及本地服务。关闭会结束本次会话，未保存的游戏进度可能丢失。\n确认结束会话并关闭？")) return false;
            if (!_operationActive) await StopSessionAsync();
            else _operationCancellation?.Cancel();
            var deadline = DateTime.UtcNow.AddSeconds(30);
            while (IsBusy && DateTime.UtcNow < deadline)
            {
                await Task.Delay(50);
                Refresh();
                if (!_operationActive && _snapshot.State == SessionState.Running) await StopSessionAsync();
            }
            if (IsBusy) ShowNotice("清理仍未完成，窗口将保持打开。请查看日志，等待清理或重试。", true);
            return !IsBusy;
        }
    }
}
