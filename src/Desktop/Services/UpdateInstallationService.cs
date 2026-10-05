using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Threading;
using System.Threading.Tasks;

namespace T7.Rekindle.Desktop.Services
{
    internal sealed class UpdateInstallationService
    {
        private readonly Func<Task<bool>> _requestClose;
        private readonly Action _saveSettings;
        private readonly Action<string> _startInstaller;
        private readonly Action _closeApplication;
        private int _installing;

        internal UpdateInstallationService(Func<Task<bool>> requestClose, Action saveSettings,
            Action<string> startInstaller, Action closeApplication)
        {
            _requestClose = requestClose ?? throw new ArgumentNullException(nameof(requestClose));
            _saveSettings = saveSettings ?? throw new ArgumentNullException(nameof(saveSettings));
            _startInstaller = startInstaller ?? throw new ArgumentNullException(nameof(startInstaller));
            _closeApplication = closeApplication ?? throw new ArgumentNullException(nameof(closeApplication));
        }

        internal static ProcessStartInfo CreateStartInfo(string installerPath, string launcherDirectory, int launcherProcessId)
        {
            if (launcherProcessId <= 0) throw new ArgumentOutOfRangeException(nameof(launcherProcessId));
            return new ProcessStartInfo(installerPath)
            {
                UseShellExecute = true,
                Arguments = "/SILENT /SP- /NORESTART /NORESTARTAPPLICATIONS /DIR=\"" + Path.GetFullPath(launcherDirectory)
                    + "\" /LAUNCHERPID=" + launcherProcessId.ToString(CultureInfo.InvariantCulture)
            };
        }

        internal async Task<bool> InstallAsync(string path)
        {
            if (Interlocked.CompareExchange(ref _installing, 1, 0) != 0) return false;
            try
            {
                if (!File.Exists(path) || !string.Equals(Path.GetExtension(path), ".exe", StringComparison.OrdinalIgnoreCase))
                    throw new FileNotFoundException("已下载的安装包不存在，请重新下载。", path);
                if (!await _requestClose().ConfigureAwait(true)) return false;
                _saveSettings();
                _startInstaller(path);
                _closeApplication();
                return true;
            }
            finally { Interlocked.Exchange(ref _installing, 0); }
        }
    }
}
