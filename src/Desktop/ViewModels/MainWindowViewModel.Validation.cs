using System;
using System.Threading;
using System.Threading.Tasks;
using T7.Rekindle.Desktop.Services;

namespace T7.Rekindle.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel
    {
        private ClientDirectoryResult _directoryResult = new ClientDirectoryResult("", "", "请选择游戏根目录。");
        private CancellationTokenSource _validationCancellation;
        private int _validationVersion;
        private bool _isValidating;
        private bool _isScanningDirectory;
        private bool _hasSaveError;
        private bool _directoryHasDraft;

        internal void BeginDirectoryDraft()
        {
            if (_directoryHasDraft || AreSessionFieldsLocked) return;
            _directoryHasDraft = true;
            ++_validationVersion;
            _validationCancellation?.Cancel();
            _isValidating = _isScanningDirectory = false;
            UpdatePresentation();
        }
        public string DirectoryMessage => _isScanningDirectory ? "正在自动定位游戏目录，可重新选择或修改路径…"
            : _isValidating ? "正在检查游戏目录…" : _directoryResult.Message;
        public bool IsDirectoryValid => !_isValidating && _directoryResult.IsValid;
        public bool HasDirectoryError => !_isValidating && !_directoryResult.IsValid && ClientDirectory.Length != 0;
        public string DirectorySymbol => IsDirectoryValid ? "✓" : HasDirectoryError ? "!" : "·";

        private async Task ValidateDirectoryAsync(bool debounce, bool scanDescendants = false)
        {
            var version = ++_validationVersion;
            _validationCancellation?.Cancel();
            var cancellation = new CancellationTokenSource();
            _validationCancellation = cancellation;
            var path = ClientDirectory;
            _isValidating = true;
            _isScanningDirectory = scanDescendants;
            UpdatePresentation();
            try
            {
                if (debounce) await Task.Delay(300, cancellation.Token);
                var result = scanDescendants
                    ? await _locateDirectory(path, cancellation.Token)
                    : await _inspectDirectory(path);
                cancellation.Token.ThrowIfCancellationRequested();
                if (_disposed || version != _validationVersion) return;
                _directoryResult = result;
                _isValidating = false;
                if (result.IsValid) SetProperty(ref _clientDirectory, result.Root, nameof(ClientDirectory));
                SaveValidatedFields();
            }
            catch (OperationCanceledException) when (cancellation.IsCancellationRequested)
            {
                if (!_disposed && version == _validationVersion && scanDescendants)
                    _directoryResult = new ClientDirectoryResult("", "", "已取消目录扫描，请重新选择游戏目录。");
            }
            catch (Exception error)
            {
                if (!_disposed && version == _validationVersion)
                {
                    _directoryResult = new ClientDirectoryResult("", "", "目录检查失败：" + error.Message);
                    _log.Error("目录检查失败", error);
                }
            }
            finally
            {
                if (!_disposed && version == _validationVersion)
                {
                    _isValidating = false;
                    _isScanningDirectory = false;
                    UpdatePresentation();
                }
                if (ReferenceEquals(_validationCancellation, cancellation)) _validationCancellation = null;
                cancellation.Dispose();
            }
        }

        private string _nameSaveError = string.Empty;
        private string _directorySaveError = string.Empty;

        private bool SaveValidatedFields()
        {
            var directory = !_directoryHasDraft && !_isValidating && _directoryResult.IsValid ? _directoryResult.Root : _savedDirectory;
            var name = HasPlayerNameError ? _savedName : PlayerName.Trim();
            if (!HasPlayerNameError && name == _savedName) _nameSaveError = string.Empty;
            if (!_isValidating && !_directoryHasDraft && _directoryResult.IsValid && directory == _savedDirectory) _directorySaveError = string.Empty;
            _hasSaveError = _nameSaveError.Length != 0 || _directorySaveError.Length != 0;
            if (!_hasSaveError) SettingsFeedback = string.Empty;
            if (_savedDirectory == directory && _savedName == name)
            {
                OnPropertyChanged(nameof(NameFieldError));
                OnPropertyChanged(nameof(DirectoryFieldError));
                if (!_hasSaveError) Notices.Resolve("settings.save-failed");
                return !_hasSaveError && !_isValidating && !_directoryHasDraft && _directoryResult.IsValid && !HasPlayerNameError;
            }
            try
            {
                _settings.Save(CreateSettings(directory, name));
                _savedDirectory = directory;
                _savedName = name;
                _hasSaveError = false;
                _nameSaveError = _directorySaveError = SettingsFeedback = string.Empty;
                Notices.Resolve("settings.save-failed");
                OnPropertyChanged(nameof(SavedPlayerName));
                OnPropertyChanged(nameof(SavedClientDirectory));
            }
            catch (Exception error)
            {
                _hasSaveError = true;
                _log.Error("保存设置失败", error);
                SettingsFeedback = "设置未保存，请检查写入权限后重试";
                if (_savedName != name) _nameSaveError = SettingsFeedback;
                if (_savedDirectory != directory) _directorySaveError = SettingsFeedback;
                if (!IsSettingsSelected || SettingsTabIndex != 0)
                    Notices.Publish("settings.save-failed", "设置未保存，请重试", NoticeSeverity.Error, "重试", () =>
                    {
                        ShowSettingsCommand.Execute(null);
                        SaveValidatedFields();
                        UpdatePresentation();
                    });
            }
            OnPropertyChanged(nameof(NameFieldError));
            OnPropertyChanged(nameof(DirectoryFieldError));
            return !_hasSaveError && !_isValidating && _directoryResult.IsValid && !HasPlayerNameError;
        }
    }
}
