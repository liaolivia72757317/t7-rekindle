using System;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Collections.Generic;
using Newtonsoft.Json;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;
using T7.Rekindle.Desktop.Views;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class NativeWindowUiTests
    {
        internal static void Run(string output)
        {
            var previous = System.Threading.SynchronizationContext.Current;
            System.Threading.SynchronizationContext.SetSynchronizationContext(new System.Windows.Threading.DispatcherSynchronizationContext());
            var directory = Path.Combine(Path.GetTempPath(), "T7-native-ui-" + Guid.NewGuid().ToString("N"));
            var app = new App(false);
            app.InitializeComponent();
            app.ShutdownMode = ShutdownMode.OnExplicitShutdown;
            MainWindow window = null;
            try
            {
                var bridge = new FakeLauncherBridge();
                using (var model = new MainWindowViewModel(bridge, new SettingsService(directory),
                    new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "玩家", MinimizeToTray = true }, null,
                    path => Task.FromResult(ValidDirectory(path)), new FakeDesktopInteraction()))
                {
                    RunTask(model.ValidationTask);
                    window = new MainWindow { DataContext = model, Opacity = 0, ShowActivated = false, ShowInTaskbar = false };
                    app.MainWindow = window;
                    window.Show();
                    Pump();
                    var handle = new WindowInteropHelper(window).Handle;
                    var monitors = new List<IntPtr>();
                    Assert(EnumDisplayMonitors(IntPtr.Zero, IntPtr.Zero, (IntPtr monitor, IntPtr dc, ref NativeRect rect, IntPtr data) =>
                    { monitors.Add(monitor); return true; }, IntPtr.Zero), "monitor enumeration failed");
                    var measurements = new List<object>();
                    var dpis = new HashSet<uint>();
                    foreach (var monitorHandle in monitors)
                    {
                        var monitor = new MonitorInfo { Size = Marshal.SizeOf(typeof(MonitorInfo)) };
                        Assert(GetMonitorInfo(monitorHandle, ref monitor), "native monitor query failed");
                        Assert(SetWindowPos(handle, IntPtr.Zero, monitor.Work.Left + 16, monitor.Work.Top + 16, 0, 0, 0x0015), "native monitor move failed");
                        SendMessage(handle, 0x0232, IntPtr.Zero, IntPtr.Zero);
                        Pump();
                        Assert(GetWindowRect(handle, out var rect), "native window query failed");
                        var dpi = GetDpiForWindow(handle);
                        dpis.Add(dpi);
                        VerifyCaptionHitTesting(window, handle);
                        Assert(dpi >= 96 && rect.Left >= monitor.Work.Left && rect.Top >= monitor.Work.Top
                            && rect.Right <= monitor.Work.Right && rect.Bottom <= monitor.Work.Bottom,
                            "real HWND extends outside its current monitor work area");
                        measurements.Add(new { dpi, workAreaPx = new[] { monitor.Work.Right - monitor.Work.Left, monitor.Work.Bottom - monitor.Work.Top },
                            windowDIP = new[] { window.Width, window.Height } });
                    }
                    Assert(window.ResizeMode == ResizeMode.CanMinimize && window.IsVisible, "real window lost fixed-size policy");
                    var dialog = new ConfirmationDialog("结束游戏？", "测试说明", "结束游戏")
                    { Owner = window, Opacity = 0, ShowActivated = false };
                    try
                    {
                        dialog.Show();
                        Pump();
                        Assert(GetWindowRect(handle, out var ownerBounds)
                            && GetWindowRect(new WindowInteropHelper(dialog).Handle, out var dialogBounds)
                            && Math.Abs(ownerBounds.Left + ownerBounds.Right - dialogBounds.Left - dialogBounds.Right) <= 2
                            && Math.Abs(ownerBounds.Top + ownerBounds.Bottom - dialogBounds.Top - dialogBounds.Bottom) <= 2,
                            "dialog did not stay centered on its owner");
                    }
                    finally { dialog.Close(); }
                    VerifyGameExitRestoresHome(window, model, bridge);
                    model.MinimizeToTray = true;
                    RunTask(model.StartCommand.ExecuteAsync(null));
                    Assert(bridge.StartCount == 1 && !window.IsVisible, "managed process detection did not hide to the tray");
                    var tray = (TrayService)typeof(MainWindow).GetField("_tray", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(window);
                    Assert(tray != null, "hidden window has no tray recovery service");
                    tray.Restore();
                    Pump();
                    Assert(window.IsVisible && window.WindowState == WindowState.Normal, "tray recovery did not restore the real window");
                    RunTask(model.StopCommand.ExecuteAsync(null));
                    if (output != null)
                    {
                        Directory.CreateDirectory(output);
                        File.WriteAllText(Path.Combine(output, "native-window.json"), JsonConvert.SerializeObject(new
                        { monitors = measurements, trayHideRestore = true, gameExitRestoresHome = true,
                            dialogOwnerCenter = true, realGame = false, mixedDpi = dpis.Count > 1 }, Formatting.Indented));
                    }
                    window.Close(); window = null;
                }
                Console.WriteLine("native window work-area and tray checks passed");
            }
            finally
            {
                window?.Close();
                app.Shutdown();
                System.Threading.SynchronizationContext.SetSynchronizationContext(previous);
                if (Directory.Exists(directory)) Directory.Delete(directory, true);
            }
        }

        private static void VerifyGameExitRestoresHome(MainWindow window, MainWindowViewModel model, FakeLauncherBridge bridge)
        {
            foreach (var minimizeToTray in new[] { false, true })
            {
                model.MinimizeToTray = minimizeToTray;
                foreach (var exit in new[] { "normal", "abnormal", "stop" })
                {
                    RunTask(model.StartCommand.ExecuteAsync(null));
                    model.IsSettingsSelected = true;
                    if (!minimizeToTray) window.WindowState = WindowState.Minimized;
                    Pump();
                    Assert(minimizeToTray ? !window.IsVisible : window.WindowState == WindowState.Minimized,
                        "exit fixture did not hide or minimize the launcher");
                    if (exit == "stop") RunTask(model.StopCommand.ExecuteAsync(null));
                    else
                    {
                        bridge.Snapshot = new SessionSnapshot { State = SessionState.StoppingRuntime };
                        model.Refresh();
                        Assert(model.IsSettingsSelected, "window restored home before session cleanup completed");
                        bridge.Snapshot = new SessionSnapshot
                        {
                            State = exit == "normal" ? SessionState.Idle : SessionState.Failed,
                            Phase = exit == "normal" ? "client-exited" : "client-exited-error",
                            ErrorCode = exit == "normal" ? 0u : 1003u,
                            CleanupComplete = true
                        };
                        model.Refresh();
                    }
                    Pump();
                    Assert(window.IsVisible && window.WindowState == WindowState.Normal && window.IsActive && model.IsHomeSelected,
                        "game exit did not activate the launcher on home: " + exit + ", tray=" + minimizeToTray);
                    model.IsAboutSelected = true;
                    window.WindowState = WindowState.Minimized;
                    model.Refresh();
                    Pump();
                    Assert(model.IsAboutSelected && window.WindowState == WindowState.Minimized,
                        "repeated exit polling stole focus or reset navigation");
                    window.WindowState = WindowState.Normal;
                }
            }
        }

        private static void VerifyCaptionHitTesting(MainWindow window, IntPtr handle)
        {
            Assert(HitTest(window, handle, new Point(108, 30)) == 2, "brand area lost native caption dragging");
            Assert(HitTest(window, handle, new Point(400, 30)) == 2, "main caption lost native dragging");
            var buttonCount = 0;
            foreach (var child in ((Grid)window.Content).Children)
            {
                if (!(child is StackPanel panel) || Grid.GetColumn(panel) != 1) continue;
                foreach (Button button in panel.Children)
                {
                    var position = button.TranslatePoint(new Point(button.ActualWidth / 2, button.ActualHeight / 2), window);
                    Assert(HitTest(window, handle, position) == 1, "caption button was captured by window dragging");
                    buttonCount++;
                }
            }
            Assert(buttonCount == 3, "caption controls are missing");
        }

        private static int HitTest(MainWindow window, IntPtr handle, Point position)
        {
            var screen = window.PointToScreen(position);
            var coordinates = unchecked(((int)screen.Y << 16) | ((int)screen.X & 0xffff));
            return SendMessage(handle, 0x0084, IntPtr.Zero, new IntPtr(coordinates)).ToInt32();
        }

        [StructLayout(LayoutKind.Sequential)] private struct NativeRect { public int Left, Top, Right, Bottom; }
        [StructLayout(LayoutKind.Sequential)] private struct MonitorInfo { public int Size; public NativeRect Monitor, Work; public uint Flags; }
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern bool GetMonitorInfo(IntPtr handle, ref MonitorInfo info);
        [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr handle, out NativeRect rect);
        [DllImport("user32.dll")] private static extern uint GetDpiForWindow(IntPtr handle);
        private delegate bool MonitorCallback(IntPtr monitor, IntPtr dc, ref NativeRect rect, IntPtr data);
        [DllImport("user32.dll")] private static extern bool EnumDisplayMonitors(IntPtr dc, IntPtr clip, MonitorCallback callback, IntPtr data);
        [DllImport("user32.dll")] private static extern bool SetWindowPos(IntPtr window, IntPtr after, int x, int y, int width, int height, uint flags);
        [DllImport("user32.dll")] private static extern IntPtr SendMessage(IntPtr window, int message, IntPtr wParam, IntPtr lParam);
    }
}
