using System;
using System.ComponentModel;
using System.Threading;
using System.Windows;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;
using T7.Rekindle.Core;

namespace T7.Rekindle.Desktop
{
    public partial class App : Application
    {
        private NativeBridgeService _bridge;
        private SettingsService _settings;
        private Mutex _singleInstance;
        private bool _darkTheme;
        private ResourceDictionary _themeDictionary;
        private readonly bool _startRuntime;

        public App() : this(true) { }

        internal App(bool startRuntime) { _startRuntime = startRuntime; }

        protected override void OnStartup(StartupEventArgs e)
        {
            base.OnStartup(e);
            if (!_startRuntime) return;
            NativeAbiLayout.Validate();
            bool created;
            _singleInstance = new Mutex(true, @"Local\T7-Rekindle.Desktop", out created);
            if (!created)
            {
                Shutdown(2);
                return;
            }
            _settings = new SettingsService();
            var settings = _settings.Load();
            _darkTheme = settings.DarkTheme;
            ApplyTheme();
            SystemParameters.StaticPropertyChanged += OnSystemParametersChanged;
            try
            {
                _bridge = new NativeBridgeService(AppContext.BaseDirectory);
            }
            catch (Exception error)
            {
                MessageBox.Show(error.Message, "T7-Rekindle", MessageBoxButton.OK, MessageBoxImage.Error);
                Shutdown(1);
                return;
            }
            var viewModel = new MainWindowViewModel(_bridge, _settings, settings, _settings.LastWarning);
            var window = new MainWindow
            {
                Width = Math.Max(856, settings.WindowWidth),
                Height = Math.Max(659, settings.WindowHeight),
                DataContext = viewModel
            };
            var workArea = SystemParameters.WorkArea;
            window.MinWidth = Math.Min(window.MinWidth, workArea.Width);
            window.MinHeight = Math.Min(window.MinHeight, workArea.Height);
            window.Width = Math.Min(window.Width, workArea.Width);
            window.Height = Math.Min(window.Height, workArea.Height);
            MainWindow = window;
            window.Show();
        }

        protected override void OnExit(ExitEventArgs e)
        {
            SystemParameters.StaticPropertyChanged -= OnSystemParametersChanged;
            _bridge?.Dispose();
            _singleInstance?.Dispose();
            base.OnExit(e);
        }

        private void OnSystemParametersChanged(object sender, PropertyChangedEventArgs e)
        {
            if (string.IsNullOrEmpty(e.PropertyName) || e.PropertyName == nameof(SystemParameters.HighContrast))
            {
                ApplyTheme();
            }
        }

        private void ApplyTheme()
        {
            if (_themeDictionary != null)
            {
                Resources.MergedDictionaries.Remove(_themeDictionary);
                _themeDictionary = null;
            }

            if (!SystemParameters.HighContrast && !_darkTheme)
            {
                return;
            }

            _themeDictionary = new ResourceDictionary
            {
                Source = new Uri(SystemParameters.HighContrast
                    ? "/T7-Rekindle;component/Resources/Theme.HighContrast.xaml"
                    : "/T7-Rekindle;component/Resources/Theme.Dark.xaml", UriKind.Relative)
            };
            Resources.MergedDictionaries.Add(_themeDictionary);
        }
    }
}
