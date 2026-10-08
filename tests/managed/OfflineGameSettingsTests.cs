using System;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class OfflineGameSettingsTests
    {
        internal static void Run(string root)
        {
            TestOffline(Path.Combine(root, "offline-settings"));
            TestLifecycle(Path.Combine(root, "settings-source-switch"));
            TestDirectoryAndErrors(Path.Combine(root, "settings-file-errors"));
        }

        private static MainWindowViewModel Model(string root, string binaryDirectory, FakeLauncherBridge bridge,
            Func<string, bool> running = null) => new MainWindowViewModel(bridge, new SettingsService(Path.Combine(root, "launcher")),
                new UserSettings { ClientDirectory = Path.GetDirectoryName(binaryDirectory) }, null,
                path => Task.FromResult(ValidDirectory(path)), new FakeDesktopInteraction(), isClientRunning: running ?? (_ => false));

        private static void TestOffline(string root)
        {
            var directory = GameSettingsFileTests.CreateFixture(Path.Combine(root, "client"));
            var path = GameSettingsFileTests.ConfigPath(directory);
            var bridge = new FakeLauncherBridge();
            using (var model = Model(root, directory, bridge))
            {
                RunTask(model.ValidationTask); model.Refresh();
                Assert(model.CanEditGraphics && model.CanEditAudio && model.GraphicsQuality == 3 && model.AudioMusicVolume == 75
                    && model.GraphicsStatus.Contains("文件") && model.AudioStatus.Contains("文件"), "offline controls did not load the file");
                model.GraphicsFrameLimit = true; model.AudioMusicVolume = 40;
                model.RefreshGameSettings(true);
                Assert(model.HasGraphicsChanges && model.HasAudioChanges && model.AudioMusicVolume == 40, "file polling discarded drafts without an external change");
                model.ApplyGraphicsCommand.Execute(null);
                Assert(!model.HasGraphicsChanges && model.HasAudioChanges && model.GraphicsStatus.Contains("下次启动")
                    && GameSettingsFileService.Read(directory).Graphics.FrameLimit, "offline graphics save or independent audio draft");
                model.ApplyAudioCommand.Execute(null);
                Assert(!model.HasAudioChanges && GameSettingsFileService.Read(directory).Audio.MusicVolume == .4f
                    && bridge.GraphicsApplyCount == 0 && bridge.AudioApplyCount == 0, "offline save called the memory bridge");

                model.GraphicsFog = true; model.AudioEffectsVolume = 10;
                var current = GameSettingsFileService.Read(directory);
                var external = new AudioSettingsValues(true, .2f, true, .6f);
                GameSettingsFileService.SaveAudio(directory, current.Audio, external, out _);
                model.RefreshGameSettings(true);
                Assert(model.HasGraphicsChanges && !model.HasAudioChanges && !model.AudioMusicEnabled
                    && Math.Abs(model.AudioEffectsVolume - 60) < .01, "file change clobbered another group or failed to update UI");

                model.AudioMusicVolume = 90;
                GameSettingsFileService.SaveAudio(directory, external, new AudioSettingsValues(), out _);
                var before = File.ReadAllBytes(path);
                model.ApplyAudioCommand.Execute(null);
                Assert(model.AudioMusicVolume == 100 && model.AudioStatus.Contains("未覆盖")
                    && File.ReadAllBytes(path).SequenceEqual(before), "file conflict was overwritten");
                model.RevertGraphicsCommand.Execute(null);
                Assert(!model.HasGraphicsChanges && !model.GraphicsFog, "offline revert did not restore the saved file");
                model.ResetGraphicsCommand.Execute(null);
                Assert(model.HasGraphicsChanges && File.ReadAllBytes(path).SequenceEqual(before), "offline reset saved without confirmation");
            }
        }

        private static void TestLifecycle(string root)
        {
            var directory = GameSettingsFileTests.CreateFixture(Path.Combine(root, "client"));
            var path = GameSettingsFileTests.ConfigPath(directory);
            var bridge = new FakeLauncherBridge();
            using (var model = Model(root, directory, bridge))
            {
                RunTask(model.ValidationTask); model.Refresh();
                model.GraphicsFrameLimit = true; model.AudioMusicVolume = 35;
                model.ApplyGraphicsCommand.Execute(null); model.ApplyAudioCommand.Execute(null);
                var saved = GameSettingsFileService.Read(directory);
                var bytes = File.ReadAllBytes(path);
                model.GraphicsFog = true; model.AudioMusicVolume = 90;
                bridge.Snapshot = new SessionSnapshot { State = SessionState.StartingClient };
                model.Refresh();
                Assert(!model.CanEditGraphics && !model.CanEditAudio, "launching retained file editing");
                model.ApplyGraphicsCommand.Execute(null); model.ApplyAudioCommand.Execute(null);
                Assert(File.ReadAllBytes(path).SequenceEqual(bytes), "launch transition flushed an unsaved draft");
                bridge.Snapshot = new SessionSnapshot { State = SessionState.Running };
                model.Refresh();
                Assert(!model.CanEditGraphics && !model.CanEditAudio && model.GraphicsStatus.Contains("等待"), "uninitialized game fell back to file writes");
                bridge.Graphics = new GraphicsSettingsSnapshot(1, GraphicsSyncState.Ready, saved.Graphics);
                bridge.Audio = new AudioSettingsSnapshot(1, AudioSyncState.Ready, saved.Audio);
                model.Refresh();
                Assert(model.CanEditGraphics && model.CanEditAudio && !model.GraphicsFog
                    && Math.Abs(model.AudioMusicVolume - 35) < .01 && !model.HasGraphicsChanges && !model.HasAudioChanges,
                    "first memory snapshot retained a file draft");
                model.GraphicsViewDistance = 250; model.AudioMusicVolume = 45;
                model.ApplyGraphicsCommand.Execute(null); model.ApplyAudioCommand.Execute(null);
                Assert(bridge.GraphicsApplyCount == 1 && bridge.AudioApplyCount == 1
                    && bridge.AppliedGraphicsRevision == 1 && bridge.AppliedAudioRevision == 1
                    && File.ReadAllBytes(path).SequenceEqual(bytes), "running edits wrote the file instead of memory");
                bridge.Graphics = new GraphicsSettingsSnapshot(2, GraphicsSyncState.Ready, bridge.AppliedGraphics);
                bridge.Audio = new AudioSettingsSnapshot(2, AudioSyncState.Ready, bridge.AppliedAudio);
                model.Refresh();
                Assert(!model.HasGraphicsChanges && !model.HasAudioChanges, "memory acknowledgement lost after file mode");
                var gameGraphics = new GraphicsSettingsValues(1600, 900, false, 2, false, true, 300);
                var gameAudio = new AudioSettingsValues(false, .5f, false, .3f);
                bridge.Graphics = new GraphicsSettingsSnapshot(3, GraphicsSyncState.Ready, gameGraphics);
                bridge.Audio = new AudioSettingsSnapshot(3, AudioSyncState.Ready, gameAudio);
                model.Refresh();
                Assert(model.GraphicsViewDistance == 300 && model.AudioMusicVolume == 50
                    && bridge.GraphicsApplyCount == 1 && bridge.AudioApplyCount == 1, "in-game change did not sync without feedback writes");
                bridge.Snapshot = new SessionSnapshot { State = SessionState.StoppingClient };
                model.Refresh();
                Assert(!model.CanEditGraphics && !model.CanEditAudio, "stopping game permitted file editing");
                GameSettingsFileService.SaveGraphics(directory, saved.Graphics, gameGraphics, out _);
                GameSettingsFileService.SaveAudio(directory, saved.Audio, gameAudio, out _);
                bridge.Snapshot = new SessionSnapshot { State = SessionState.Idle, CleanupComplete = true };
                model.Refresh();
                Assert(model.CanEditGraphics && model.CanEditAudio && model.GraphicsQuality == 2 && model.AudioMusicVolume == 50
                    && model.GraphicsStatus.Contains("文件"), "game exit did not return to saved file settings");
            }
        }

        private static void TestDirectoryAndErrors(string root)
        {
            var directory = GameSettingsFileTests.CreateFixture(Path.Combine(root, "first"));
            var second = GameSettingsFileTests.CreateFixture(Path.Combine(root, "second"));
            var path = GameSettingsFileTests.ConfigPath(directory);
            var bytes = File.ReadAllBytes(path);
            var running = false;
            using (var model = Model(root, directory, new FakeLauncherBridge(), _ => running))
            {
                RunTask(model.ValidationTask); model.Refresh();
                model.AudioMusicVolume = 10;
                running = true;
                model.ApplyAudioCommand.Execute(null);
                Assert(File.ReadAllBytes(path).SequenceEqual(bytes) && model.AudioError.Length != 0, "external game raced an offline save");
                model.RefreshGameSettings(true);
                Assert(!model.CanEditGraphics && !model.CanEditAudio, "unmanaged running game allowed offline edits");
                running = false; model.RefreshGameSettings(true);
                Assert(model.CanEditGraphics && model.CanEditAudio && model.AudioError.Length == 0, "file editing failed to recover after game exit");
                File.WriteAllText(path, "broken"); model.RefreshGameSettings(true);
                Assert(!model.CanEditGraphics && !model.CanEditAudio && model.GraphicsError.Length != 0, "invalid file silently defaulted to writable settings");
                File.WriteAllBytes(path, bytes); model.RefreshGameSettings(true);
                Assert(model.CanEditGraphics && model.CanEditAudio, "repaired file remained disabled");
                model.GraphicsFog = true;
                model.BeginDirectoryDraft();
                Assert(!model.CanEditGraphics && !model.CanEditAudio, "directory draft retained stale write target");
                model.ClientDirectory = Path.GetDirectoryName(second);
                RunTask(model.ValidationTask); model.Refresh();
                Assert(!model.GraphicsFog && model.CanEditGraphics, "directory change retained previous file draft");
                model.GraphicsFog = true; model.ApplyGraphicsCommand.Execute(null);
                Assert(GameSettingsFileService.Read(second).Graphics.Fog && !GameSettingsFileService.Read(directory).Graphics.Fog,
                    "settings were saved to the previous client directory");
            }
        }
    }
}
