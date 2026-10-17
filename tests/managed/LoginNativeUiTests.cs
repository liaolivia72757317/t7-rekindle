using System;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;
using T7R.Desktop;
using T7R.Desktop.Views;
using T7R.Desktop.Services;
using T7R.Desktop.ViewModels;
using T7R.Core;
using static T7R.ManagedHarness.LauncherTests;

namespace T7R.ManagedHarness
{
    internal static class LoginNativeUiTests
    {
        internal static void Run(string output)
        {
            var previous = SynchronizationContext.Current;
            SynchronizationContext.SetSynchronizationContext(new System.Windows.Threading.DispatcherSynchronizationContext());
            var app = new App(false);
            app.InitializeComponent();
            app.ShutdownMode = ShutdownMode.OnExplicitShutdown;
            var directory = Path.Combine(Path.GetTempPath(), "T7R-login-ui-" + Guid.NewGuid().ToString("N"));
            QqLoginDialog dialog = null;
            try
            {
                Directory.CreateDirectory(directory);
                var page = Path.Combine(directory, "provider.html");
                File.WriteAllText(page, "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>Provider fixture</title>"
                    + "<body style='margin:0;background:white;color:#203d49;font:14px Microsoft YaHei UI'>"
                    + "<main style='padding:70px 32px'><h2>授权页测试内容</h2><p>此本地页面用于验证原生操作按钮的叠加、重载和取消。</p>"
                    + "<input aria-label='输入测试' placeholder='测试键盘焦点'><a href='#page-link'>页面原有入口</a></main></body></html>");
                dialog = new QqLoginDialog(() => CoreWebView2Environment.GetAvailableBrowserVersionString(),
                    null, _ => { throw new InvalidOperationException("native test must not launch an external browser"); }, new Uri(page).AbsoluteUri);
                app.MainWindow = dialog;
                dialog.Show();
                var host = (Grid)dialog.FindName("BrowserHost");
                WebView2CompositionControl browser = null;
                WaitUntil(() =>
                {
                    browser = host.Children.OfType<WebView2CompositionControl>().SingleOrDefault();
                    return browser?.CoreWebView2 != null && ((Button)dialog.FindName("ReloadButton")).IsEnabled;
                });
                RunTask(AssertDocumentAsync(browser));
                var reload = (Button)dialog.FindName("ReloadButton");
                var cancel = (Button)dialog.FindName("CancelButton");
                AssertOverlayHit(dialog, reload);
                AssertOverlayHit(dialog, cancel);
                var navigation = new TaskCompletionSource<bool>();
                browser.CoreWebView2.NavigationCompleted += (_, e) => navigation.TrySetResult(e.IsSuccess);
                reload.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                RunTask(navigation.Task);
                Assert(navigation.Task.Result, "native provider fixture failed to reload");
                RunTask(AssertReloadAsync(browser));
                SaveNativeSurface(dialog, output);
                browser.Focus();
                var key = new KeyEventArgs(Keyboard.PrimaryDevice, PresentationSource.FromVisual(dialog), Environment.TickCount, Key.Escape)
                    { RoutedEvent = Keyboard.PreviewKeyDownEvent };
                dialog.RaiseEvent(key);
                Assert(!dialog.IsVisible && host.Children.Count == 0 && key.Handled, "Escape did not close and dispose the native login surface");
                dialog = null;
                AccountTests.VerifyNativeMenu();
                VerifyMainCaption(directory);
                Console.WriteLine("native login overlay, full-page reload, Escape, account menu and caption hit-testing checks passed");
            }
            finally
            {
                dialog?.Close();
                app.Shutdown();
                SynchronizationContext.SetSynchronizationContext(previous);
                if (Directory.Exists(directory)) Directory.Delete(directory, true);
            }
        }

        private static async Task AssertDocumentAsync(WebView2CompositionControl browser)
        {
            var result = await browser.ExecuteScriptAsync("document.title");
            Assert(result == "\"Provider fixture\"", "native WebView did not load only the local provider fixture");
        }
        private static async Task AssertReloadAsync(WebView2CompositionControl browser)
        {
            var result = await browser.ExecuteScriptAsync("performance.getEntriesByType('navigation')[0].type");
            Assert(result == "\"reload\"", "reload action did not refresh the whole provider document");
        }
        private static void VerifyMainCaption(string directory)
        {
            using (var model = new MainWindowViewModel(new FakeLauncherBridge(),
                new SettingsService(directory),
                new UserSettings(),
                null,
                new FakeClientEnvironment(path => Task.FromResult(ValidDirectory(path))),
                new FakeDesktopInteraction()))
            {
                RunTask(model.ValidationTask);
                var window = new MainWindow { DataContext = model, Opacity = 0, ShowActivated = false, ShowInTaskbar = false };
                try
                {
                    window.Show(); Pump();
                    NativeWindowUiTests.VerifyCaptionHitTesting(window, new System.Windows.Interop.WindowInteropHelper(window).Handle);
                }
                finally { window.Close(); }
            }
        }
        private static void AssertOverlayHit(Window window, Button button)
        {
            var point = button.TranslatePoint(new Point(16, 16), window);
            var hit = window.InputHitTest(point) as DependencyObject;
            while (hit != null && !(hit is Button)) hit = VisualTreeHelper.GetParent(hit);
            Assert(ReferenceEquals(hit, button), "WebView obscured a native overlay action");
        }
        private static void SaveNativeSurface(Window window, string output)
        {
            var root = (FrameworkElement)window.Content;
            var dpi = VisualTreeHelper.GetDpi(window);
            window.Activate();
            var deadline = DateTime.UtcNow.AddMilliseconds(300);
            while (DateTime.UtcNow < deadline) { Pump(); Thread.Sleep(10); }
            Assert(window.IsActive, "native screenshot window is not in the foreground");
            var origin = root.PointToScreen(new Point());
            Directory.CreateDirectory(output);
            using (var bitmap = new System.Drawing.Bitmap((int)Math.Ceiling(root.ActualWidth * dpi.DpiScaleX),
                (int)Math.Ceiling(root.ActualHeight * dpi.DpiScaleY)))
            {
                using (var graphics = System.Drawing.Graphics.FromImage(bitmap))
                    graphics.CopyFromScreen((int)origin.X, (int)origin.Y, 0, 0, bitmap.Size);
                bitmap.Save(Path.Combine(output, "login-native-provider.png"), System.Drawing.Imaging.ImageFormat.Png);
            }
        }
        private static void WaitUntil(Func<bool> condition)
        {
            var deadline = DateTime.UtcNow.AddSeconds(20);
            while (DateTime.UtcNow < deadline)
            {
                Pump();
                if (condition()) return;
                Thread.Sleep(20);
            }
            throw new InvalidOperationException("native WebView initialization timed out; inspect the runtime prompt");
        }
    }
}
