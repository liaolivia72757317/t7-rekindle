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
using T7.Rekindle.Core;
using T7.Rekindle.Desktop;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;
using T7.Rekindle.Desktop.Views;
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
                    var version = (TextBlock)window.FindName("CapsuleVersion");
                    LauncherLayoutTests.Render(root, window, output, "version-reminder-unchecked", 1200, 900);
                    Assert(reminder.Visibility == Visibility.Collapsed, "unchecked capsule displayed an update reminder");
                    model.StartUpdateChecks(DateTime.UtcNow);
                    var notice = model.Notices.Visible.Single();
                    notice.IsPaused = true;
                    LauncherLayoutTests.Render(root, window, output, "version-reminder-toast", 1200, 900);
                    Assert(reminder.Visibility == Visibility.Visible && !reminder.HasAnimatedProperties,
                        "update reminder is hidden or continuously animated");
                    Assert(((Icon)window.FindName("VersionUpdateIcon")).Kind == "refresh"
                        && ((TextBlock)window.FindName("VersionUpdateLabel")).Text == "有更新", "reminder lacks its icon or text");
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
                    const string longVersion = "v123456789.123456789.123456789+long-build-version";
                    capsule.DataContext = new
                    {
                        model.ShowUpdatePageCommand,
                        About = new { Version = longVersion, HasUpdateReminder = true,
                            VersionCapsuleHint = "当前版本 " + longVersion + "；可用版本 v999999999.0.0；打开更新页面" }
                    };
                    LauncherLayoutTests.Render(root, window, output, "version-reminder-long", 928, 460);
                    AssertCapsuleLayout(window, capsule, reminder);
                    var natural = new TextBlock { Text = longVersion, FontFamily = version.FontFamily, FontSize = version.FontSize };
                    natural.Measure(new Size(double.PositiveInfinity, double.PositiveInfinity));
                    Assert(version.Text == longVersion && version.TextTrimming == TextTrimming.CharacterEllipsis
                        && version.ActualWidth < natural.DesiredSize.Width && capsule.ToolTip.ToString().Contains(longVersion),
                        "long version was not trimmed visually while retaining its full accessible value");
                }
                finally { window.DataContext = null; window.Close(); }
            }
        }

        private static void AssertCapsuleLayout(MainWindow window, Button capsule, Border reminder)
        {
            var sidebar = (FrameworkElement)window.FindName("Sidebar");
            var position = capsule.TranslatePoint(new Point(), sidebar);
            Assert(Math.Abs(position.X + capsule.ActualWidth / 2 - sidebar.ActualWidth / 2) < 1
                && position.X >= 16 && position.X + capsule.ActualWidth <= sidebar.ActualWidth - 16,
                "update capsule is not centered with sidebar insets");
            LauncherLayoutTests.AssertWithin(reminder, capsule, capsule.ActualWidth, capsule.ActualHeight);
            var label = (TextBlock)window.FindName("VersionUpdateLabel");
            Assert(label.ActualWidth >= 40 && capsule.Focusable && capsule.IsTabStop,
                "update label was truncated or the capsule lost keyboard access");
        }
    }
}
