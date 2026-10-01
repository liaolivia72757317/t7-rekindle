using System;
using System.ComponentModel;
using System.Windows;
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
    }
}
