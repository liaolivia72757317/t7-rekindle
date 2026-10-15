using System;
using System.Threading;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using T7R.Core;
using T7R.Desktop.Services;

namespace T7R.Desktop.ViewModels
{
    public sealed class GameNameSetupViewModel : ObservableObject, IDisposable
    {
        private readonly Func<string, CancellationToken, Task<AccountSession>> _submit;
        private readonly CancellationTokenSource _cancellation;
        private string _name = string.Empty, _error = string.Empty;
        private bool _busy, _disposed;

        public GameNameSetupViewModel(Func<string, CancellationToken, Task<AccountSession>> submit, CancellationToken cancellation)
        {
            _submit = submit;
            _cancellation = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
            SubmitCommand = new AsyncRelayCommand(SubmitAsync, () => !_busy && !_disposed);
        }
        public string GameName { get => _name; set { SetProperty(ref _name, value ?? string.Empty); Error = string.Empty; } }
        public string Error { get => _error; private set => SetProperty(ref _error, value); }
        public bool IsBusy => _busy;
        public IAsyncRelayCommand SubmitCommand { get; }
        public AccountSession Result { get; private set; }
        internal event Action Accepted;

        private async Task SubmitAsync()
        {
            if (_busy || _disposed) return;
            Error = PlayerNameRules.Validate(GameName);
            if (Error.Length != 0) return;
            _busy = true;
            Changed();
            try
            {
                var result = await _submit(GameName.Trim(), _cancellation.Token).ConfigureAwait(true);
                if (_disposed || _cancellation.IsCancellationRequested) return;
                Result = result;
                Accepted?.Invoke();
            }
            catch (OperationCanceledException) when (_cancellation.IsCancellationRequested) { }
            catch (Exception error)
            {
                if (_disposed) return;
                var problem = error as AccountApiException;
                Error = problem?.Code == "GAME_NAME_EXISTS" ? "游戏名称已被使用，请换一个名称"
                    : problem?.Code == "INVALID_GAME_NAME" ? "名称不符合游戏要求，请检查后重试"
                    : problem?.Status == 401 ? "登录验证已失效，请取消后重新扫码"
                    : problem?.Status == 404 || error is System.IO.InvalidDataException ? "账号服务尚未支持游戏名称，请联系管理员升级"
                    : problem?.Status == 429 ? "操作过于频繁，请稍后重试"
                    : problem?.Code == "QQ_GROUP_NOT_MEMBER" || problem?.Code == "QQ_GROUP_UNAVAILABLE" ? problem.Message
                    : "注册未完成，请检查网络后重试";
                new LogService().Write(DiagnosticLogLevel.Warn, "游戏名称提交未完成（" + error.GetType().Name + "）");
            }
            finally { _busy = false; if (!_disposed) Changed(); }
        }
        private void Changed() { OnPropertyChanged(nameof(IsBusy)); SubmitCommand.NotifyCanExecuteChanged(); }
        public void Dispose() { if (_disposed) return; _disposed = true; _cancellation.Cancel(); _cancellation.Dispose(); }
    }
}
