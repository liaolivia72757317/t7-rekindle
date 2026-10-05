using System;
using Microsoft.Win32;

namespace T7.Rekindle.Desktop.Services
{
    internal static class WindowsStartupService
    {
        private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
        internal static void SetEnabled(bool enabled)
        {
            using (var key = Registry.CurrentUser.CreateSubKey(RunKey, true))
            {
                if (key == null) throw new InvalidOperationException("登录启动项暂不可用。");
                if (enabled)
                {
                    var executable = typeof(App).Assembly.Location;
                    key.SetValue("T7-Rekindle", "\"" + executable + "\"", RegistryValueKind.String);
                }
                else key.DeleteValue("T7-Rekindle", false);
            }
        }
    }
}
