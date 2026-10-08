using System;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;
using T7.Rekindle.Desktop.Views;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class GraphicsSettingsTests
    {
        internal static void Run(string directory)
        {
            var bridge = new FakeLauncherBridge();
            using (var model = CreateModel(bridge, directory))
            {
                RunTask(model.ValidationTask);
                model.Refresh();
                Assert(!model.CanEditGraphics && !model.ApplyGraphicsCommand.CanExecute(null), "offline graphics accepted writes");
                var current = new GraphicsSettingsValues(1920, 1080, false, 4, false, false, 159, true, false, 1);
                bridge.Snapshot = new SessionSnapshot { State = SessionState.Running };
                bridge.Graphics = new GraphicsSettingsSnapshot(1, GraphicsSyncState.Ready, current);
                model.Refresh();
                Assert(model.CanEditGraphics && model.GraphicsResolution == "1920x1080" && model.GraphicsViewDistance == 159
                    && model.GraphicsSwoosh == 1 && !model.HasGraphicsChanges, "initial game snapshot was not mapped");
                model.GraphicsFrameLimit = true;
                Assert(model.CanApplyGraphics && bridge.GraphicsApplyCount == 0, "draft was applied before save");
                model.Refresh();
                Assert(model.GraphicsFrameLimit, "same-revision polling overwrote an unsaved draft");
                model.ApplyGraphicsCommand.Execute(null);
                Assert(bridge.GraphicsApplyCount == 1 && bridge.AppliedGraphicsRevision == 1 && bridge.AppliedGraphics.FrameLimit
                    && !model.CanEditGraphics && model.GraphicsStatus.Contains("等待"), "save did not wait for game acknowledgement");
                model.ApplyGraphicsCommand.Execute(null);
                Assert(bridge.GraphicsApplyCount == 1, "duplicate in-flight save was submitted");
                bridge.Graphics = new GraphicsSettingsSnapshot(2, GraphicsSyncState.Ready, bridge.AppliedGraphics);
                model.Refresh();
                Assert(!model.HasGraphicsChanges && model.GraphicsStatus.Contains("回读确认"), "apply acknowledgement missing");
                model.GraphicsFog = true;
                bridge.Graphics = new GraphicsSettingsSnapshot(3, GraphicsSyncState.Ready, current);
                model.Refresh();
                Assert(!model.GraphicsFog && !model.HasGraphicsChanges && bridge.GraphicsApplyCount == 1,
                    "in-game changes were not synchronized or triggered a feedback write");
                model.GraphicsFrameLimit = true; model.ApplyGraphicsCommand.Execute(null);
                bridge.Graphics = new GraphicsSettingsSnapshot(4, GraphicsSyncState.Conflict, current);
                model.Refresh();
                Assert(!model.GraphicsFrameLimit && model.GraphicsStatus.Contains("未覆盖"), "conflict clobbered game state");
                model.GraphicsFog = true; bridge.GraphicsError = new InvalidOperationException("fixture apply failure");
                model.ApplyGraphicsCommand.Execute(null);
                Assert(model.HasGraphicsChanges && model.GraphicsError.Contains("fixture"), "submit failure was hidden");
                bridge.GraphicsError = null; model.RevertGraphicsCommand.Execute(null);
                Assert(!model.HasGraphicsChanges && model.GraphicsError.Length == 0,
                    "revert did not restore game values or retained a stale error");
                model.ResetGraphicsCommand.Execute(null);
                Assert(model.HasGraphicsChanges && model.GraphicsResolution == "1440x900" && bridge.GraphicsApplyCount == 2,
                    "initialize wrote settings without save");
                bridge.Snapshot = new SessionSnapshot { State = SessionState.Idle };
                model.Refresh();
                Assert(!model.CanEditGraphics && !model.CanApplyGraphics, "game exit retained memory write controls");
            }
            Assert(!new GraphicsSettingsValues(1, 1).IsValid && !new GraphicsSettingsValues(quality: 5).IsValid
                && !new GraphicsSettingsValues(swoosh: 3).IsValid, "graphics value bounds");
        }
        internal static void Render(string directory, string output)
        {
            var bridge = new FakeLauncherBridge
            {
                Snapshot = new SessionSnapshot { State = SessionState.Running },
                Graphics = new GraphicsSettingsSnapshot(1, GraphicsSyncState.Ready,
                    new GraphicsSettingsValues(1920, 1080, false, 4, false, false, 159, true, false, 1))
            };
            using (var model = CreateModel(bridge, directory))
            {
                RunTask(model.ValidationTask); model.Refresh();
                var panel = new GraphicsSettingsPanel { DataContext = model };
                var root = new Grid { Children = { panel }, Background = (System.Windows.Media.Brush)Application.Current.Resources["PanelBackgroundBrush"] };
                System.Windows.Media.TextOptions.SetTextFormattingMode(root, System.Windows.Media.TextFormattingMode.Display);
                TextElementFont(root);
                foreach (var scale in new[] { 1.0, 1.5, 2.0 })
                {
                    LauncherLayoutTests.Render(root, null, output, "graphics-" + (int)(scale * 100), 640, 660, scale);
                    LauncherLayoutTests.AssertSettingsHeader(panel, "ApplyGraphicsButton");
                    LauncherLayoutTests.Render(root, null, output, "graphics-compact-" + (int)(scale * 100), 480, 660, scale);
                    LauncherLayoutTests.AssertSettingsHeader(panel, "ApplyGraphicsButton");
                }
                var quality = (Slider)panel.FindName("Quality");
                quality.Value = 2; Pump();
                Assert(model.GraphicsQuality == 2 && model.HasGraphicsChanges, "quality slider was not two-way bound");
                var fullScreen = (ComboBox)panel.FindName("FullScreen");
                fullScreen.SelectedIndex = 1; Pump();
                Assert(model.GraphicsFullScreen, "display mode was not boolean-bound");
                var resolution = (ComboBox)panel.FindName("Resolution");
                Assert(resolution.Items.Cast<string>().SequenceEqual(model.GraphicsResolutions), "resolution dropdown differs from detected options");
                resolution.SelectedItem = "1280x720"; Pump();
                Assert(model.GraphicsResolution == "1280x720" && model.HasGraphicsChanges, "resolution selection was not two-way bound");
                bridge.Graphics = new GraphicsSettingsSnapshot(2, GraphicsSyncState.Ready, new GraphicsSettingsValues(1200, 800));
                model.Refresh(); Pump();
                Assert((string)resolution.SelectedItem == "1200x800" && !model.HasGraphicsChanges,
                    "rebuilding resolution options cleared the bound game value");
                bridge.Graphics = new GraphicsSettingsSnapshot(3, GraphicsSyncState.Ready, new GraphicsSettingsValues(1360, 768));
                model.Refresh(); Pump();
                Assert((string)resolution.SelectedItem == "1360x768" && !resolution.Items.Contains("1200x800")
                    && !model.HasGraphicsChanges && bridge.GraphicsApplyCount == 0,
                    "resolution synchronization retained stale options or triggered an apply");
            }
        }
        private static void TextElementFont(DependencyObject root)
        {
            root.SetValue(System.Windows.Documents.TextElement.FontFamilyProperty, Application.Current.Resources["UiFontFamily"]);
            root.SetValue(System.Windows.Documents.TextElement.ForegroundProperty, Application.Current.Resources["TextBrush"]);
        }
        private static MainWindowViewModel CreateModel(FakeLauncherBridge bridge, string directory) =>
            new MainWindowViewModel(bridge, new SettingsService(Path.Combine(directory, "graphics")),
                new UserSettings { ClientDirectory = @"C:\Games\T7" }, null,
                path => Task.FromResult(ValidDirectory(path)), new FakeDesktopInteraction(),
                readGraphicsResolutions: () => new[] { "1280x720", "1600x900", "1920x1080" });
    }
}
