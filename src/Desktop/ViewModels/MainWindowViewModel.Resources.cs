using System;
using System.Threading.Tasks;
using T7R.Core;
using T7R.Desktop.Services;

namespace T7R.Desktop.ViewModels
{
    public sealed partial class MainWindowViewModel
    {
        private string _clientVersion = "";
        private string _resourceCheckText = "未检查";

        public string ClientVersionText => "客户端版本：" + (_clientVersion.Length == 0 ? "未识别" : _clientVersion);
        public string ResourceCheckText => _resourceCheckText;

        private static bool IsResourceFailure(string message) => message?.Contains("[T7R_RESOURCE:") == true;

        private void ResetClientResources()
        {
            _clientVersion = "";
            _resourceCheckText = "未检查";
            _failureMessage = _completedMessage = "";
            NotifyClientResources();
        }

        private void NotifyClientResources()
        {
            OnPropertyChanged(nameof(ClientVersionText));
            OnPropertyChanged(nameof(ResourceCheckText));
        }

        private async Task RefreshClientVersionAsync(ClientDirectoryResult directory, int version)
        {
            if (!directory.IsValid) return;
            ClientVersionResult result;
            try { result = await _clientEnvironment.ReadVersionAsync(directory.Root); }
            catch (Exception error)
            {
                result = new ClientVersionResult("", "update.lst: " + error.GetType().Name + ": " + error.Message);
            }
            if (_disposed || version != _validationVersion) return;
            _clientVersion = result.Version;
            NotifyClientResources();
            AppendLog(result.Diagnostic.Length == 0 ? DiagnosticLogLevel.Info : DiagnosticLogLevel.Warn,
                ClientVersionText + (result.Diagnostic.Length == 0 ? "" : "; " + result.Diagnostic));
        }

        private void CompleteResourceCheck(OperationStatus status, string error)
        {
            _resourceCheckText = status == OperationStatus.Succeeded ? "检查通过"
                : IsResourceFailure(error) ? "资源不匹配" : "未检查";
            NotifyClientResources();
        }
    }
}
