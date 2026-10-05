using System;
using System.Drawing;
using System.Windows;
using System.Windows.Forms;

namespace T7.Rekindle.Desktop.Services
{
    internal sealed class TrayService : IDisposable
    {
        private readonly Window _window;
        private readonly NotifyIcon _tray;
        private readonly Icon _icon;
        private readonly ContextMenuStrip _menu;

        internal TrayService(Window window)
        {
            _window = window;
            _icon = Icon.ExtractAssociatedIcon(typeof(App).Assembly.Location);
            if (_icon == null) throw new InvalidOperationException("托盘图标加载失败。");
            _menu = new ContextMenuStrip();
            _menu.Items.Add("打开启动器", null, (_, __) => Restore());
            _menu.Items.Add("退出启动器", null, (_, __) => { Restore(); _window.Close(); });
            _tray = new NotifyIcon { Icon = _icon, Text = "T7-Rekindle", ContextMenuStrip = _menu };
            _tray.DoubleClick += (_, __) => Restore();
        }

        internal void HideToTray()
        {
            _tray.Visible = true;
            _window.Hide();
        }

        internal void Restore()
        {
            _window.Show();
            _window.WindowState = WindowState.Normal;
            _window.Activate();
            _tray.Visible = false;
        }

        public void Dispose()
        {
            _tray.Visible = false;
            _tray.Dispose(); _menu.Dispose(); _icon.Dispose();
        }
    }
}
