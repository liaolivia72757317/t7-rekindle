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
                    _validationCancellation = null;
                    UpdatePresentation();
                }
                cancellation.Dispose();
            }
        }

        private bool SaveValidatedFields()
        {
            _hasSaveError = false;
            if (_isValidating || !_directoryResult.IsValid || HasPlayerNameError)
            {
                SettingsFeedback = "完成目录与名称配置后即可启动";
                return false;
            }
            var name = PlayerName.Trim();
            if (_savedDirectory == _directoryResult.Root && _savedName == name)
            {
                SettingsFeedback = "目录与名称已保存，供下次使用";
                return true;
            }
            try
            {
                _settings.Save(CreateSettings(_directoryResult.Root, name));
                _savedDirectory = _directoryResult.Root;
                _savedName = name;
                SettingsFeedback = "目录与名称已保存，供下次使用";
                return true;
            }
            catch (Exception error)
            {
                _hasSaveError = true;
                _log.Error("保存设置失败", error);
                SettingsFeedback = "保存设置失败，请检查写入权限后重试";
                return false;
            }
        }
    }
}
