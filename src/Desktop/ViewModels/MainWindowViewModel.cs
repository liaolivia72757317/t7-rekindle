using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using T7R.Core;
using T7R.Desktop.Services;

namespace T7R.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel : ObservableObject, IDisposable
    {
        private readonly INativeBridge _bridge;
        private readonly SettingsService _settings;
        private readonly IDesktopInteraction _interaction;
        private readonly IClientEnvironment _clientEnvironment;
        private readonly AnnouncementViewModel _announcements;
        private readonly AutomaticUpdateChecks _updateChecks;
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
                new ClientEnvironment(), new DesktopInteraction()) { }

        internal MainWindowViewModel(INativeBridge bridge, SettingsService settings, UserSettings initial,
            string settingsWarning, IClientEnvironment clientEnvironment, IDesktopInteraction interaction,
            AnnouncementViewModel announcements = null, LauncherUpdateFactory updates = null,
            PreviewAccessService previewAccess = null, Func<PreviewAccessViewModel, bool> requestPreviewAccess = null,
            AccountViewModel account = null)
        {
            _bridge = bridge ?? throw new ArgumentNullException(nameof(bridge));
            Account = account ?? AccountViewModel.CreateDefault();
            _settings = settings ?? throw new ArgumentNullException(nameof(settings));
            _clientEnvironment = clientEnvironment ?? throw new ArgumentNullException(nameof(clientEnvironment));
            _interaction = interaction ?? throw new ArgumentNullException(nameof(interaction));
            _announcements = announcements ?? new AnnouncementViewModel();
            _announcements.PropertyChanged += OnAnnouncementChanged;
            _announcements.LoadFailed += OnAnnouncementFailed;
            var loaded = initial ?? new UserSettings();
            _clientDirectory = loaded.ClientDirectory;
            _playerName = loaded.PlayerName;
            _savedDirectory = loaded.ClientDirectory;
            _savedName = loaded.PlayerName;
            _windowWidth = loaded.WindowWidth;
            _windowHeight = loaded.WindowHeight;
            _darkTheme = loaded.DarkTheme;
            _minimizeToTray = loaded.MinimizeToTray;
            _startWithWindows = loaded.StartWithWindows;
            _skipStartupAnimation = loaded.SkipStartupAnimation;
            _previewAccess = previewAccess ?? new PreviewAccessService(settings.DirectoryPath);
            _requestPreviewAccess = requestPreviewAccess ?? DesktopInteraction.RequestPreviewAccess;
            _updateChannel = _previewAccess.InitializeChannel(settings, out var channelWarning);
            _selectedLogLevel = _updateChannel == UpdateChannel.Preview
                ? DiagnosticLogLevel.Debug : DiagnosticLogLevels.Parse(loaded.LogLevel);
            _bridge.SetLogLevel(_selectedLogLevel);
            LogService.SetMinimumLevel(_selectedLogLevel);
            _previewAccessStatus = _previewAccess.StatusText;
            _preferenceError = channelWarning;
            _settingsFeedback = string.Empty;
            ShowNotice(settingsWarning ?? string.Empty, !string.IsNullOrEmpty(settingsWarning));
            BrowseCommand = new RelayCommand(Browse, () => !AreSessionFieldsLocked);
            CheckCommand = new AsyncRelayCommand(CheckAsync, () => !IsBusy && !_isValidating
                && NativeBridgeContract.IsUtf8PathAcceptable(ClientDirectory));
            StartCommand = new AsyncRelayCommand(StartAsync, () => CanStart);
            MainActionCommand = new AsyncRelayCommand(ExecuteMainActionAsync,
                () => !IsBusy && !_isValidating && !Account.IsRegistrationOpen);
            CancelCommand = new RelayCommand(Cancel, () => CanCancel);
            StopCommand = new AsyncRelayCommand(StopAsync, () => CanStop);
            ShowHomeCommand = new RelayCommand(() => SelectedPage = 0);
            ShowAboutCommand = new RelayCommand(() => IsAboutSelected = true);
            ShowSettingsCommand = new RelayCommand(() => { SettingsTabIndex = 0; SelectedPage = 2; });
            ShowUpdatePageCommand = new RelayCommand(() => IsUpdateSelected = true);
            ToggleLogsCommand = new RelayCommand(() => LogsExpanded = !LogsExpanded);
            OpenLogsCommand = new RelayCommand(OpenLogs);
            CopyLogsCommand = new RelayCommand(CopyLogs);
            About = (updates ?? new LauncherUpdateFactory()).Create(interaction, () => _updateChannel,
                () => _updateChannel != UpdateChannel.Preview || CanStartPreviewOperation);
            _updateChecks = new AutomaticUpdateChecks(About);
            About.ResetUpdateChannel(_updateChannel);
            About.PropertyChanged += OnUpdateActivityChanged;
            About.NoticeRaised += (message, severity, actionText, action) => Notices.Publish(message, message, severity, actionText, action, severity == NoticeSeverity.Warning);
            About.UpdateFinished += OnUpdateFinished;
            ShowRecentNoticesCommand = new RelayCommand(ShowRecentNotices);
            ShowDiagnosticsCommand = new RelayCommand(ShowDiagnostics);
            ShowAnnouncementCommand = new RelayCommand(ShowAnnouncement);
            CopyDirectoryCommand = new RelayCommand(CopyDirectory);
            GameSettings = new GameSettingsViewModel(_bridge, _clientEnvironment, _log, loaded.OutputDeviceId,
                () => new GameSettingsContext(_snapshot, _directoryResult, _disposed, _operationActive, _isValidating, _directoryHasDraft),
                id =>
                {
                    var saved = CreateSettings(_savedDirectory, _savedName);
                    saved.OutputDeviceId = id;
                    _settings.SaveLauncherSettings(saved);
                });
            GameSettings.PropertyChanged += OnGameSettingsChanged;
            var dispatcher = Application.Current?.Dispatcher ?? Dispatcher.CurrentDispatcher;
            _poller = new DispatcherTimer(TimeSpan.FromMilliseconds(250), DispatcherPriority.Background,
                (_, __) => Poll(), dispatcher);
            Account.PropertyChanged += OnAccountChanged;
            OnAccountChanged(Account, new System.ComponentModel.PropertyChangedEventArgs(string.Empty));
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
            if (!changed && !scanDescendants && !_directoryHasDraft && _directorySaveError.Length == 0) return;
            _directoryHasDraft = false;
            _failureMessage = string.Empty;
            _completedMessage = string.Empty;
            SettingsFeedback = string.Empty;
            ValidationTask = ValidateDirectoryAsync(!scanDescendants, scanDescendants);
        }

        public string PlayerName
        {
            get => _playerName;
            set
            {
                if (ArePlayerNameFieldsLocked) return;
                SetProperty(ref _playerName, value ?? string.Empty);
                _hasEditedPlayerName = true;
                OnPropertyChanged(nameof(PlayerNameError));
                OnPropertyChanged(nameof(HasPlayerNameError));
                OnPropertyChanged(nameof(ShowPlayerNameError));
                OnPropertyChanged(nameof(NameFieldError));
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
                OnPropertyChanged(nameof(IsMultiplayerSelected));
                OnPropertyChanged(nameof(IsUpdateSelected));
                if (IsHomeSelected && _announcements.ChecksStarted) _ = RefreshAnnouncementAsync();
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
        public bool IsBusy => _operationActive || _bridge.GetRuntimeStatus().HasSession
            || (_offlineCleanupTask != null && !_offlineCleanupTask.IsCompleted)
            || (_snapshot.State != SessionState.Idle && _snapshot.State != SessionState.Failed);
        public bool AreSessionFieldsLocked => _operationActive ? _activeKind != OperationKind.Check
            : IsBusy && _snapshot.State != SessionState.Checking;
        public bool CanClose => !IsBusy;
        public bool CanStart => !Account.IsRegistrationOpen && !IsBusy && !_isValidating && !_directoryHasDraft && _directoryResult.IsValid && !HasPlayerNameError && !_hasSaveError;
        public bool CanCancel => _operationCancellation != null && !_operationCancellation.IsCancellationRequested
            && (_snapshot.State == SessionState.StartingRuntime || _snapshot.State == SessionState.Checking
                || ((_activeKind == OperationKind.Check || _activeKind == OperationKind.Start)
                    && (_snapshot.State == SessionState.Idle || _snapshot.State == SessionState.Failed)));
        public bool CanStop => !_operationActive && (_bridge.GetRuntimeStatus().HasSession
            || _snapshot.State == SessionState.Running || _snapshot.State == SessionState.FailedCleaning);
        public string StopButtonText => _snapshot.State == SessionState.FailedCleaning ? "重试清理" : "结束游戏";
        public string CancelButtonText => _activeKind == OperationKind.Check ? "取消检查" : "取消启动";
        public AboutViewModel About { get; }
        public AccountViewModel Account { get; }
        internal Task ValidationTask { get; private set; }
        public RelayCommand BrowseCommand { get; }
        public IAsyncRelayCommand CheckCommand { get; }
        public IAsyncRelayCommand StartCommand { get; }
        public IAsyncRelayCommand MainActionCommand { get; }
        public RelayCommand CancelCommand { get; }
        public IAsyncRelayCommand StopCommand { get; }
        public RelayCommand ShowHomeCommand { get; }
        public RelayCommand ShowAboutCommand { get; }
        public RelayCommand ShowSettingsCommand { get; }
        public RelayCommand ShowUpdatePageCommand { get; }
        public RelayCommand ToggleLogsCommand { get; }
        public RelayCommand OpenLogsCommand { get; }
        public RelayCommand CopyLogsCommand { get; }

        internal void StartUpdateChecks(DateTime utcNow)
        {
            if (_disposed || !_updateChecks.Start(utcNow)) return;
            GameSessionEnded += TriggerUpdateCheck;
            TriggerUpdateCheck();
        }

        internal void CheckScheduledUpdate(DateTime utcNow)
        {
            if (!_disposed) _updateChecks.Tick(utcNow);
        }

        private void TriggerUpdateCheck()
        {
            if (!_disposed) _updateChecks.Trigger();
        }

        private void OnUpdateFinished()
        {
            var announce = _updateChecks.Complete(_updateChannel != UpdateChannel.Preview || CanStartPreviewOperation);
            if (_updateChannel == UpdateChannel.Preview && !CanStartPreviewOperation)
            { QueuePreviewAccessCheck(); return; }
            if (!IsUpdateSelected && About.UpdateFailed)
                Notices.Publish("update.failed", "检查更新失败，不影响本地启动", NoticeSeverity.Warning, "重试",
                    () => { IsUpdateSelected = true; About.CheckUpdateCommand.Execute(null); });
            if (!announce) return;
            Notices.Publish("update.available." + About.UpdateReminderIdentity,
                "发现启动器新版本 " + About.UpdateReminderVersion, NoticeSeverity.Info,
                "查看", () => ShowUpdatePageCommand.Execute(null), duration: TimeSpan.FromSeconds(6));
        }

        private Task ExecuteMainActionAsync()
        {
            if (Account.IsRegistrationOpen) return Task.CompletedTask;
            if (!_directoryResult.IsValid || HasPlayerNameError || _directoryHasDraft)
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
            ShowNotice(action + "，请重试", true);
        }

        private void ShowNotice(string message, bool isError)
        {
            Notices.Publish(message, message, isError ? NoticeSeverity.Error : NoticeSeverity.Success);
            IsNoticeError = isError;
            NoticeText = message;
        }

        internal void DismissExpiredNotice(DateTime utcNow)
        {
            Notices.Tick(utcNow);
            if (Notices.Visible.Count == 0) { NoticeText = string.Empty; IsNoticeError = false; }
        }

        public void SaveSettings(double windowWidth = 0, double windowHeight = 0)
        {
            if (!double.IsNaN(windowWidth) && !double.IsInfinity(windowWidth) && windowWidth > 0)
                _windowWidth = Math.Max(480, Math.Min(4096, windowWidth));
            if (!double.IsNaN(windowHeight) && !double.IsInfinity(windowHeight) && windowHeight > 0)
                _windowHeight = Math.Max(320, Math.Min(4096, windowHeight));
            _settings.SaveLauncherSettings(CreateSettings(_savedDirectory, _savedName));
        }

        private UserSettings CreateSettings(string directory, string name) => new UserSettings
        {
            ClientDirectory = directory,
            PlayerName = name,
            DarkTheme = _darkTheme,
            MinimizeToTray = _minimizeToTray,
            StartWithWindows = _startWithWindows,
            SkipStartupAnimation = _skipStartupAnimation,
            OutputDeviceId = SelectedOutputDeviceId,
            LogLevel = DiagnosticLogLevels.Name(_selectedLogLevel),
            UpdateChannel = _updateChannel == UpdateChannel.Preview ? "preview" : "stable",
            WindowWidth = _windowWidth,
            WindowHeight = _windowHeight
        };

        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;
            _announcements.PropertyChanged -= OnAnnouncementChanged;
            _announcements.LoadFailed -= OnAnnouncementFailed;
            _announcements.Dispose();
            _poller.Stop();
            GameSettings.PropertyChanged -= OnGameSettingsChanged;
            About.PropertyChanged -= OnUpdateActivityChanged;
            About.Dispose();
            Account.PropertyChanged -= OnAccountChanged;
            Account.Dispose();
            GameSessionEnded -= TriggerUpdateCheck;
            _validationCancellation?.Cancel();
            _operationCancellation?.Cancel();
            if (_bridge.GetRuntimeStatus().HasSession)
            {
                try { _bridge.StopAsync(CancellationToken.None).GetAwaiter().GetResult(); }
                catch (Exception error) { _log.Error("结束 Rust 战局失败", error); }
            }
        }
    }
}
