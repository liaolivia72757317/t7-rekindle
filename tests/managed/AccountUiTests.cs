using System;
using System.Reflection;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Documents;
using System.Windows.Media;
using System.Windows.Input;
using System.Windows.Automation.Peers;
using System.Windows.Automation.Provider;
using T7R.Desktop.Services;
using T7R.Desktop.ViewModels;
using T7R.Desktop.Views;
using static T7R.ManagedHarness.LauncherTests;

namespace T7R.ManagedHarness
{
    internal static partial class AccountTests
    {
        internal static void VerifyNativeMenu()
        {
            var api = new FakeApi();
            var session = api.Session;
            api.Session = new AccountSession(session.SessionId, session.SessionToken, session.ExpiresAt, session.AccountId,
                session.LoginName, session.MachineId, session.QqNumber, "重燃玩家", null, session.GameName);
            using (var model = Model(api, new MemoryStore(), new ManualDelay()))
            {
                RunTask(model.LoginAsync());
                var panel = new AccountPanel { DataContext = model, HorizontalAlignment = HorizontalAlignment.Right,
                    VerticalAlignment = VerticalAlignment.Top, Margin = new Thickness(20) };
                var window = new Window { Content = panel, Width = 400, Height = 240, ShowInTaskbar = false };
                try
                {
                    window.Show(); Pump();
                    var popup = (Popup)panel.FindName("AccountPopup");
                    var toggle = (ToggleButton)panel.FindName("AccountToggle");
                    var logout = (Button)panel.FindName("LogoutButton");
                    var toggleProvider = (IToggleProvider)new ToggleButtonAutomationPeer(toggle).GetPattern(PatternInterface.Toggle);
                    toggleProvider.Toggle(); Pump();
                    Assert(popup.IsOpen && toggle.IsChecked == true && logout.IsKeyboardFocused,
                        "account activation did not open and focus its menu");
                    var escape = new KeyEventArgs(Keyboard.PrimaryDevice, PresentationSource.FromVisual(popup.Child),
                        Environment.TickCount, Key.Escape) { RoutedEvent = Keyboard.PreviewKeyDownEvent };
                    popup.Child.RaiseEvent(escape); Pump();
                    Assert(!popup.IsOpen && toggle.IsChecked == false && toggle.IsKeyboardFocused,
                        "account Escape did not collapse and return focus");
                    toggleProvider.Toggle(); Pump();
                    Mouse.Capture(null); Pump();
                    Assert(!popup.IsOpen, "account menu stayed open after losing outside-click capture");
                    api.Kick(new AccountKick(api.Session.SessionId, api.Connection.ConnectionId, "ONLINE_TAKEN_OVER"));
                    toggleProvider.Toggle(); Pump();
                    Assert(popup.IsOpen && logout.IsEnabled, "offline account menu is not actionable");
                    logout.RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); Pump();
                    Assert(model.IsSignedOut && !popup.IsOpen && toggle.IsKeyboardFocused,
                        "logout did not restore guest presentation and focus");
                }
                finally { window.Close(); }
            }
        }

        private static async Task PresentationStateAsync()
        {
            var api = new FakeApi();
            var scan = new TaskCompletionSource<string>();
            int scans = 0;
            using (var model = new AccountViewModel(api, new MemoryStore(), () => Machine,
                token => { scans++; return scan.Task; }, action => action(), new ManualDelay().Delay))
            {
                Assert(model.DisplayName == "未登录" && model.AccountState == "guest" && !model.IsOnline,
                    "guest identity or presence differs from the approved capsule");
                var login = model.LoginAsync();
                Assert(model.IsLoginOpen && !model.LoginCommand.CanExecute(null), "login surface is not active or allows reentry");
                await model.LoginAsync();
                Assert(scans == 1, "repeated entry opened a second login surface");
                scan.SetResult(null);
                await login;
                Assert(!model.IsLoginOpen && model.IsSignedOut && !model.IsWaitingForServer,
                    "cancelled login changed identity or left the surface active");
            }
            var time = new ManualDelay();
            using (var model = Model(api, new MemoryStore(), time))
            {
                await model.LoginAsync();
                Assert(model.AccountState == "online" && model.AccountHint.Contains(api.Session.Nickname),
                    "confirmed identity did not drive online presentation");
                api.HeartbeatError = new System.IO.IOException("TEST_OFFLINE");
                await time.TickAsync(15);
                await Until(() => !model.IsOnline);
                Assert(model.AccountState == "offline" && model.IsSignedIn && model.QqNumber == api.Session.QqNumber,
                    "network failure discarded the account identity");
                api.HeartbeatError = null;
                await time.TickAsync(15);
                await Until(() => model.IsOnline);
                api.Kick(new AccountKick(api.Session.SessionId, api.Connection.ConnectionId, "ONLINE_TAKEN_OVER"));
                Assert(model.AccountState == "offline" && model.HasSession && model.LogoutCommand.CanExecute(null),
                    "offline account lost its logout action");
                await model.LogoutAsync();
                Assert(model.AccountState == "guest" && model.AvatarUrl == null && model.QqNumber.Length == 0,
                    "logout did not clear the visible identity");
            }
        }

        private static void RenderAccountUi(string output)
        {
            foreach (var longName in new[] { false, true })
            {
                var api = new FakeApi();
                var session = api.Session;
                string number = longName ? "12345678901234567890" : session.QqNumber;
                api.Session = new AccountSession(session.SessionId, session.SessionToken, session.ExpiresAt, session.AccountId,
                    number, session.MachineId, number,
                    longName ? "一位名字很长的铁骑玩家用于检查昵称省略" : "重燃玩家", null, session.GameName);
                using (var model = Model(api, new MemoryStore(), new ManualDelay()))
                {
                    var panel = new AccountPanel { DataContext = model, HorizontalAlignment = HorizontalAlignment.Right,
                        VerticalAlignment = VerticalAlignment.Top, Margin = new Thickness(0, 13, 30, 0) };
                    var popup = (Popup)panel.FindName("AccountPopup");
                    var menuSurface = (Border)popup.Child;
                    popup.Child = null;
                    menuSurface.DataContext = model;
                    menuSurface.HorizontalAlignment = HorizontalAlignment.Right;
                    menuSurface.VerticalAlignment = VerticalAlignment.Top;
                    menuSurface.Margin = new Thickness(0, 49, 0, 0);
                    var root = new Grid { Background = (Brush)Application.Current.Resources["LoginPaperBrush"] };
                    TextElement.SetFontFamily(root, (FontFamily)Application.Current.Resources["UiFontFamily"]);
                    TextElement.SetForeground(root, (Brush)Application.Current.Resources["LoginTextBrush"]);
                    TextOptions.SetTextFormattingMode(root, TextFormattingMode.Display);
                    root.Children.Add(new Border { Height = 62, VerticalAlignment = VerticalAlignment.Top,
                        Background = (Brush)Application.Current.Resources["LoginTitlebarBrush"] });
                    root.Children.Add(panel); root.Children.Add(menuSurface);
                    var toggle = (ToggleButton)panel.FindName("AccountToggle");
                    foreach (var state in new[] { "guest", "online", "offline" })
                    {
                        if (state == "online") RunTask(model.LoginAsync());
                        if (state == "offline")
                            api.Kick(new AccountKick(api.Session.SessionId, api.Connection.ConnectionId, "ONLINE_TAKEN_OVER"));
                        menuSurface.Visibility = model.IsSignedIn ? Visibility.Visible : Visibility.Collapsed;
                        foreach (var scale in new[] { 1.0, 1.25, 1.5, 1.75, 2.0 })
                        {
                            foreach (var interaction in new[] { "default", "hover", "pressed", "focus", "expanded" })
                            {
                                SetReadOnly(toggle, typeof(UIElement), "IsMouseOverPropertyKey", interaction == "hover");
                                SetReadOnly(toggle, typeof(ButtonBase), "IsPressedPropertyKey", interaction == "pressed");
                                SetReadOnly(toggle, typeof(UIElement), "IsKeyboardFocusedPropertyKey", interaction == "focus");
                                var synchronizing = typeof(AccountPanel).GetField("_synchronizing", BindingFlags.NonPublic | BindingFlags.Instance);
                                synchronizing.SetValue(panel, true);
                                try { toggle.IsChecked = interaction == "expanded"; }
                                finally { synchronizing.SetValue(panel, false); }
                                string name = "account-" + state + "-" + interaction + (longName ? "-long" : "") + "-" + (int)(scale * 100);
                                LauncherLayoutTests.Render(root, null, output, name, 400, 240, scale);
                                Assert(Math.Abs(toggle.ActualHeight - 36) < 1, "capsule height changed with state or DPI");
                                Assert(model.IsSignedIn ? toggle.ActualWidth >= 144 && toggle.ActualWidth <= 224 : toggle.ActualWidth == 96,
                                    "capsule width does not preserve guest/member bounds");
                                Assert(((FrameworkElement)panel.FindName("MemberContent")).Visibility ==
                                    (model.IsSignedIn ? Visibility.Visible : Visibility.Collapsed), "guest showed account decoration");
                                if (model.IsSignedIn) AssertMenu(panel, root, toggle);
                            }
                        }
                        Assert(AutomationProperties.GetName(toggle).Contains(model.IsSignedIn ? model.DisplayName : "未登录"),
                            "account accessibility name lost the full nickname");
                    }
                    SetReadOnly(toggle, typeof(UIElement), "IsMouseOverPropertyKey", false);
                    SetReadOnly(toggle, typeof(ButtonBase), "IsPressedPropertyKey", false);
                    SetReadOnly(toggle, typeof(UIElement), "IsKeyboardFocusedPropertyKey", false);
                    var avatar = (Image)panel.FindName("CapsuleAvatar");
                    typeof(AccountPanel).GetMethod("OnAvatarFailed", BindingFlags.NonPublic | BindingFlags.Instance)
                        .Invoke(panel, new object[] { avatar, null });
                    Assert(avatar.Visibility == Visibility.Hidden && model.IsSignedIn, "avatar failure discarded identity");
                    typeof(AccountPanel).GetMethod("OnAvatarChanged", BindingFlags.NonPublic | BindingFlags.Instance)
                        .Invoke(panel, new object[] { avatar, null });
                    Assert(avatar.Visibility == Visibility.Visible, "new avatar did not recover from the fallback");
                    RunTask(model.LogoutAsync());
                }
            }
        }

        private static void AssertMenu(AccountPanel panel, Grid root, ToggleButton toggle)
        {
            var menu = (Border)panel.FindName("AccountMenu");
            var nickname = (TextBlock)panel.FindName("AccountNickname");
            var number = (TextBlock)panel.FindName("AccountNumber");
            var logout = (Button)panel.FindName("LogoutButton");
            Assert(menu.ActualHeight >= 96 && menu.ActualHeight <= 128 && menu.ActualWidth == 280, "account menu lost its compact identity/title rows");
            Assert(number.Text == "（" + ((AccountViewModel)panel.DataContext).QqNumber + "）" && number.TextTrimming == TextTrimming.None,
                "QQ number was shortened");
            Assert(nickname.TextTrimming == TextTrimming.CharacterEllipsis && logout.IsEnabled, "offline menu lost usable content");
            LauncherLayoutTests.AssertWithin(logout, root, 400, 240);
            LauncherLayoutTests.AssertWithin((Button)panel.FindName("TitlesButton"), root, 400, 240);
            LauncherLayoutTests.AssertWithin(number, menu, 280, 58);
            Assert(number.TranslatePoint(new Point(number.ActualWidth, 0), menu).X + 12
                <= logout.TranslatePoint(new Point(), menu).X + 1, "full QQ number overlaps the logout action");
            var menuPosition = menu.TranslatePoint(new Point(), root);
            var capsulePosition = toggle.TranslatePoint(new Point(), root);
            Assert(Math.Abs(menuPosition.X + menu.ActualWidth - capsulePosition.X - toggle.ActualWidth) < 1,
                "menu is not right-aligned with its capsule");
            Assert(Math.Abs(menuPosition.Y - capsulePosition.Y - toggle.ActualHeight - 8) < 1,
                "menu is not 8 DIP below its capsule");
            var placements = AccountPanel.PlaceMenu(new Size(340, 106), new Size(toggle.ActualWidth, 36), new Point());
            Assert(placements[0].Point.X + 30 + 280 == toggle.ActualWidth, "native popup anchor ignores shadow padding");
        }

        private static void SetReadOnly(DependencyObject target, Type owner, string name, bool value)
        {
            var key = (DependencyPropertyKey)owner.GetField(name, BindingFlags.NonPublic | BindingFlags.Public | BindingFlags.Static).GetValue(null);
            target.SetValue(key, value);
        }
    }
}
