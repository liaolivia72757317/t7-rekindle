using System;
using System.Diagnostics;
using System.IO;
using System.Windows;
using System.Windows.Interop;
using T7.Rekindle.Desktop.Views;

namespace T7.Rekindle.Desktop.Services
{
    public interface IDesktopInteraction
    {
        string SelectDirectory(string initialDirectory);
        bool Confirm(string message);
        void ShowText(string title, string text);
        void ShowMarkdown(string title, string markdown);
        void CopyText(string text);
        void OpenDirectory(string path);
        void OpenAddress(string address);
        void ShowUpdate(LauncherUpdateInfo info);
    }

    public sealed class DesktopInteraction : IDesktopInteraction
    {
        public string SelectDirectory(string initialDirectory)
        {
            using (var dialog = new System.Windows.Forms.FolderBrowserDialog
            {
                Description = "选择游戏根目录或其上级目录，将自动查找包含 Bin、Data 和 vfs 的客户端",
                SelectedPath = Directory.Exists(initialDirectory) ? initialDirectory : string.Empty,
                ShowNewFolderButton = false
            })
            {
                var owner = Application.Current?.MainWindow;
                var handle = new System.Windows.Forms.NativeWindow();
                if (owner != null) handle.AssignHandle(new WindowInteropHelper(owner).Handle);
                try
                {
                    return dialog.ShowDialog(handle) == System.Windows.Forms.DialogResult.OK ? dialog.SelectedPath : null;
                }
                finally { if (owner != null) handle.ReleaseHandle(); }
            }
        }

        public bool Confirm(string message) => MessageBox.Show(message, "T7-Rekindle",
            MessageBoxButton.YesNo, MessageBoxImage.Warning, MessageBoxResult.No) == MessageBoxResult.Yes;

        public void ShowText(string title, string text)
        {
            var dialog = new TextDialog(title, text) { Owner = Application.Current?.MainWindow };
            dialog.ShowDialog();
        }

        public void ShowMarkdown(string title, string markdown)
        {
            var dialog = new TextDialog(title, markdown, true) { Owner = Application.Current?.MainWindow };
            dialog.ShowDialog();
        }

        public void CopyText(string text) => Clipboard.SetText(text);

        public void OpenDirectory(string path)
        {
            Directory.CreateDirectory(path);
            Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
        }

        public void OpenAddress(string address)
        {
            if (!Uri.TryCreate(address, UriKind.Absolute, out var uri)
                || uri.Scheme != Uri.UriSchemeHttps || uri.UserInfo.Length != 0)
                throw new ArgumentException("链接不是有效的 HTTPS 地址。", nameof(address));
            Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true });
        }

        public void ShowUpdate(LauncherUpdateInfo info)
        {
            new UpdateDialog(info) { Owner = Application.Current?.MainWindow }.ShowDialog();
        }
    }
}
