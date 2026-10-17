using System;
using System.IO;
using System.Threading.Tasks;
using T7R.Core;
using T7R.Desktop.Services;
using T7R.Desktop.ViewModels;

namespace T7R.ManagedHarness
{
    internal static class SessionCloseTests
    {
        internal static void Run(string directory)
        {
            foreach (var state in new[] { SessionState.Running, SessionState.Idle, SessionState.Failed })
            {
                var bridge = new FakeLauncherBridge();
                var interaction = new FakeDesktopInteraction { ConfirmResult = false };
                using (var model = new MainWindowViewModel(bridge,
                new SettingsService(Path.Combine(directory, "session-close-" + state)),
                new UserSettings(),
                null,
                new FakeClientEnvironment(path => Task.FromResult(LauncherTests.ValidDirectory(path))),
                interaction))
                {
                    LauncherTests.RunTask(model.ValidationTask);
                    bridge.Snapshot = new SessionSnapshot { State = state, CleanupComplete = state != SessionState.Running };
                    model.Refresh();
                    bridge.RuntimeStatus = new RuntimeSessionStatus(true);
                    interaction.Confirming = () => LauncherTests.Assert(bridge.StopCount == 0,
                        "closing stopped the game before confirmation");
                    var close = model.RequestCloseAsync();
                    LauncherTests.RunTask(close);
                    if (state == SessionState.Running)
                    {
                        LauncherTests.Assert(!close.Result && bridge.StopCount == 0 && interaction.ConfirmCount == 1,
                            "cancelled close must preserve the running offline session");
                        interaction.ConfirmResult = true;
                        close = model.RequestCloseAsync();
                        LauncherTests.RunTask(close);
                        LauncherTests.Assert(close.Result && bridge.StopCount == 1 && interaction.ConfirmCount == 2,
                            "confirmed close must stop the offline session once");
                    }
                    else
                        LauncherTests.Assert(close.Result && bridge.StopCount == 1 && interaction.ConfirmCount == 0,
                            "already exited sessions should clean up without confirmation");
                }
            }
            CleanupFailureCanBeRetried(directory);
        }

        private static void CleanupFailureCanBeRetried(string directory)
        {
            var bridge = new FakeLauncherBridge();
            using (var model = new MainWindowViewModel(bridge,
                new SettingsService(Path.Combine(directory, "session-retry")), new UserSettings(), null,
                new FakeClientEnvironment(path => Task.FromResult(LauncherTests.ValidDirectory(path))),
                new FakeDesktopInteraction()))
            {
                LauncherTests.RunTask(model.ValidationTask);
                bridge.RuntimeStatus = new RuntimeSessionStatus(true, "connection fixture failure",
                    new InvalidOperationException("gateway fixture failure"));
                bridge.StopError = new InvalidOperationException("cleanup fixture failure");
                model.Refresh();
                LauncherTests.Assert(bridge.StopCount == 1 && bridge.RuntimeStatus.HasSession && model.CanStop,
                    "interface-reported gateway failure retains failed cleanup for retry");
                bridge.StopError = null;
                LauncherTests.RunTask(model.StopCommand.ExecuteAsync(null));
                LauncherTests.Assert(bridge.StopCount == 2 && !bridge.RuntimeStatus.HasSession && model.CanClose,
                    "retry releases the session through the bridge interface");
            }
        }
    }
}
