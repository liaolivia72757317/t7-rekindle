using System;
using System.IO;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using T7R.Desktop.Services;
using T7R.Desktop.Views;
using T7R.Desktop.ViewModels;
using static T7R.ManagedHarness.LauncherTests;

namespace T7R.ManagedHarness
{
    internal static partial class AccountTests
    {
        private static async Task WaitingForServerAsync()
        {
            var scan = new TaskCompletionSource<string>();
            var loginResponse = new TaskCompletionSource<AccountSession>();
            var onlineResponse = new TaskCompletionSource<AccountConnection>();
            var api = new FakeApi { PendingLogin = loginResponse.Task, PendingOnline = onlineResponse.Task };
            using (var model = new AccountViewModel(api, new MemoryStore(), () => Machine,
                token => scan.Task, action => action(), new ManualDelay().Delay))
            {
                Assert(!model.IsWaitingForServer, "idle account shows a server wait");
                var login = model.LoginAsync();
                Assert(model.IsLoginOpen && !model.IsWaitingForServer, "QR scan is presented as a server wait");
                scan.SetResult("TEST_COOKIE");
                await Until(() => api.LoginCalls == 1);
                Assert(model.IsWaitingForServer && !model.IsLoginOpen && model.IsSignedOut,
                    "login response wait is not visible after the QR window closes");
                Assert(model.AccountHint.Contains("正在验证登录") && !model.AccountHint.Contains("打开 QQ"),
                    "waiting account still invites a second login");
                await model.LoginAsync();
                Assert(api.LoginCalls == 1 && !model.LoginCommand.CanExecute(null), "waiting login allowed duplicate requests");
                loginResponse.SetResult(api.Session);
                await Until(() => api.OnlineCalls == 1);
                Assert(model.IsWaitingForServer && model.IsSignedIn && !model.IsOnline,
                    "accepted identity hid the pending online response");
                onlineResponse.SetResult(api.Connection);
                await login;
                Assert(!model.IsWaitingForServer && model.IsOnline && api.ListenCalls == 1,
                    "completed login or background push left the account loading");
            }
        }

        private static async Task RestoreWaitingForServerAsync()
        {
            var currentResponse = new TaskCompletionSource<AccountSession>();
            var onlineResponse = new TaskCompletionSource<AccountConnection>();
            var api = new FakeApi { PendingCurrent = currentResponse.Task, PendingOnline = onlineResponse.Task };
            using (var model = Model(api, new MemoryStore { Value = api.Session }, new ManualDelay()))
            {
                var restore = model.RestoreAsync();
                Assert(model.IsWaitingForServer && model.Status == "正在恢复登录", "restore response lacks loading state");
                currentResponse.SetResult(api.Session);
                await Until(() => api.OnlineCalls == 1);
                Assert(model.IsWaitingForServer, "restore stopped loading before its online response");
                onlineResponse.SetException(new IOException("TEST_OFFLINE"));
                await restore;
                Assert(!model.IsWaitingForServer && model.IsSignedIn && model.CanReconnect,
                    "failed online response did not restore the offline account actions");
                onlineResponse = new TaskCompletionSource<AccountConnection>();
                api.PendingOnline = onlineResponse.Task;
                var reconnect = model.ReconnectAsync();
                Assert(model.IsWaitingForServer && !model.CanReconnect, "reconnect response lacks loading state");
                onlineResponse.SetResult(api.Connection);
                await reconnect;
                Assert(!model.IsWaitingForServer && model.IsOnline, "reconnect completion left a loading state");
            }
            using (var model = Model(new FakeApi(), new MemoryStore(), new ManualDelay()))
            {
                await model.RestoreAsync();
                Assert(!model.IsWaitingForServer, "empty login store left a loading state");
            }
        }

        private static async Task CancelWaitingForServerAsync()
        {
            var oldResponse = new TaskCompletionSource<AccountSession>();
            var api = new FakeApi { PendingLogin = oldResponse.Task };
            using (var model = Model(api, new MemoryStore(), new ManualDelay()))
            {
                var oldLogin = model.LoginAsync();
                await model.SuspendAsync();
                Assert(!model.IsWaitingForServer, "suspended login kept its loading state");
                var response = new TaskCompletionSource<AccountSession>();
                api.PendingLogin = response.Task;
                var login = model.LoginAsync();
                oldResponse.SetResult(api.Session);
                await oldLogin;
                Assert(model.IsWaitingForServer && model.IsSignedOut, "stale response stopped the current loading state");
                response.SetException(new IOException("TEST_OFFLINE"));
                await login;
                Assert(!model.IsWaitingForServer && model.LoginCommand.CanExecute(null), "failed login kept loading or blocked retry");
                response = new TaskCompletionSource<AccountSession>();
                api.PendingLogin = response.Task;
                login = model.LoginAsync();
                model.Dispose();
                Assert(!model.IsWaitingForServer, "disposed account kept its loading state");
                response.SetResult(api.Session);
                await login;
                Assert(!model.IsWaitingForServer && model.IsSignedOut, "late response restarted a disposed account");
            }
            var onlineResponse = new TaskCompletionSource<AccountConnection>();
            api = new FakeApi { PendingOnline = onlineResponse.Task };
            using (var model = Model(api, new MemoryStore(), new ManualDelay()))
            {
                var login = model.LoginAsync();
                Assert(model.IsWaitingForServer && model.LogoutCommand.CanExecute(null), "pending online hid the logout action");
                await model.LogoutAsync();
                Assert(!model.IsWaitingForServer && model.IsSignedOut, "logout did not stop a pending online indicator");
                onlineResponse.SetResult(api.Connection);
                await login;
                Assert(!model.IsWaitingForServer && model.IsSignedOut, "late online response restored a logged-out account");
            }
        }

        private static void RenderAccountLoading(string output)
        {
            var loginResponse = new TaskCompletionSource<AccountSession>();
            var onlineResponse = new TaskCompletionSource<AccountConnection>();
            var api = new FakeApi { PendingLogin = loginResponse.Task, PendingOnline = onlineResponse.Task };
            var session = api.Session;
            api.Session = new AccountSession(session.SessionId, session.SessionToken, session.ExpiresAt, session.AccountId,
                session.LoginName, session.MachineId, session.QqNumber, "重燃玩家", null, session.GameName);
            using (var model = Model(api, new MemoryStore(), new ManualDelay()))
            {
                var panel = new AccountPanel { DataContext = model, HorizontalAlignment = HorizontalAlignment.Right,
                    VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(20, 0, 20, 0) };
                var root = new Grid { Background = (Brush)Application.Current.Resources["LoginTitlebarBrush"] };
                TextOptions.SetTextFormattingMode(root, TextFormattingMode.Display);
                root.Children.Add(panel);
                var login = model.LoginAsync();
                foreach (string phase in new[] { "login", "online", "complete" })
                {
                    if (phase == "online") { loginResponse.SetResult(api.Session); RunTask(Until(() => api.OnlineCalls == 1)); }
                    if (phase == "complete") { onlineResponse.SetResult(api.Connection); RunTask(login); }
                    foreach (double scale in new[] { 1.0, 1.25, 1.5, 1.75, 2.0 })
                    {
                        LauncherLayoutTests.Render(root, null, output, "account-loading-" + phase + "-" + (int)(scale * 100), 320, 62, scale);
                        var toggle = (ToggleButton)panel.FindName("AccountToggle");
                        var indicator = (ProgressBar)panel.FindName(phase == "login" ? "GuestLoadingIndicator" : "MemberLoadingIndicator");
                        Assert(toggle.ActualHeight == 36 && (phase == "login" ? toggle.ActualWidth == 96
                            : toggle.ActualWidth >= 144 && toggle.ActualWidth <= 224), "loading changed the capsule size");
                        Assert(indicator.Visibility == (phase == "complete" ? Visibility.Collapsed : Visibility.Visible),
                            "loading indicator does not follow the pending response");
                        if (phase == "complete") continue;
                        LauncherLayoutTests.AssertWithin(indicator, toggle, toggle.ActualWidth, toggle.ActualHeight);
                        Assert(AutomationProperties.GetName(indicator) == model.Status, "loading indicator lacks its accessible phase");
                        if (phase == "login")
                        {
                            var label = (TextBlock)panel.FindName("GuestLabel");
                            Assert(label.Text == "登录中", "waiting guest lacks a visible loading label");
                            LauncherLayoutTests.AssertWithin(label, toggle, toggle.ActualWidth, toggle.ActualHeight);
                        }
                        else Assert(((Control)panel.FindName("AccountChevron")).Visibility == Visibility.Collapsed,
                            "loading ring overlaps the account chevron");
                    }
                }
            }
        }

        private static void VerifyAccountLoadingAnimation()
        {
            var response = new TaskCompletionSource<AccountSession>();
            var api = new FakeApi { PendingLogin = response.Task };
            using (var model = Model(api, new MemoryStore(), new ManualDelay()))
            {
                var panel = new AccountPanel { DataContext = model, HorizontalAlignment = HorizontalAlignment.Center };
                var window = new Window { Content = panel, Width = 320, Height = 120, ShowInTaskbar = false, ShowActivated = false };
                try
                {
                    var login = model.LoginAsync();
                    window.Show(); Pump();
                    var indicator = (ProgressBar)panel.FindName("GuestLoadingIndicator");
                    indicator.Resources[SystemParameters.ClientAreaAnimationKey] = true;
                    Pump();
                    var rotor = (FrameworkElement)indicator.Template.FindName("Rotor", indicator);
                    var rotation = (RotateTransform)rotor.RenderTransform;
                    Assert(rotation.HasAnimatedProperties, "visible loading ring has no animation");
                    double angle = rotation.Angle;
                    var deadline = DateTime.UtcNow.AddSeconds(2);
                    while (Math.Abs(rotation.Angle - angle) <= 1 && DateTime.UtcNow < deadline)
                        RunTask(Task.Delay(20));
                    Assert(Math.Abs(rotation.Angle - angle) > 1, "loading ring does not rotate while waiting");
                    indicator.Resources[SystemParameters.ClientAreaAnimationKey] = false;
                    Pump();
                    Assert(!indicator.IsIndeterminate, "loading animation ignores the reduced-motion resource");
                    WaitForRotationToStop(rotation);
                    Assert(!rotation.HasAnimatedProperties && indicator.Visibility == Visibility.Visible,
                        "reduced motion did not stop rotation while retaining the loading indicator");
                    indicator.Resources[SystemParameters.ClientAreaAnimationKey] = true;
                    Pump();
                    window.Hide(); Pump();
                    WaitForRotationToStop(rotation);
                    Assert(!rotation.HasAnimatedProperties, "hidden loading ring kept its animation clock");
                    window.Show(); Pump();
                    Assert(rotation.HasAnimatedProperties, "visible loading ring did not resume");
                    response.SetException(new IOException("TEST_OFFLINE"));
                    RunTask(login); Pump();
                    WaitForRotationToStop(rotation);
                    Assert(indicator.Visibility == Visibility.Collapsed && !rotation.HasAnimatedProperties,
                        "completed response did not remove the loading animation");
                    Assert(((TextBlock)panel.FindName("GuestLabel")).Text == "未登录", "failed login kept its loading label");
                }
                finally { window.Close(); }
            }
        }

        private static void WaitForRotationToStop(RotateTransform rotation)
        {
            var deadline = DateTime.UtcNow.AddSeconds(2);
            while (rotation.HasAnimatedProperties && DateTime.UtcNow < deadline) RunTask(Task.Delay(20));
            Assert(!rotation.HasAnimatedProperties, "loading animation clock did not stop");
        }
    }
}
