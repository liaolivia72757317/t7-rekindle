using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class DirectorySelectionTests
    {
        internal static void Run(string directory)
        {
            TestAncestorSelection(directory);
            TestEditDuringSearch(directory);
            TestNewSelectionDuringSearch(directory);
            TestCancelledSelection(directory);
            TestSearchFailure(directory);
            TestDisposeDuringSearch(directory);
        }

        private static void TestAncestorSelection(string directory)
        {
            var parent = Path.Combine(directory, "browse-parent");
            var first = ClientDirectorySearchTests.CreateClient(Path.Combine(parent, "a-game"));
            var second = ClientDirectorySearchTests.CreateClient(Path.Combine(parent, "b-game"));
            var interaction = new FakeDesktopInteraction { DirectorySelection = parent };
            var settings = new SettingsService(Path.Combine(directory, "browse-settings"));
            using (var model = new MainWindowViewModel(new FakeLauncherBridge(), settings,
                new UserSettings { ClientDirectory = parent, PlayerName = "玩家" }, null,
                path => Task.FromResult(ClientDirectoryService.Inspect(path)), interaction))
            {
                RunTask(model.ValidationTask);
                Assert(!model.IsDirectoryValid, "manual path validation unexpectedly searched descendants");
                model.IsSettingsSelected = true;
                model.BrowseCommand.Execute(null);
                RunTask(model.ValidationTask);
                Assert(model.ClientDirectory == first && model.IsDirectoryValid && model.CanStart
                    && model.DirectoryMessage.Contains("自动定位") && model.IsSettingsSelected,
                    "selecting the same ancestor did not locate and display the first game root");
                Assert(settings.Load().ClientDirectory == first, "located root was not saved");

                interaction.DirectorySelection = second;
                model.BrowseCommand.Execute(null);
                RunTask(model.ValidationTask);
                Assert(model.ClientDirectory == second && settings.Load().ClientDirectory == second,
                    "manual selection of a more precise root was overridden");
                interaction.DirectorySelection = Path.Combine(second, "Bin");
                model.BrowseCommand.Execute(null);
                RunTask(model.ValidationTask);
                Assert(model.ClientDirectory == second, "browsed Bin directory was not normalized");

                interaction.DirectorySelection = null;
                var previous = model.ValidationTask;
                model.BrowseCommand.Execute(null);
                Assert(model.ValidationTask == previous && model.ClientDirectory == second,
                    "cancelling the directory dialog changed a valid selection");
                var empty = Path.Combine(directory, "browse-empty");
                Directory.CreateDirectory(empty);
                interaction.DirectorySelection = empty;
                model.BrowseCommand.Execute(null);
                RunTask(model.ValidationTask);
                Assert(model.ClientDirectory == empty && !model.CanStart && model.DirectoryMessage.Contains("未在所选目录")
                    && settings.Load().ClientDirectory == second, "empty search lost its error or replaced saved settings");
            }
        }

        private static void TestEditDuringSearch(string directory)
        {
            var pending = new TaskCompletionSource<ClientDirectoryResult>();
            var token = CancellationToken.None;
            var interaction = new FakeDesktopInteraction { DirectorySelection = @"C:\Games" };
            var settings = new SettingsService(Path.Combine(directory, "browse-edit"));
            using (var model = CreateModel(settings, interaction, (path, cancellation) =>
                { token = cancellation; return pending.Task; }))
            {
                RunTask(model.ValidationTask);
                model.BrowseCommand.Execute(null);
                var searching = model.ValidationTask;
                Assert(!searching.IsCompleted && model.DirectoryMessage.Contains("正在自动定位")
                    && !model.CanStart && !model.CheckCommand.CanExecute(null) && model.BrowseCommand.CanExecute(null)
                    && !model.AreSessionFieldsLocked && model.CanClose, "search blocked editing or allowed an early launch");
                model.ClientDirectory = @"C:\Games\Manual";
                RunTask(model.ValidationTask);
                Assert(token.IsCancellationRequested, "manual edit did not cancel the previous search");
                pending.SetResult(ValidDirectory(@"C:\Games\OldResult"));
                RunTask(searching);
                Assert(model.ClientDirectory == @"C:\Games\Manual" && model.CanStart
                    && settings.Load().ClientDirectory == model.ClientDirectory, "stale search replaced a newer manual input");
            }
        }

        private static void TestNewSelectionDuringSearch(string directory)
        {
            var pending = new TaskCompletionSource<ClientDirectoryResult>();
            var token = CancellationToken.None;
            var calls = 0;
            var interaction = new FakeDesktopInteraction { DirectorySelection = @"C:\Games" };
            using (var model = CreateModel(new SettingsService(Path.Combine(directory, "browse-reselect")), interaction,
                (path, cancellation) =>
                {
                    if (++calls != 1) return Task.FromResult(ValidDirectory(@"C:\Games\NewResult"));
                    token = cancellation;
                    return pending.Task;
                }))
            {
                RunTask(model.ValidationTask);
                model.BrowseCommand.Execute(null);
                var searching = model.ValidationTask;
                model.BrowseCommand.Execute(null);
                RunTask(model.ValidationTask);
                Assert(token.IsCancellationRequested && calls == 2, "reselecting the same parent did not restart search");
                pending.SetResult(ValidDirectory(@"C:\Games\OldResult"));
                RunTask(searching);
                Assert(model.ClientDirectory == @"C:\Games\NewResult", "older browse result replaced the latest selection");
            }
        }

        private static void TestCancelledSelection(string directory)
        {
            var pending = new TaskCompletionSource<ClientDirectoryResult>();
            var token = CancellationToken.None;
            var interaction = new FakeDesktopInteraction { DirectorySelection = @"C:\Games" };
            var settings = new SettingsService(Path.Combine(directory, "browse-cancel"));
            using (var model = CreateModel(settings, interaction, (path, cancellation) =>
                { token = cancellation; return pending.Task; }))
            {
                RunTask(model.ValidationTask);
                model.BrowseCommand.Execute(null);
                var searching = model.ValidationTask;
                interaction.DirectorySelection = null;
                model.BrowseCommand.Execute(null);
                Assert(token.IsCancellationRequested, "reopening the picker did not cancel the old search");
                pending.SetCanceled();
                RunTask(searching);
                Assert(!model.CanStart && !model.IsDirectoryValid && model.DirectoryMessage.Contains("已取消")
                    && settings.Load().ClientDirectory == @"C:\Games\Current", "cancelled search left a stale valid result");
            }
        }

        private static void TestSearchFailure(string directory)
        {
            var settings = new SettingsService(Path.Combine(directory, "browse-failure"));
            using (var model = CreateModel(settings, new FakeDesktopInteraction { DirectorySelection = @"C:\Games" },
                (path, cancellation) => Task.FromException<ClientDirectoryResult>(new IOException("fixture scan failure"))))
            {
                RunTask(model.ValidationTask);
                model.BrowseCommand.Execute(null);
                RunTask(model.ValidationTask);
                Assert(!model.CanStart && model.HasDirectoryError && model.DirectoryMessage.Contains("fixture scan failure")
                    && settings.Load().ClientDirectory == @"C:\Games\Current", "search failure was hidden or saved as valid");
            }
        }

        private static void TestDisposeDuringSearch(string directory)
        {
            var pending = new TaskCompletionSource<ClientDirectoryResult>();
            var token = CancellationToken.None;
            var settings = new SettingsService(Path.Combine(directory, "browse-dispose"));
            using (var model = CreateModel(settings, new FakeDesktopInteraction { DirectorySelection = @"C:\Games" },
                (path, cancellation) => { token = cancellation; return pending.Task; }))
            {
                RunTask(model.ValidationTask);
                model.BrowseCommand.Execute(null);
                var searching = model.ValidationTask;
                model.Dispose();
                Assert(token.IsCancellationRequested, "closing the view model did not cancel search");
                pending.SetResult(ValidDirectory(@"C:\Games\LateResult"));
                RunTask(searching);
                Assert(model.ClientDirectory == @"C:\Games" && settings.Load().ClientDirectory == @"C:\Games\Current",
                    "disposed search changed the selection or saved settings");
            }
        }

        private static MainWindowViewModel CreateModel(SettingsService settings, FakeDesktopInteraction interaction,
            Func<string, CancellationToken, Task<ClientDirectoryResult>> locateDirectory)
        {
            var initial = new UserSettings { ClientDirectory = @"C:\Games\Current", PlayerName = "Player" };
            settings.Save(initial);
            return new MainWindowViewModel(new FakeLauncherBridge(), settings, initial, null,
                path => Task.FromResult(ValidDirectory(path)), interaction, locateDirectory);
        }
    }
}
