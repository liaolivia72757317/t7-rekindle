using System;
using System.ComponentModel;
using System.Threading;
using System.Windows;
using T7R.Desktop.Services;
using T7R.Desktop.ViewModels;
using T7R.Core;

namespace T7R.Desktop
{
    public partial class App : Application
    {
        private NativeBridgeService _bridge;
        private SettingsService _settings;
        private Mutex _singleInstance;
        private bool _darkTheme;
        private ResourceDictionary _themeDictionary;
        private MainWindowViewModel _themeViewModel;
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
            LogService.SetMinimumLevel(DiagnosticLogLevels.Parse(settings.LogLevel));
            _darkTheme = settings.DarkTheme;
            ApplyTheme();
            SystemParameters.StaticPropertyChanged += OnSystemParametersChanged;
            MainWindowViewModel viewModel;
            try
            {
                _bridge = new NativeBridgeService(AppContext.BaseDirectory);
                viewModel = new MainWindowViewModel(_bridge, _settings, settings, _settings.LastWarning);
            }
            catch (Exception error)
            {
                new LogService().Error("启动原生运行时失败", error);
                MessageBox.Show(error.Message, "T7-Rekindle", MessageBoxButton.OK, MessageBoxImage.Error);
                Shutdown(1);
                return;
            }
            InitializeTheme(viewModel);
            var window = new MainWindow { DataContext = viewModel };
            MainWindow = window;
            window.Show();
            viewModel.StartAnnouncementChecks();
            viewModel.StartUpdateChecks(DateTime.UtcNow);
            _ = viewModel.Account.RestoreAsync();
        }

        protected override void OnExit(ExitEventArgs e)
        {
            SystemParameters.StaticPropertyChanged -= OnSystemParametersChanged;
            if (_themeViewModel != null) _themeViewModel.PropertyChanged -= OnThemePreferenceChanged;
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

        internal void InitializeTheme(MainWindowViewModel viewModel)
        {
            if (_themeViewModel != null) _themeViewModel.PropertyChanged -= OnThemePreferenceChanged;
            _themeViewModel = viewModel;
            if (_themeViewModel != null) _themeViewModel.PropertyChanged += OnThemePreferenceChanged;
            _darkTheme = _themeViewModel?.DarkTheme ?? false;
            ApplyTheme();
        }

        private void OnThemePreferenceChanged(object sender, PropertyChangedEventArgs e)
        {
            if (e.PropertyName != nameof(MainWindowViewModel.DarkTheme) || _darkTheme == _themeViewModel.DarkTheme) return;
            _darkTheme = _themeViewModel.DarkTheme;
            ApplyTheme();
        }

        private void ApplyTheme() => ApplyTheme(SystemParameters.HighContrast);

        internal void ApplyTheme(bool highContrast)
        {
            if (_themeDictionary != null)
            {
                Resources.MergedDictionaries.Remove(_themeDictionary);
                _themeDictionary = null;
            }

            if (!highContrast && !_darkTheme)
            {
                return;
            }

            _themeDictionary = new ResourceDictionary
            {
                Source = new Uri(highContrast
                    ? "/T7-Rekindle;component/Resources/Theme.HighContrast.xaml"
                    : "/T7-Rekindle;component/Resources/Theme.Dark.xaml", UriKind.Relative)
            };
            Resources.MergedDictionaries.Add(_themeDictionary);
        }
    }
}
