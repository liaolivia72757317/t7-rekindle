using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Threading;

namespace T7.Rekindle.Desktop.Services
{
    internal sealed class WindowLayoutController : IDisposable
    {
        private readonly Window _window;
        private readonly Action<Exception> _reportError;
        private HwndSource _source;
        private bool _scheduled;
        private bool _disposed;
        private readonly Size _preferredSize;

        internal WindowLayoutController(Window window, Action<Exception> reportError, double preferredWidth = 1200, double preferredHeight = 900)
        {
            _window = window; _reportError = reportError;
            _preferredSize = new Size(preferredWidth, preferredHeight);
            window.SourceInitialized += OnSourceInitialized;
        }

        internal static Size CalculateSize(double workWidthPx, double workHeightPx, double scale, double preferredWidth = 1200, double preferredHeight = 900)
        {
            if (scale <= 0 || double.IsNaN(scale)) throw new ArgumentOutOfRangeException(nameof(scale));
            return new Size(Math.Max(4, Math.Floor(Math.Min(preferredWidth, workWidthPx / scale - 32) / 4) * 4),
                Math.Max(4, Math.Floor(Math.Min(preferredHeight, workHeightPx / scale - 32) / 4) * 4));
        }

        private void OnSourceInitialized(object sender, EventArgs e)
        {
            _source = HwndSource.FromHwnd(new WindowInteropHelper(_window).Handle);
            _source.AddHook(OnMessage);
            ScheduleFit(true);
        }

        private IntPtr OnMessage(IntPtr hwnd, int message, IntPtr wParam, IntPtr lParam, ref bool handled)
        {
            // WPF processes WM_DPICHANGED first; re-fit using the new HWND DPI afterwards.
            if (message == 0x02E0 || message == 0x0232 || message == 0x007E || message == 0x001A) ScheduleFit(false);
            return IntPtr.Zero;
        }

        private void ScheduleFit(bool center)
        {
            if (_scheduled || _disposed) return;
            _scheduled = true;
            _window.Dispatcher.BeginInvoke(DispatcherPriority.Loaded, new Action(() =>
            {
                _scheduled = false;
                if (_disposed || _window.WindowState == WindowState.Minimized) return;
                try { Fit(center); }
                catch (Exception error) { _reportError(error); }
            }));
        }

        private void Fit(bool center)
        {
            var handle = new WindowInteropHelper(_window).Handle;
            var owner = center && _window.Owner != null ? new WindowInteropHelper(_window.Owner).Handle : IntPtr.Zero;
            var monitor = MonitorFromWindow(owner != IntPtr.Zero ? owner : handle, 2);
            var info = new MonitorInfo { Size = Marshal.SizeOf(typeof(MonitorInfo)) };
            if (!GetMonitorInfo(monitor, ref info) || !GetWindowRect(handle, out var current)) throw new Win32Exception();
            var anchor = info.Work;
            if (owner != IntPtr.Zero && !GetWindowRect(owner, out anchor)) throw new Win32Exception();
            var dpi = GetDpiForWindow(handle);
            if (dpi == 0) throw new InvalidOperationException("窗口 DPI 暂不可用。");
            var scale = dpi / 96.0;
            var work = info.Work;
            var size = CalculateSize(work.Right - work.Left, work.Bottom - work.Top, scale, _preferredSize.Width, _preferredSize.Height);
            _window.MinWidth = Math.Min(_window.MinWidth, size.Width);
            _window.MinHeight = Math.Min(_window.MinHeight, size.Height);
            _window.Width = size.Width; _window.Height = size.Height;
            var width = (int)Math.Round(size.Width * scale);
            var height = (int)Math.Round(size.Height * scale);
            var margin = (int)Math.Round(16 * scale);
            var targetLeft = center ? anchor.Left + (anchor.Right - anchor.Left - width) / 2 : current.Left;
            var targetTop = center ? anchor.Top + (anchor.Bottom - anchor.Top - height) / 2 : current.Top;
            var left = Math.Max(work.Left + margin, Math.Min(targetLeft, work.Right - width - margin));
            var top = Math.Max(work.Top + margin, Math.Min(targetTop, work.Bottom - height - margin));
            if (!SetWindowPos(handle, IntPtr.Zero, left, top, width, height, 0x0014)) throw new Win32Exception();
        }

        public void Dispose()
        {
            _disposed = true;
            _window.SourceInitialized -= OnSourceInitialized;
            _source?.RemoveHook(OnMessage);
        }

        [StructLayout(LayoutKind.Sequential)] private struct NativeRect { public int Left, Top, Right, Bottom; }
        [StructLayout(LayoutKind.Sequential)] private struct MonitorInfo { public int Size; public NativeRect Monitor, Work; public uint Flags; }
        [DllImport("user32.dll")] private static extern IntPtr MonitorFromWindow(IntPtr window, uint flags);
        [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern bool GetMonitorInfo(IntPtr monitor, ref MonitorInfo info);
        [DllImport("user32.dll", SetLastError = true)] private static extern bool GetWindowRect(IntPtr window, out NativeRect rectangle);
        [DllImport("user32.dll")] private static extern uint GetDpiForWindow(IntPtr window);
        [DllImport("user32.dll", SetLastError = true)] private static extern bool SetWindowPos(IntPtr window, IntPtr after, int x, int y, int width, int height, uint flags);
    }
}
