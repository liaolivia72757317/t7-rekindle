using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Automation.Peers;
using System.Windows.Automation.Provider;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Input;
using System.Windows.Media;
using CommunityToolkit.Mvvm.Input;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class UpdateReminderTests
    {
        internal static void Run(string directory, string output)
        {
            TestCheckLifecycle();
            TestDownloadLifecycle();
            TestCapsule(directory, output);
        }

        private static LauncherUpdateInfo NewRelease() => new LauncherUpdateInfo
        {
            TargetVersion = "v1.2.3", IsNewVersion = true,
            DownloadAddress = "https://example.com/releases/v1.2.3"
        };

        private static void TestCheckLifecycle()
        {
            var pending = new TaskCompletionSource<LauncherUpdateInfo>();
            using (var model = new AboutViewModel(new FakeDesktopInteraction(), () => pending.Task))
            {
                Assert(!model.HasUpdateReminder && model.UpdateReminderVersion.Length == 0
                    && model.VersionCapsuleHint.Contains(model.Version), "unchecked launcher fabricated an update reminder");
                var installedPreview = LauncherInformation.CurrentBuild.Channel == UpdateChannel.Preview;
                Assert(model.IsPreviewBuild == installedPreview
                    && model.VersionCapsuleHint.Contains(installedPreview ? "预览版" : "正式版"), "capsule omitted installed build metadata");
                foreach (var channel in new[] { UpdateChannel.Preview, UpdateChannel.Stable })
                {
                    model.ResetUpdateChannel(channel);
                    Assert(model.IsPreviewBuild == installedPreview && model.DisplayVersion == LauncherInformation.DisplayVersion,
                        "selected update channel changed the installed build presentation");
                }
                var checking = model.CheckUpdateCommand.ExecuteAsync(null);
                pending.SetException(new IOException("update fixture"));
                RunTask(checking);
                Assert(!model.HasUpdateReminder, "first failed check fabricated an update reminder");
                pending = new TaskCompletionSource<LauncherUpdateInfo>();
                pending.SetResult(NewRelease());
                RunTask(model.CheckUpdateCommand.ExecuteAsync(null));
                Assert(model.HasUpdateReminder && model.UpdateReminderVersion == "v1.2.3"
                    && model.UpdateDownload.Info.Installer == null, "release-page-only update has no reminder");
                Assert(model.VersionCapsuleHint.Contains(model.Version) && model.VersionCapsuleHint.Contains("v1.2.3"),
                    "version capsule hint omitted the current or target version");
                var changed = new HashSet<string>();
                model.PropertyChanged += (_, change) => changed.Add(change.PropertyName);
                pending = new TaskCompletionSource<LauncherUpdateInfo>();
                checking = model.CheckUpdateCommand.ExecuteAsync(null);
                Assert(model.IsCheckingUpdate && !model.HasNewUpdate && model.HasUpdateReminder,
                    "checking cleared the reminder or changed update-page actions");
                pending.SetException(new IOException("update fixture"));
                RunTask(checking);
                Assert(model.UpdateFailed && model.HasUpdateReminder && model.UpdateReminderVersion == "v1.2.3"
                    && model.VersionCapsuleHint.Contains("上次"), "failed check lost the known update or stale-result hint");
                foreach (var result in new[]
                {
                    new LauncherUpdateInfo { TargetVersion = model.Version },
                    new LauncherUpdateInfo { HasPublishedRelease = false, TargetVersion = "未发布" },
                    new LauncherUpdateInfo { IsCurrentVersionAhead = true, TargetVersion = "v0.0.1" }
                })
                {
                    pending = new TaskCompletionSource<LauncherUpdateInfo>();
                    pending.SetResult(result);
                    RunTask(model.CheckUpdateCommand.ExecuteAsync(null));
                    Assert(!model.HasUpdateReminder && model.UpdateReminderVersion.Length == 0
                        && !model.VersionCapsuleHint.Contains("上次"), "authoritative no-update result retained a stale reminder");
                    pending = new TaskCompletionSource<LauncherUpdateInfo>();
                    pending.SetResult(NewRelease());
                    RunTask(model.CheckUpdateCommand.ExecuteAsync(null));
                    Assert(model.HasUpdateReminder, "rediscovered target did not restore its reminder");
                }
                Assert(new[] { nameof(model.HasUpdateReminder), nameof(model.UpdateReminderVersion), nameof(model.VersionCapsuleHint) }
                    .All(changed.Contains), "update reminder bindings were not notified");
                pending = new TaskCompletionSource<LauncherUpdateInfo>();
                pending.SetResult(new LauncherUpdateInfo { IsNewVersion = true, TargetVersion = string.Empty });
                RunTask(model.CheckUpdateCommand.ExecuteAsync(null));
                Assert(model.HasUpdateReminder && model.VersionCapsuleHint.Contains("\n有更新\n打开更新页面"),
                    "unknown target version hid the reminder or produced an empty version label");
            }
        }

        private static void TestDownloadLifecycle()
        {
            var pending = new TaskCompletionSource<string>();
            using (var model = new AboutViewModel(new FakeDesktopInteraction(),
                () => Task.FromResult(UpdateDownloadViewModelTests.Info()),
                info => new UpdateDownloadViewModel(info, (asset, progress, token, control) =>
                {
                    token.Register(() => pending.TrySetCanceled());
                    return pending.Task;
                }, path => Task.FromResult(false), address => { throw new InvalidOperationException("unexpected browser launch"); })))
            {
                RunTask(model.CheckUpdateCommand.ExecuteAsync(null));
                var download = model.UpdateDownload.PrimaryCommand.ExecuteAsync(null);
                Assert(model.UpdateDownload.IsDownloading && model.HasUpdateReminder, "download cleared the reminder");
                model.UpdateDownload.PauseDownloadCommand.Execute(null);
                Assert(model.UpdateDownload.IsPaused && model.HasUpdateReminder, "pausing cleared the reminder");
                RunTask(model.UpdateDownload.CancelAndWaitAsync());
                RunTask(download);
                Assert(model.HasUpdateReminder, "cancelling download cleared the reminder");
                pending = new TaskCompletionSource<string>();
                pending.SetResult("installer-fixture.exe");
                RunTask(model.UpdateDownload.PrimaryCommand.ExecuteAsync(null));
                Assert(model.UpdateDownload.HasDownloadedInstaller && model.HasUpdateReminder, "download completion cleared the reminder");
                RunTask(model.UpdateDownload.PrimaryCommand.ExecuteAsync(null));
                Assert(model.UpdateDownload.StatusText.Contains("取消") && model.HasUpdateReminder,
                    "cancelling installation cleared the reminder");
            }
        }

        private static void TestCapsule(string directory, string output)
        {
            var result = Task.FromResult(NewRelease());
            var checks = 0;
            var interaction = new FakeDesktopInteraction();
            using (var model = new MainWindowViewModel(new FakeLauncherBridge(),
                new SettingsService(Path.Combine(directory, "update-reminder")),
                new UserSettings { ClientDirectory = @"C:\Games\T7", PlayerName = "玩家" }, null,
                path => Task.FromResult(ValidDirectory(path)), interaction,
                checkUpdate: () => { checks++; return result; }))
            {
                RunTask(model.ValidationTask);
                var window = new MainWindow { DataContext = model };
                try
                {
                    var root = (FrameworkElement)window.Content;
                    var capsule = (Button)window.FindName("VersionCapsule");
                    var reminder = (Border)window.FindName("VersionUpdateReminder");
                    LauncherLayoutTests.Render(root, window, output, "version-reminder-unchecked", 1200, 900);
                    Assert(reminder.Visibility == Visibility.Collapsed, "unchecked capsule displayed an update reminder");
                    model.StartUpdateChecks(DateTime.UtcNow);
                    var notice = model.Notices.Visible.Single();
                    notice.IsPaused = true;
                    LauncherLayoutTests.Render(root, window, output, "version-reminder-toast", 1200, 900);
                    Assert(reminder.Visibility == Visibility.Visible && !reminder.HasAnimatedProperties,
                        "update reminder is hidden or continuously animated");
                    Assert(window.FindName("VersionUpdateIcon") == null
                        && ((TextBlock)window.FindName("VersionUpdateLabel")).Text == "有更新", "reminder changed its text-only design");
                    notice.CloseCommand.Execute(null);
                    var invoke = (IInvokeProvider)new ButtonAutomationPeer(capsule).GetPattern(PatternInterface.Invoke);
                    foreach (var page in new[] { 0, 1, 2, 3, 4 })
                    {
                        model.SelectedPage = page;
                        LauncherLayoutTests.Render(root, window, null, "version-reminder-page", 1200, 900);
                        Assert(reminder.Visibility == Visibility.Visible, "navigation cleared the update reminder");
                        invoke.Invoke();
                        Pump();
                        Assert(model.IsUpdateSelected && model.About.HasUpdateReminder && checks == 1
                            && interaction.Text == null && !model.About.IsUpdating, "capsule triggered an operation or cleared a read reminder");
                    }
                    model.IsHomeSelected = true;
                    foreach (var scale in new[] { 1.0, 1.25, 1.5, 1.75, 2.0 })
                    foreach (var size in new[] { new Size(1200, 900), new Size(928, 460) })
                    {
                        LauncherLayoutTests.Render(root, window, output,
                            "version-reminder-" + (int)(scale * 100) + "-" + size.Height, size.Width, size.Height, scale);
                        AssertCapsuleLayout(window, capsule, reminder);
                    }
                    result = Task.FromException<LauncherUpdateInfo>(new IOException("update fixture"));
                    RunTask(model.About.CheckUpdateCommand.ExecuteAsync(null));
                    LauncherLayoutTests.Render(root, window, output, "version-reminder-check-failed", 1200, 900);
                    Assert(reminder.Visibility == Visibility.Visible && capsule.ToolTip.ToString().Contains("上次")
                        && AutomationProperties.GetName(capsule) == capsule.ToolTip.ToString(), "stale tooltip or accessible name was lost");
                    TestCapsuleStates(window, model, output);
                    capsule.ClearValue(FrameworkElement.DataContextProperty);
                    TestNativeCapsule(window, model);
                }
                finally { window.DataContext = null; window.Close(); }
            }
        }

        private static void AssertCapsuleLayout(MainWindow window, Button capsule, Border reminder)
        {
            var sidebar = (FrameworkElement)window.FindName("Sidebar");
            var position = capsule.TranslatePoint(new Point(), sidebar);
            Assert(Math.Abs(position.X + capsule.ActualWidth / 2 - sidebar.ActualWidth / 2) < 1
                && position.X >= 7 && position.X + capsule.ActualWidth <= sidebar.ActualWidth - 7,
                "update capsule is not centered with sidebar insets");
            LauncherLayoutTests.AssertWithin(reminder, capsule, capsule.ActualWidth, capsule.ActualHeight);
            var label = (TextBlock)window.FindName("VersionUpdateLabel");
            var body = (Border)window.FindName("CapsuleBody");
            var bodyPosition = body.TranslatePoint(new Point(), sidebar);
            var contentScroll = (ScrollViewer)window.FindName("ContentScroll");
            Assert(Math.Abs(bodyPosition.X + body.ActualWidth / 2 - sidebar.ActualWidth / 2) < 1
                && bodyPosition.X >= 15 && bodyPosition.X + body.ActualWidth <= sidebar.ActualWidth - 15,
                "capsule body is not visually centered with sidebar insets");
            Assert(Math.Abs(sidebar.ActualHeight - bodyPosition.Y - body.ActualHeight - contentScroll.Margin.Bottom) <= 1,
                "capsule bottom spacing does not match the page content");
            var badgePosition = reminder.TranslatePoint(new Point(), body);
            Assert(Math.Abs(badgePosition.Y + 15) <= 1
                && Math.Abs(badgePosition.X + reminder.ActualWidth - body.ActualWidth - 6) <= 1
                && Math.Abs(body.ActualHeight - 36) < 1 && Math.Abs(reminder.ActualHeight - 20) < 1,
                "external badge geometry changed: " + badgePosition + ", body=" + body.RenderSize + ", badge=" + reminder.RenderSize);
            Assert(label.FontSize == 12 && label.ActualWidth >= 35
                && capsule.Focusable && capsule.IsTabStop && capsule.FocusVisualStyle != null,
                "update label was truncated or the capsule lost keyboard access");
            LauncherLayoutTests.AssertWithin(label, reminder, reminder.ActualWidth, reminder.ActualHeight);
            var version = (TextBlock)window.FindName("CapsuleVersion");
            var textEnd = version.ContentEnd.GetCharacterRect(LogicalDirection.Backward);
            Assert(version.FontSize == 14 && version.FontWeight == FontWeights.Normal
                && version.TextTrimming == TextTrimming.None && version.TextWrapping == TextWrapping.NoWrap
                && !textEnd.IsEmpty && textEnd.Right <= version.ActualWidth + 1,
                "full version text was clipped, resized or wrapped: " + version.Text
                    + ", width=" + version.ActualWidth + ", end=" + textEnd);
            LauncherLayoutTests.AssertWithin(version, body, body.ActualWidth, body.ActualHeight);
            foreach (var target in new FrameworkElement[] { body, version, (Border)window.FindName("CapsuleChannelPlate"), reminder })
            {
                var root = (FrameworkElement)window.Content;
                var point = target.TranslatePoint(new Point(target.ActualWidth - 4, target.ActualHeight / 2), root);
                var hit = VisualTreeHelper.HitTest(root, point)?.VisualHit;
                while (hit != null && !(hit is Button)) hit = VisualTreeHelper.GetParent(hit);
                Assert(ReferenceEquals(hit, capsule), "capsule region does not hit its single navigation button: " + target.Name);
            }
        }

        private static void TestCapsuleStates(MainWindow window, MainWindowViewModel model, string output)
        {
            var root = (FrameworkElement)window.Content;
            var capsule = (Button)window.FindName("VersionCapsule");
            var body = (Border)window.FindName("CapsuleBody");
            var version = (TextBlock)window.FindName("CapsuleVersion");
            var reminder = (Border)window.FindName("VersionUpdateReminder");
            var icon = (System.Windows.Shapes.Path)window.FindName("CapsuleChannelIcon");
            foreach (var preview in new[] { false, true })
            foreach (var longVersion in new[] { false, true })
            foreach (var scale in new[] { 1.0, 1.25, 1.5, 1.75, 2.0 })
            {
                var build = new LauncherBuild(preview ? UpdateChannel.Preview : UpdateChannel.Stable,
                    longVersion ? "v65534.65534.65534.65534" : "v0.1.0",
                    preview ? 1 : 0, preview ? (longVersion ? long.MaxValue : 128) : 0,
                    preview ? (longVersion ? int.MaxValue : 2) : 0, preview ? new string('a', 40) : "");
                var positions = new List<Rect>();
                foreach (var hasUpdate in new[] { false, true })
                {
                    capsule.DataContext = new
                    {
                        model.ShowUpdatePageCommand,
                        About = new { DisplayVersion = build.DisplayVersion, IsPreviewBuild = preview, HasUpdateReminder = hasUpdate,
                            VersionCapsuleHint = "当前安装：" + (preview ? "预览版 " : "正式版 ") + build.DisplayVersion
                                + (hasUpdate ? "\n有更新" : "") + "\n打开更新页面" }
                    };
                    var name = "capsule-" + (preview ? "preview" : "stable") + (longVersion ? "-long" : "")
                        + (hasUpdate ? "-update-" : "-normal-") + (int)(scale * 100);
                    LauncherLayoutTests.Render(root, window, output, name, longVersion ? 928 : 1200, longVersion ? 460 : 900, scale);
                    positions.Add(new Rect(body.TranslatePoint(new Point(), root), body.RenderSize));
                    Assert(version.Text == build.DisplayVersion && ReferenceEquals(icon.Data,
                        Application.Current.Resources[preview ? "CapsulePreviewIcon" : "CapsuleStableIcon"]),
                        "capsule did not preserve its installed version or icon");
                    Assert(ReferenceEquals(body.Background, Application.Current.Resources[preview ? "CapsulePreviewBackground" : "CapsuleStableBackground"])
                        && ReferenceEquals(capsule.Foreground, Application.Current.Resources[preview ? "CapsulePreviewText" : "CapsuleStableText"]),
                        "capsule ignored its build colors or high-contrast theme");
                    Assert(reminder.Visibility == (hasUpdate ? Visibility.Visible : Visibility.Collapsed), "incorrect update badge state");
                    if (hasUpdate) AssertCapsuleLayout(window, capsule, reminder);
                    var contentScroll = (ScrollViewer)window.FindName("ContentScroll");
                    var content = (Grid)window.FindName("ContentViewport");
                    Assert(content.ActualWidth >= 543, "long version squeezed the page below its supported content width");
                    if (longVersion && preview)
                    {
                        Assert(contentScroll.ScrollableWidth > 0, "compact window cannot scroll its wide page");
                        contentScroll.ScrollToRightEnd();
                        window.UpdateLayout();
                        Pump();
                        Assert(contentScroll.HorizontalOffset > 0, "wide page content is unreachable");
                        contentScroll.ScrollToLeftEnd();
                    }
                }
                Assert(positions[0] == positions[1], "badge visibility moved or resized the capsule body");
            }
        }

        private static void TestNativeCapsule(MainWindow window, MainWindowViewModel model)
        {
            window.Opacity = 0;
            window.ShowActivated = false;
            window.ShowInTaskbar = false;
            window.Show();
            Pump();
            var capsule = (Button)window.FindName("VersionCapsule");
            var reminder = (Border)window.FindName("VersionUpdateReminder");
            var point = reminder.TranslatePoint(new Point(reminder.ActualWidth - 4, reminder.ActualHeight / 2), window);
            var hit = window.InputHitTest(point) as DependencyObject;
            while (hit != null && !(hit is Button)) hit = VisualTreeHelper.GetParent(hit);
            Assert(ReferenceEquals(hit, capsule), "native external badge is not clickable through its button");
            var count = 0;
            capsule.Command = new RelayCommand(() => { count++; model.ShowUpdatePageCommand.Execute(null); });
            window.Activate();
            capsule.Focus();
            Pump();
            Assert(capsule.IsKeyboardFocused, "native version button cannot receive keyboard focus");
            var source = PresentationSource.FromVisual(window);
            foreach (var key in new[] { Key.Enter, Key.Space })
            {
                model.IsHomeSelected = true;
                var before = count;
                capsule.RaiseEvent(new KeyEventArgs(Keyboard.PrimaryDevice, source, Environment.TickCount, key)
                    { RoutedEvent = Keyboard.KeyDownEvent });
                capsule.RaiseEvent(new KeyEventArgs(Keyboard.PrimaryDevice, source, Environment.TickCount, key)
                    { RoutedEvent = Keyboard.KeyUpEvent });
                Pump();
                Assert(count == before + 1 && model.IsUpdateSelected && !model.About.IsUpdating,
                    "native capsule key did not navigate exactly once: " + key);
            }
            Assert(capsule.MoveFocus(new TraversalRequest(FocusNavigationDirection.Next)) && !capsule.IsKeyboardFocusWithin,
                "capsule decorations added keyboard stops");
        }
    }
}
