using System;
using System.IO;
using System.Reflection;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Automation.Peers;
using System.Windows.Automation.Provider;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;
using T7.Rekindle.Desktop.Views;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class UiInteractionTests
    {
        internal static void Run(string directory, string output)
        {
            TestFieldAndPreferenceFailures(directory);
            TestDraftRejectsLateValidation(directory);
            TestUpdateStates(directory, output);
            TestVersionCapsuleNavigation(directory);
            TestConstructionNavigation(directory, output);
            TestClientDownloadCard(directory, output);
            TestSettingsAccessibility(directory);
            TestGameSettingsPersistence(directory);
            TestDialogs(output);
            DiagnosticLogTests.Run(directory, output);
        }

        private static void TestConstructionNavigation(string directory, string output)
        {
            var bridge = new FakeLauncherBridge();
            var interaction = new FakeDesktopInteraction();
            using (var model = new MainWindowViewModel(bridge, new SettingsService(Path.Combine(directory, "construction")),
                new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "玩家" }, null,
                path => Task.FromResult(ValidDirectory(path)), interaction))
            {
                RunTask(model.ValidationTask);
                var window = new MainWindow { DataContext = model };
                try
                {
                    model.IsMultiplayerSelected = true;
                    LauncherLayoutTests.Render((FrameworkElement)window.Content, window, null, "construction", 1200, 900);
                    var page = (MultiplayerPage)window.FindName("RoomsPage");
                    var feedback = (Button)page.FindName("ConstructionFeedbackButton");
                    Assert(model.IsMultiplayerSelected && !model.IsAboutSelected && bridge.StartCount == 0
                        && model.Notices.Visible.Count == 0 && model.About.LastCheckText == "尚未检查" && interaction.Text == null,
                        "entering construction changed navigation or triggered an unrelated operation");
                    Action<Button> invoke = button =>
                    {
                        ((IInvokeProvider)new ButtonAutomationPeer(button).GetPattern(PatternInterface.Invoke)).Invoke();
                        Pump();
                    };
                    invoke(feedback);
                    Assert(interaction.Text == LauncherInformation.IssuesAddress && model.IsMultiplayerSelected,
                        "construction feedback did not use the existing project address");
                    RunTask(model.StartCommand.ExecuteAsync(null));
                    Assert(model.IsManagedGameRunning, "construction navigation test did not start its simulated session");
                    invoke((Button)page.FindName("ConstructionHomeButton"));
                    Assert(model.IsHomeSelected && model.IsManagedGameRunning, "returning home stopped the session or failed to navigate");
                    model.IsMultiplayerSelected = true;
                    Pump();
                    invoke((Button)page.FindName("ConstructionAboutButton"));
                    Assert(model.IsAboutSelected && model.IsManagedGameRunning, "about navigation stopped the session or looped to construction");
                    model.IsMultiplayerSelected = true;
                    page.DataContext = new
                    {
                        model.ShowHomeCommand, model.ShowAboutCommand,
                        About = new { HasIssuesAddress = false, model.About.ShowIssuesCommand, IssuesAddress = string.Empty }
                    };
                    LauncherLayoutTests.Render((FrameworkElement)window.Content, window, output, "battle-feedback-unavailable", 1200, 900);
                    Assert(!feedback.IsEnabled && ((TextBlock)page.FindName("ConstructionFeedbackCaption")).Text == "反馈入口暂不可用",
                        "unavailable feedback did not disable its action and explain its state");
                }
                finally
                {
                    window.DataContext = null;
                    window.Close();
                }
            }
        }

        private static void TestClientDownloadCard(string directory, string output)
        {
            const string address = "https://www.bilibili.com/opus/768784882628296761";
            var interaction = new FakeDesktopInteraction();
            var bridge = new FakeLauncherBridge();
            using (var model = new MainWindowViewModel(bridge, new SettingsService(Path.Combine(directory, "client-download")),
                new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "玩家" }, null,
                path => Task.FromResult(ValidDirectory(path)), interaction))
            {
                RunTask(model.ValidationTask);
                var window = new MainWindow { DataContext = model };
                try
                {
                    model.IsAboutSelected = true;
                    var page = (AboutPage)window.FindName("ProjectPage");
                    var button = (Button)page.FindName("ClientDownloadButton");
                    var disclaimer = (TextBlock)page.FindName("ClientDownloadDisclaimer");
                    Assert(button != null && disclaimer != null, "client download card or disclaimer is missing");
                    var root = (FrameworkElement)window.Content;
                    foreach (var size in new[] { new Size(1200, 900), new Size(960, 720) })
                    {
                        LauncherLayoutTests.Render(root, window, null, "client-download", size.Width, size.Height);
                        button.BringIntoView();
                        Pump();
                        LauncherLayoutTests.Render(root, window, output, "about-client-download-" + size.Width, size.Width, size.Height);
                        LauncherLayoutTests.AssertWithin(button, root, size.Width, size.Height);
                        LauncherLayoutTests.AssertWithin(disclaimer, button, button.ActualWidth, button.ActualHeight);
                    }
                    Assert(button.IsTabStop && button.Focusable && button.Command?.CanExecute(null) == true,
                        "client download card is not keyboard accessible");
                    Assert(disclaimer.FontSize == 14 && disclaimer.TextWrapping == TextWrapping.Wrap
                        && System.Windows.Automation.AutomationProperties.GetHelpText(button) == disclaimer.Text,
                        "client download disclaimer is not readable or exposed to accessibility tools");
                    Assert(interaction.Text == null, "opening the about page unexpectedly opened a browser");
                    var invoke = (IInvokeProvider)new ButtonAutomationPeer(button).GetPattern(PatternInterface.Invoke);
                    invoke.Invoke();
                    Pump();
                    Assert(interaction.Text == address && model.About.ClientDownloadAddress == address
                        && model.IsAboutSelected && bridge.StartCount == 0 && model.About.LastCheckText == "尚未检查",
                        "client download did not open the requested address or triggered an unrelated operation");
                    Action copyAddress = null;
                    model.About.NoticeRaised += (message, severity, actionText, action) =>
                    {
                        if (severity == NoticeSeverity.Warning && actionText == "复制地址") copyAddress = action;
                    };
                    interaction.AddressError = new IOException("fixture browser unavailable");
                    invoke.Invoke();
                    Pump();
                    Assert(copyAddress != null && model.About.Feedback.Contains("打开浏览器失败"),
                        "browser failure did not offer the existing copy-address fallback");
                    interaction.CopyText(string.Empty);
                    copyAddress();
                    Assert(interaction.Text == address, "browser failure copied the wrong client download address");
                }
                finally
                {
                    window.DataContext = null;
                    window.Close();
                }
            }
        }

        private static void TestVersionCapsuleNavigation(string directory)
        {
            using (var model = CreateModel(Path.Combine(directory, "version-navigation")))
            {
                RunTask(model.ValidationTask);
                var window = new MainWindow { DataContext = model };
                try
                {
                    LauncherLayoutTests.Render((FrameworkElement)window.Content, window, null, "version-navigation", 1200, 900);
                    var capsule = (Button)window.FindName("VersionCapsule");
                    Assert(capsule.Focusable && capsule.IsTabStop && capsule.Command?.CanExecute(null) == true,
                        "version capsule is not an accessible navigation button");
                    var invoke = (IInvokeProvider)new ButtonAutomationPeer(capsule).GetPattern(PatternInterface.Invoke);
                    foreach (var page in new[] { 0, 1, 2, 3, 4 })
                    {
                        model.SelectedPage = page;
                        Pump();
                        invoke.Invoke();
                        Pump();
                        Assert(model.IsUpdateSelected && ((LauncherUpdatePage)window.FindName("UpdatePage")).Visibility == Visibility.Visible,
                            "version capsule did not navigate to the update page");
                        foreach (RadioButton entry in ((StackPanel)window.FindName("SidebarNavigation")).Children)
                            Assert(entry.IsChecked == ((string)entry.Tag == "refresh"), "sidebar selection did not follow version navigation");
                    }
                    Assert(model.About.LastCheckText == "尚未检查" && !model.About.IsCheckingUpdate,
                        "version navigation unexpectedly checked for updates");
                }
                finally
                {
                    window.DataContext = null;
                    window.Close();
                }
            }
        }

        private static void TestSettingsAccessibility(string directory)
        {
            using (var model = CreateModel(Path.Combine(directory, "settings-accessibility")))
            {
                RunTask(model.ValidationTask);
                model.IsSettingsSelected = true;
                var window = new MainWindow { DataContext = model };
                LauncherLayoutTests.Render((FrameworkElement)window.Content, window, null, "settings-accessibility", 1200, 900);
                var settings = (GameSettingsPage)window.FindName("SettingsPage");
                var tabs = (TabControl)settings.FindName("SettingsTabs");
                var peer = UIElementAutomationPeer.CreatePeerForElement(tabs);
                Assert(HasAutomationId(peer, "NameInput"), "selected settings tab hides its fields from UI Automation");
                Assert(tabs.Items.Count == 3 && ((TabItem)tabs.Items[1]).Header.ToString() == "启动器设置"
                    && ((TabItem)tabs.Items[2]).Header.ToString() == "游戏设置", "settings tabs were not renamed and extended");
                model.SettingsTabIndex = 2;
                LauncherLayoutTests.Render((FrameworkElement)window.Content, window, null, "game-settings-accessibility", 1200, 900);
                Assert(HasAutomationId(peer, "SkipStartupAnimationSwitch"), "game switch is hidden from UI Automation");
                var skip = (CheckBox)settings.FindName("SkipStartupAnimationSwitch");
                var toggle = (IToggleProvider)new CheckBoxAutomationPeer(skip).GetPattern(PatternInterface.Toggle);
                toggle.Toggle();
                Pump();
                Assert(skip.IsChecked == true && model.SkipStartupAnimation
                    && new SettingsService(Path.Combine(directory, "settings-accessibility")).Load().SkipStartupAnimation,
                    "game switch did not save its toggled state");
                toggle.Toggle();
                Pump();
                Assert(skip.IsChecked == false && !model.SkipStartupAnimation, "game switch did not toggle off");
                window.DataContext = null;
                window.Close();
            }
        }

        private static void TestGameSettingsPersistence(string directory)
        {
            var service = new SettingsService(Path.Combine(directory, "game-settings"));
            Assert(!service.Load().SkipStartupAnimation, "startup animation skipping is not off by default");
            using (var model = new MainWindowViewModel(new FakeLauncherBridge(), service, service.Load(), null,
                _ => Task.FromResult(new ClientDirectoryResult("", "", "请选择目录")), new FakeDesktopInteraction()))
            {
                RunTask(model.ValidationTask);
                model.SkipStartupAnimation = true;
                Assert(model.SkipStartupAnimation && service.Load().SkipStartupAnimation,
                    "game setting required a configured client or was not saved immediately");
                model.PlayerName = "新的名字";
                model.MinimizeToTray = true;
                model.SaveSettings(1200, 900);
                Assert(service.Load().SkipStartupAnimation, "saving other settings discarded the game setting");
            }
            using (var model = new MainWindowViewModel(new FakeLauncherBridge(), service, service.Load(), null,
                _ => Task.FromResult(new ClientDirectoryResult("", "", "请选择目录")), new FakeDesktopInteraction()))
            {
                RunTask(model.ValidationTask);
                Assert(model.SkipStartupAnimation, "game setting was not restored when reopening the launcher");
                model.SkipStartupAnimation = false;
                Assert(!service.Load().SkipStartupAnimation && service.Load().MinimizeToTray && service.Load().PlayerName == "新的名字",
                    "disabling startup animation skipping was not saved or changed other settings");
            }
        }

        private static bool HasAutomationId(AutomationPeer peer, string id)
        {
            if (peer.GetAutomationId() == id) return true;
            var children = peer.GetChildren();
            if (children == null) return false;
            foreach (var child in children) if (HasAutomationId(child, id)) return true;
            return false;
        }

        private static void TestFieldAndPreferenceFailures(string directory)
        {
            var blocked = Path.Combine(directory, "write-blocked");
            File.WriteAllText(blocked, "fixture");
            using (var model = CreateModel(blocked))
            {
                RunTask(model.ValidationTask);
                model.IsSettingsSelected = true;
                model.PlayerName = "新的名字";
                Assert(model.NameFieldError.Length != 0 && model.SavedPlayerName == "玩家"
                    && model.Notices.Visible.Count == 0 && !model.CanStart, "failed field save discarded the draft or duplicated feedback");
                var registrationCalls = 0;
                var registration = false;
                model.RegisterStartup = value => { registration = value; registrationCalls++; };
                model.SettingsTabIndex = 1;
                model.StartWithWindows = true;
                Assert(!model.StartWithWindows && !registration && registrationCalls == 2
                    && model.PreferenceError.Length != 0, "failed preference save did not restore the system registration");
                model.MinimizeToTray = true;
                Assert(!model.MinimizeToTray, "tray preference changed despite failed persistence");
                model.SettingsTabIndex = 2;
                model.SkipStartupAnimation = true;
                Assert(!model.SkipStartupAnimation && model.GameSettingsError.Length != 0 && model.Notices.Visible.Count == 0,
                    "failed game setting save changed the switch or duplicated inline feedback");
                model.IsHomeSelected = true;
                model.SkipStartupAnimation = true;
                Assert(model.Notices.Visible.Count == 1, "hidden game settings did not surface a save failure");
                model.Notices.Visible[0].ActionCommand.Execute(null);
                Assert(model.IsSettingsSelected && model.SettingsTabIndex == 2, "game setting failure action opened the wrong tab");
                File.Delete(blocked);
                model.SkipStartupAnimation = true;
                Assert(model.GameSettingsError.Length == 0 && model.Notices.Visible.Count == 0,
                    "successful game setting retry did not clear its failure feedback");
                model.SettingsTabIndex = 0;
                model.PlayerName = "新的名字";
                Assert(model.NameFieldError.Length == 0 && model.SavedPlayerName == "新的名字" && model.CanStart,
                    "recommitting the same draft did not retry persistence");
                model.MinimizeToTray = true;
                model.StartWithWindows = true;
                var saved = new SettingsService(blocked).Load();
                Assert(saved.MinimizeToTray && saved.StartWithWindows && saved.SkipStartupAnimation && registration,
                    "preferences were not persisted or discarded the game setting");
            }
            using (var model = new MainWindowViewModel(new FakeLauncherBridge(), new SettingsService(Path.Combine(directory, "name-only")),
                new UserSettings(), null, _ => Task.FromResult(new ClientDirectoryResult("", "", "请选择目录")), new FakeDesktopInteraction()))
            {
                RunTask(model.ValidationTask);
                model.PlayerName = "已保存名称";
                Assert(model.SavedPlayerName == "已保存名称" && model.SettingsFeedback.Length == 0 && !model.CanStart,
                    "saving a valid name incorrectly required a configured directory");
            }
        }

        private static void TestDraftRejectsLateValidation(string directory)
        {
            var pending = new TaskCompletionSource<ClientDirectoryResult>();
            using (var model = new MainWindowViewModel(new FakeLauncherBridge(), new SettingsService(Path.Combine(directory, "draft")),
                new UserSettings { ClientDirectory = @"C:\Old", PlayerName = "玩家" }, null,
                path => path == @"C:\Old" ? pending.Task : Task.FromResult(ValidDirectory(path)), new FakeDesktopInteraction()))
            {
                var old = model.ValidationTask;
                model.BeginDirectoryDraft();
                pending.SetResult(ValidDirectory(@"C:\Old\Normalized"));
                RunTask(old);
                Assert(model.ClientDirectory == @"C:\Old" && !model.CanStart, "late validation overwrote an uncommitted UI draft");
                model.ClientDirectory = @"C:\New";
                RunTask(model.ValidationTask);
                Assert(model.CanStart && model.SavedClientDirectory == @"C:\New", "new directory did not win");
            }
        }

        private static void TestUpdateStates(string directory, string output)
        {
            using (var model = CreateModel(Path.Combine(directory, "update-states")))
            {
                RunTask(model.ValidationTask);
                var window = new MainWindow { DataContext = model };
                model.IsUpdateSelected = true;
                var page = (LauncherUpdatePage)window.FindName("UpdatePage");
                foreach (var state in new[] { "checking", "latest", "available", "unpublished", "failed" })
                {
                    var pending = new TaskCompletionSource<LauncherUpdateInfo>();
                    var about = new AboutViewModel(new FakeDesktopInteraction(), () => pending.Task);
                    page.DataContext = new { About = about };
                    var check = about.CheckUpdateCommand.ExecuteAsync(null);
                    if (state == "failed") pending.SetException(new IOException("fixture offline"));
                    else if (state != "checking") pending.SetResult(new LauncherUpdateInfo
                    {
                        IsNewVersion = state == "available", HasPublishedRelease = state != "unpublished",
                        CurrentVersion = "v0.1.0", TargetVersion = "v0.2.0"
                    });
                    if (state != "checking") RunTask(check);
                    LauncherLayoutTests.Render((FrameworkElement)window.Content, window, output, "update-state-" + state, 1200, 900);
                    Assert(model.CanStart && about.Version == LauncherInformation.Version, "update state affected local launch or installed version");
                    if (state == "checking") { pending.SetResult(new LauncherUpdateInfo()); RunTask(check); }
                }
                window.DataContext = null;
                window.Close();
            }
        }

        private static void TestDialogs(string output)
        {
            var confirm = new ConfirmationDialog("结束游戏？", "将关闭本次启动的游戏进程。\n尚未保存的游戏进度可能丢失。", "结束游戏");
            LauncherLayoutTests.Render((FrameworkElement)confirm.Content, null, output, "confirm-end-game", 504, 261);
            var cancel = (Button)confirm.FindName("CancelButton");
            Assert(cancel.IsCancel && cancel.IsDefault && !((Button)confirm.FindName("ConfirmButton")).IsDefault,
                "destructive dialog does not default to cancellation");
            var raw = "检查失败\nC:\\Users\\fixture-user\\private\\client\ntoken=fixture-token\nAuthorization: Bearer fixture-secret";
            var redacted = DiagnosticSanitizer.Redact(raw);
            Assert(!redacted.Contains("fixture-user") && !redacted.Contains("fixture-token") && !redacted.Contains("fixture-secret"),
                "shareable diagnostics disclose personal paths or credentials");
            var dialog = new TextDialog("启动诊断", raw, false, true);
            string copied = null;
            dialog.CopyDiagnosticText = text => copied = text;
            var copy = typeof(TextDialog).GetMethod("OnCopyDiagnostics", BindingFlags.NonPublic | BindingFlags.Instance);
            copy.Invoke(dialog, new object[] { dialog, new RoutedEventArgs() });
            Assert(copied == redacted && ((TextBlock)dialog.FindName("LocalFeedback")).Text == "诊断信息已复制", "copy did not confirm the actual write");
            dialog.CopyDiagnosticText = _ => throw new IOException("fixture clipboard busy");
            copy.Invoke(dialog, new object[] { dialog, new RoutedEventArgs() });
            Assert(((TextBlock)dialog.FindName("LocalFeedback")).Text == "复制失败，请重试", "clipboard failure retained a success message");
            LauncherLayoutTests.Render((FrameworkElement)dialog.Content, null, output, "diagnostics-copy-failed", 644, 441);
            var notices = new NoticeCenter();
            notices.Publish("error", "设置未保存，请重试", NoticeSeverity.Error, "重试", () => { });
            var recent = new RecentNoticesDialog(notices);
            LauncherLayoutTests.Render((FrameworkElement)recent.Content, null, output, "recent-notices", 624, 461);
        }

        private static MainWindowViewModel CreateModel(string directory) => new MainWindowViewModel(new FakeLauncherBridge(),
            new SettingsService(directory), new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "玩家" }, null,
            path => Task.FromResult(ValidDirectory(path)), new FakeDesktopInteraction());
    }
}
