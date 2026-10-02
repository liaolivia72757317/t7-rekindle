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

        public MainWindow()
        {
            InitializeComponent();
            DataContextChanged += OnDataContextChanged;
        }

        private void OnDataContextChanged(object sender, DependencyPropertyChangedEventArgs e)
        {
            if (e.OldValue is MainWindowViewModel previous) previous.PropertyChanged -= OnModelPropertyChanged;
            if (e.NewValue is MainWindowViewModel current) current.PropertyChanged += OnModelPropertyChanged;
        }

        private void OnModelPropertyChanged(object sender, PropertyChangedEventArgs e)
        {
            if (e.PropertyName == nameof(MainWindowViewModel.SelectedPage)) PageScroll.ScrollToTop();
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
            if (model != null && !model.CanClose)
            {
                e.Cancel = true;
                _closePending = true;
                try
                {
                    if (await model.RequestCloseAsync().ConfigureAwait(true))
                    {
                        _closeAfterCleanup = true;
                        model.SaveSettings(ActualWidth, ActualHeight);
                        Close();
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
            if (DataContext is MainWindowViewModel model) model.PropertyChanged -= OnModelPropertyChanged;
            (DataContext as System.IDisposable)?.Dispose();
        }

        internal async Task<bool> InstallUpdateAsync(string installerPath, Action allowDialogClose)
        {
            if (_closePending || _closeAfterCleanup) return false;
            if (!(DataContext is MainWindowViewModel model)) throw new InvalidOperationException("启动器状态尚未就绪。");
            _closePending = true;
            try
            {
                var installation = new UpdateInstallationService(model.RequestCloseAsync,
                    () => model.SaveSettings(ActualWidth, ActualHeight),
                    path =>
                    {
                        using (var installer = Process.Start(new ProcessStartInfo(path) { UseShellExecute = true }))
                        {
                            if (installer == null) throw new InvalidOperationException("安装向导未成功启动。");
                        }
                    },
                    () =>
                    {
                        _closeAfterCleanup = true;
                        allowDialogClose();
                        Close();
                    });
                return await installation.InstallAsync(installerPath).ConfigureAwait(true);
            }
            finally { _closePending = false; }
        }
    }
}
