using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Threading.Tasks;
using System.Windows;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;

namespace T7.Rekindle.Desktop
{
    public partial class MainWindow : Window
    {
        private bool _closeAfterCleanup;
        private bool _closePending;
        private readonly WindowLayoutController _layout;
        private TrayService _tray;
        private bool _observedRunning;

        public MainWindow()
        {
            InitializeComponent();
            _layout = new WindowLayoutController(this, error =>
            {
                new LogService().Error("调整窗口工作区失败", error);
                (DataContext as MainWindowViewModel)?.Notices.Publish("window.layout", "窗口适配失败，请重新打开启动器", NoticeSeverity.Warning);
            });
            DataContextChanged += OnDataContextChanged;
            Activated += (_, __) => SetNoticeActive(true);
            Deactivated += (_, __) => SetNoticeActive(false);
        }

        private void OnViewportSizeChanged(object sender, SizeChangedEventArgs e)
        {
            var compact = e.NewSize.Height < 600;
            SidebarBrandRow.Height = new GridLength(compact ? 124 : 180);
            foreach (System.Windows.Controls.RadioButton item in SidebarNavigation.Children) item.Height = compact ? 48 : 60;
        }

        private void OnMinimize(object sender, RoutedEventArgs e) => WindowState = WindowState.Minimized;
        private void OnCloseClick(object sender, RoutedEventArgs e) => Close();

        private void OnDataContextChanged(object sender, DependencyPropertyChangedEventArgs e)
        {
            if (e.OldValue is MainWindowViewModel previous)
            {
                previous.PropertyChanged -= OnModelPropertyChanged;
                previous.GameSessionEnded -= OnGameSessionEnded;
            }
            _observedRunning = false;
            if (e.NewValue is MainWindowViewModel current)
            {
                current.PropertyChanged += OnModelPropertyChanged;
                current.GameSessionEnded += OnGameSessionEnded;
            }
        }

        private void OnGameSessionEnded()
        {
            if (_closePending || _closeAfterCleanup || !IsLoaded) return;
            try
            {
                if (_tray != null) _tray.Restore();
                else
                {
                    Show();
                    if (WindowState == WindowState.Minimized) WindowState = WindowState.Normal;
                    Activate();
                }
            }
            catch (Exception error)
            {
                new LogService().Error("恢复启动器窗口失败", error);
                (DataContext as MainWindowViewModel)?.Notices.Publish("window.restore-failed", "恢复启动器窗口失败，请重新打开启动器", NoticeSeverity.Warning);
            }
        }

        private void OnModelPropertyChanged(object sender, PropertyChangedEventArgs e)
        {
            if (e.PropertyName != nameof(MainWindowViewModel.IsManagedGameRunning)) return;
            var model = (MainWindowViewModel)sender;
            if (model.IsManagedGameRunning && !_observedRunning && model.MinimizeToTray && IsVisible)
            {
                try { (_tray ?? (_tray = new TrayService(this))).HideToTray(); }
                catch (Exception error)
                {
                    new LogService().Error("最小化到托盘失败", error);
                    model.Notices.Publish("tray.failed", "最小化到托盘失败，窗口保持打开", NoticeSeverity.Warning);
                }
            }
            _observedRunning = model.IsManagedGameRunning;
        }

        private void SetNoticeActive(bool active)
        {
            if (!(DataContext is MainWindowViewModel model)) return;
            model.Notices.Tick(DateTime.UtcNow);
            model.Notices.IsActive = active;
        }

        private void OnBrandLogoFailed(object sender, ExceptionRoutedEventArgs e)
        {
            BrandLogo.Visibility = Visibility.Collapsed;
            BrandFallback.Visibility = Visibility.Visible;
        }

        private void OnSceneArtworkFailed(object sender, ExceptionRoutedEventArgs e)
        {
            SceneArtwork.Visibility = Visibility.Collapsed;
        }

        private async void OnClosing(object sender, CancelEventArgs e)
        {
            if (_closeAfterCleanup)
            {
                return;
            }
            if (_closePending)
            {
                e.Cancel = true;
                return;
            }

            var model = DataContext as MainWindowViewModel;
            if (model != null && (!model.CanClose || model.About.IsUpdating))
            {
                e.Cancel = true;
                _closePending = true;
                try
                {
                    if (await model.RequestCloseAsync().ConfigureAwait(true))
                    {
                        await model.About.CancelUpdateDownloadAsync().ConfigureAwait(true);
                        model.SaveSettings(ActualWidth, ActualHeight);
                        _closeAfterCleanup = true;
                        await Dispatcher.InvokeAsync(new Action(Close));
                    }
                }
                catch (Exception error)
                {
                    MessageBox.Show(error.Message, "T7-Rekindle", MessageBoxButton.OK, MessageBoxImage.Error);
                }
                finally
                {
                    _closePending = false;
                }
            }
            else
            {
                try
                {
                    model?.SaveSettings(ActualWidth, ActualHeight);
                }
                catch (Exception error)
                {
                    MessageBox.Show(error.Message, "T7-Rekindle", MessageBoxButton.OK, MessageBoxImage.Error);
                    e.Cancel = true;
                }
            }
        }

        private void OnClosed(object sender, System.EventArgs e)
        {
            _layout.Dispose();
            _tray?.Dispose();
            if (DataContext is MainWindowViewModel model)
            {
                model.PropertyChanged -= OnModelPropertyChanged;
                model.GameSessionEnded -= OnGameSessionEnded;
            }
            (DataContext as System.IDisposable)?.Dispose();
        }

        internal async Task<bool> InstallUpdateAsync(string installerPath)
        {
            if (_closePending || _closeAfterCleanup) return false;
            if (!(DataContext is MainWindowViewModel model)) throw new InvalidOperationException("启动器状态尚未就绪。");
            if (!DesktopInteraction.ConfirmAction("安装更新？", "更新将覆盖当前启动器目录。结束当前游戏并退出启动器后，将直接开始安装。", "退出并安装")) return false;
            _closePending = true;
            try
            {
                var installation = new UpdateInstallationService(model.RequestCloseAsync,
                    () => model.SaveSettings(ActualWidth, ActualHeight),
                    path =>
                    {
                        using (var launcher = Process.GetCurrentProcess())
                        using (var installer = Process.Start(UpdateInstallationService.CreateStartInfo(path, AppContext.BaseDirectory, launcher.Id)))
                        {
                            if (installer == null) throw new InvalidOperationException("安装程序未成功启动。");
                        }
                    },
                    () =>
                    {
                        _closeAfterCleanup = true;
                        Close();
                    });
                return await installation.InstallAsync(installerPath).ConfigureAwait(true);
            }
            finally { _closePending = false; }
        }
    }
}
