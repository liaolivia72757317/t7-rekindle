using System;
using System.Diagnostics;
using System.IO;
using System.Threading.Tasks;
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
        void ShowEnvironmentInfo();
    }

    public sealed class DesktopInteraction : IDesktopInteraction
    {
        internal void ShowRecentNotices(ViewModels.NoticeCenter notices) => ShowModal(new RecentNoticesDialog(notices));
        internal void ShowDiagnostics(ViewModels.MainWindowViewModel model) => ShowModal(new TextDialog(model));
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

        public bool Confirm(string message)
        {
            var closing = message.Contains("关闭会") || message.Contains("并关闭");
            return ConfirmAction(closing ? "结束会话并关闭？" : "结束游戏？", message, closing ? "结束并关闭" : "结束游戏");
        }

        internal static bool ConfirmAction(string title, string message, string action) =>
            ShowModal(new ConfirmationDialog(title, message, action)) == true;

        internal static bool? ShowModal(Window dialog)
        {
            var focus = System.Windows.Input.Keyboard.FocusedElement;
            dialog.Owner = Application.Current?.MainWindow;
            try { return dialog.ShowDialog(); }
            finally { if (focus is UIElement element && element.IsVisible && element.IsEnabled) element.Focus(); }
        }

        public void ShowText(string title, string text)
        {
            var dialog = new TextDialog(title, text, false, title == "启动诊断");
            ShowModal(dialog);
        }

        public void ShowMarkdown(string title, string markdown)
        {
            ShowModal(new TextDialog(title, markdown, true));
        }

        public void CopyText(string text) => Clipboard.SetText(text);

        public void ShowEnvironmentInfo() => ShowModal(new EnvironmentInfoDialog());

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

        internal static Task<bool> InstallUpdateAsync(string path)
        {
            if (!(Application.Current?.MainWindow is MainWindow window))
                throw new InvalidOperationException("未找到启动器主窗口。");
            return window.InstallUpdateAsync(path);
        }
    }
}
