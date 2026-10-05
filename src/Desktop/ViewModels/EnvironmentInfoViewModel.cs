using System;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using T7.Rekindle.Desktop.Services;

namespace T7.Rekindle.Desktop.ViewModels
{
    internal sealed class EnvironmentInfoViewModel : ObservableObject, IDisposable
    {
        private readonly Func<Task<string>> _collect;
        private readonly Action<string> _copy;
        private string _report = string.Empty;
        private string _statusText = "点击重新检测，获取当前运行环境";
        private bool _isLoading, _hasError, _disposed;

        internal EnvironmentInfoViewModel(Func<Task<string>> collect, Action<string> copy)
        {
            _collect = collect ?? throw new ArgumentNullException(nameof(collect));
            _copy = copy ?? throw new ArgumentNullException(nameof(copy));
            RefreshCommand = new AsyncRelayCommand(RefreshAsync, () => !_disposed && !IsLoading);
            CopyCommand = new RelayCommand(Copy, () => !_disposed && !IsLoading && Report.Length != 0);
        }

        public string Report { get => _report; private set => SetProperty(ref _report, value); }
        public string StatusText { get => _statusText; private set => SetProperty(ref _statusText, value); }
        public bool HasError { get => _hasError; private set => SetProperty(ref _hasError, value); }
        public bool IsLoading
        {
            get => _isLoading;
            private set
            {
                if (!SetProperty(ref _isLoading, value)) return;
                RefreshCommand.NotifyCanExecuteChanged();
                CopyCommand.NotifyCanExecuteChanged();
            }
        }
        public IAsyncRelayCommand RefreshCommand { get; }
        public RelayCommand CopyCommand { get; }

        private async Task RefreshAsync()
        {
            if (_disposed || IsLoading) return;
            Report = string.Empty;
            HasError = false;
            IsLoading = true;
            StatusText = "正在检测硬件与运行组件…";
            try
            {
                var report = await _collect();
                if (_disposed) return;
                if (string.IsNullOrWhiteSpace(report)) throw new InvalidOperationException("环境信息为空");
                Report = report;
                StatusText = "检测完成，可一键复制全部信息";
            }
            catch (Exception error)
            {
                new LogService().Error("检测运行环境失败", error);
                if (_disposed) return;
                HasError = true;
                StatusText = "检测失败，请点击重新检测";
            }
            finally { if (!_disposed) IsLoading = false; }
        }

        private void Copy()
        {
            if (!CopyCommand.CanExecute(null)) return;
            try
            {
                _copy(Report);
                HasError = false;
                StatusText = "环境信息已复制";
            }
            catch (Exception error)
            {
                new LogService().Error("复制环境信息失败", error);
                HasError = true;
                StatusText = "复制失败，请重试";
            }
        }

        public void Dispose()
        {
            _disposed = true;
            RefreshCommand.NotifyCanExecuteChanged();
            CopyCommand.NotifyCanExecuteChanged();
        }
    }
}
