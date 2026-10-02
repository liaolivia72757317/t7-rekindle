using System;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop.Services;

namespace T7.Rekindle.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel : ObservableObject, IDisposable
    {
        private readonly INativeBridge _bridge;
        private readonly SettingsService _settings;
        private readonly IDesktopInteraction _interaction;
        private readonly Func<string, Task<ClientDirectoryResult>> _inspectDirectory;
        private readonly Func<string, CancellationToken, Task<ClientDirectoryResult>> _locateDirectory;
        private readonly LogService _log = new LogService();
        private readonly DispatcherTimer _poller;
        private readonly bool _darkTheme;
        private string _clientDirectory;
        private string _playerName;
        private bool _hasEditedPlayerName;
        private string _savedDirectory;
        private string _savedName;
        private double _windowWidth;
        private double _windowHeight;
        private string _settingsFeedback;
        private string _noticeText = string.Empty;
        private bool _isNoticeError;
        private int _selectedPage;
        private bool _disposed;
        private bool _logsExpanded;
        private SessionSnapshot _snapshot = new SessionSnapshot { State = SessionState.Idle };

        public MainWindowViewModel(NativeBridgeService bridge, SettingsService settings, UserSettings initial, string settingsWarning = null)
            : this((INativeBridge)bridge, settings, initial, settingsWarning) { }

        public MainWindowViewModel(INativeBridge bridge, SettingsService settings, UserSettings initial, string settingsWarning = null)
            : this(bridge, settings, initial, settingsWarning,
                path => Task.Run(() => ClientDirectoryService.Inspect(path)), new DesktopInteraction()) { }

        internal MainWindowViewModel(INativeBridge bridge, SettingsService settings, UserSettings initial,
            string settingsWarning, Func<string, Task<ClientDirectoryResult>> inspectDirectory, IDesktopInteraction interaction,
            Func<string, CancellationToken, Task<ClientDirectoryResult>> locateDirectory = null)
        {
            _bridge = bridge ?? throw new ArgumentNullException(nameof(bridge));
            _settings = settings ?? throw new ArgumentNullException(nameof(settings));
            _inspectDirectory = inspectDirectory;
            _locateDirectory = locateDirectory ?? ((path, token) => Task.Run(() => ClientDirectoryService.Locate(path, token), token));
            _interaction = interaction;
            var loaded = initial ?? new UserSettings();
            _clientDirectory = loaded.ClientDirectory;
            _playerName = loaded.PlayerName;
            _savedDirectory = loaded.ClientDirectory;
            _savedName = loaded.PlayerName;
            _windowWidth = Math.Max(856, loaded.WindowWidth);
            _windowHeight = Math.Max(659, loaded.WindowHeight);
            _darkTheme = loaded.DarkTheme;
            _settingsFeedback = "完成目录与名称配置后即可启动";
            _noticeText = settingsWarning ?? string.Empty;
            _isNoticeError = _noticeText.Length != 0;
            BrowseCommand = new RelayCommand(Browse, () => !AreSessionFieldsLocked);
            CheckCommand = new AsyncRelayCommand(CheckAsync, () => !IsBusy && !_isValidating
                && NativeBridgeContract.IsUtf8PathAcceptable(ClientDirectory));
            StartCommand = new AsyncRelayCommand(StartAsync, () => CanStart);
            MainActionCommand = new AsyncRelayCommand(ExecuteMainActionAsync,
                () => !IsBusy && !_isValidating && (!_directoryResult.IsValid || !HasPlayerNameError));
            CancelCommand = new RelayCommand(Cancel, () => CanCancel);
            StopCommand = new AsyncRelayCommand(StopAsync, () => CanStop);
            ShowHomeCommand = new RelayCommand(() => SelectedPage = 0);
            ShowSettingsCommand = new RelayCommand(() => SelectedPage = 2);
            ToggleLogsCommand = new RelayCommand(() => LogsExpanded = !LogsExpanded);
            OpenLogsCommand = new RelayCommand(OpenLogs);
            CopyLogsCommand = new RelayCommand(CopyLogs);
            About = new AboutViewModel(interaction);
            var dispatcher = Application.Current?.Dispatcher ?? Dispatcher.CurrentDispatcher;
            _poller = new DispatcherTimer(TimeSpan.FromMilliseconds(250), DispatcherPriority.Background,
                (_, __) => Poll(), dispatcher);
            _poller.Start();
            ValidationTask = ValidateDirectoryAsync(false);
        }

        public string ClientDirectory
        {
            get => _clientDirectory;
            set => SetClientDirectory(value, false);
        }

        private void SetClientDirectory(string value, bool scanDescendants)
        {
            if (AreSessionFieldsLocked) return;
            var changed = SetProperty(ref _clientDirectory, value ?? string.Empty, nameof(ClientDirectory));
            if (!changed && !scanDescendants) return;
            _failureMessage = string.Empty;
            _completedMessage = string.Empty;
            SettingsFeedback = "目录与名称验证通过后会自动保存";
            ValidationTask = ValidateDirectoryAsync(!scanDescendants, scanDescendants);
        }

        public string PlayerName
        {
            get => _playerName;
            set
            {
                if (AreSessionFieldsLocked || !SetProperty(ref _playerName, value ?? string.Empty)) return;
                _hasEditedPlayerName = true;
                OnPropertyChanged(nameof(PlayerNameError));
                OnPropertyChanged(nameof(HasPlayerNameError));
                OnPropertyChanged(nameof(ShowPlayerNameError));
                SaveValidatedFields();
                UpdatePresentation();
            }
        }

        public string PlayerNameError => PlayerNameRules.Validate(PlayerName);
        public bool HasPlayerNameError => PlayerNameError.Length != 0;
        public bool ShowPlayerNameError => HasPlayerNameError && (PlayerName.Length != 0 || _hasEditedPlayerName);
        public string SettingsFeedback { get => _settingsFeedback; private set => SetProperty(ref _settingsFeedback, value); }
        public string NoticeText
        {
            get => _noticeText;
            private set { if (SetProperty(ref _noticeText, value)) OnPropertyChanged(nameof(HasNotice)); }
        }
        public bool HasNotice => NoticeText.Length != 0;
        public bool IsNoticeError { get => _isNoticeError; private set => SetProperty(ref _isNoticeError, value); }
        public int SelectedPage
        {
            get => _selectedPage;
            set
            {
                if (!SetProperty(ref _selectedPage, value)) return;
                OnPropertyChanged(nameof(IsHomeSelected));
                OnPropertyChanged(nameof(IsSettingsSelected));
                OnPropertyChanged(nameof(IsAboutSelected));
            }
        }
        public bool IsHomeSelected { get => SelectedPage == 0; set { if (value) SelectedPage = 0; } }
        public bool IsSettingsSelected { get => SelectedPage == 2; set { if (value) SelectedPage = 2; } }
        public bool IsAboutSelected { get => SelectedPage == 1; set { if (value) SelectedPage = 1; } }
        public bool LogsExpanded
        {
            get => _logsExpanded;
            set { if (SetProperty(ref _logsExpanded, value)) OnPropertyChanged(nameof(LogToggleText)); }
        }
        public string LogToggleText => LogsExpanded ? "收起本次记录" : "查看本次记录";
        public bool IsBusy => _operationActive || (_snapshot.State != SessionState.Idle && _snapshot.State != SessionState.Failed);
        public bool AreSessionFieldsLocked => _operationActive ? _activeKind != OperationKind.Check
            : IsBusy && _snapshot.State != SessionState.Checking;
        public bool CanClose => !IsBusy;
        public bool CanStart => !IsBusy && !_isValidating && _directoryResult.IsValid && !HasPlayerNameError && !_hasSaveError;
        public bool CanCancel => _operationCancellation != null && !_operationCancellation.IsCancellationRequested
            && (_snapshot.State == SessionState.StartingRuntime || _snapshot.State == SessionState.Checking);
        public bool CanStop => !_operationActive && (_snapshot.State == SessionState.Running || _snapshot.State == SessionState.FailedCleaning);
        public string StopButtonText => _snapshot.State == SessionState.FailedCleaning ? "重试清理" : "结束游戏";
        public string CancelButtonText => _activeKind == OperationKind.Check ? "取消检查" : "取消启动";
        public AboutViewModel About { get; }
        internal Task ValidationTask { get; private set; }
        public RelayCommand BrowseCommand { get; }
        public IAsyncRelayCommand CheckCommand { get; }
        public IAsyncRelayCommand StartCommand { get; }
        public IAsyncRelayCommand MainActionCommand { get; }
        public RelayCommand CancelCommand { get; }
        public IAsyncRelayCommand StopCommand { get; }
        public RelayCommand ShowHomeCommand { get; }
        public RelayCommand ShowSettingsCommand { get; }
        public RelayCommand ToggleLogsCommand { get; }
        public RelayCommand OpenLogsCommand { get; }
        public RelayCommand CopyLogsCommand { get; }

        private Task ExecuteMainActionAsync()
        {
            if (!_directoryResult.IsValid)
            {
                ShowSettingsCommand.Execute(null);
                return Task.CompletedTask;
            }
            if (_hasSaveError)
            {
                if (SaveValidatedFields()) _failureMessage = string.Empty;
                UpdatePresentation();
                return Task.CompletedTask;
            }
            return StartCommand.ExecuteAsync(null);
        }

        private void Browse()
        {
            try
            {
                if (_isScanningDirectory) _validationCancellation?.Cancel();
                var selected = _interaction.SelectDirectory(_directoryResult.IsValid ? _directoryResult.Root : _savedDirectory);
                if (selected != null) SetClientDirectory(selected, true);
            }
            catch (Exception error) { ReportUiError("打开目录选择器失败", error); }
        }

        private void ReportUiError(string action, Exception error)
        {
            _log.Error(action, error);
            ShowNotice(action + "：" + error.Message, true);
        }

        private void ShowNotice(string message, bool isError)
        {
            IsNoticeError = isError;
            NoticeText = message;
        }

        public void SaveSettings(double windowWidth = 0, double windowHeight = 0)
        {
            if (!double.IsNaN(windowWidth) && !double.IsInfinity(windowWidth) && windowWidth > 0)
                _windowWidth = Math.Max(856, Math.Min(4096, windowWidth));
            if (!double.IsNaN(windowHeight) && !double.IsInfinity(windowHeight) && windowHeight > 0)
                _windowHeight = Math.Max(659, Math.Min(4096, windowHeight));
            _settings.Save(CreateSettings(_savedDirectory, _savedName));
        }

        private UserSettings CreateSettings(string directory, string name) => new UserSettings
        {
            ClientDirectory = directory,
            PlayerName = name,
            DarkTheme = _darkTheme,
            WindowWidth = _windowWidth,
            WindowHeight = _windowHeight
        };

        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;
            _poller.Stop();
            _validationCancellation?.Cancel();
            _operationCancellation?.Cancel();
        }
    }
}
