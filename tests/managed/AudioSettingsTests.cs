using System;
using System.IO;
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
    internal static class AudioSettingsTests
    {
        internal static void Run(string directory)
        {
            var bridge = new FakeLauncherBridge();
            using (var model = CreateModel(bridge, directory))
            {
                RunTask(model.ValidationTask); model.Refresh();
                Assert(!model.CanEditAudio && !model.ApplyAudioCommand.CanExecute(null), "offline audio accepted writes");
                var current = new AudioSettingsValues(false, .75f, true, .25f);
                bridge.Snapshot = new SessionSnapshot { State = SessionState.Running };
                bridge.Audio = new AudioSettingsSnapshot(1, AudioSyncState.Ready, current); model.Refresh();
                Assert(model.CanEditAudio && model.AudioMusicEnabled && !model.AudioEffectsEnabled
                    && model.AudioMusicVolume == 75 && model.AudioEffectsVolume == 25 && !model.HasAudioChanges,
                    "initial audio mute/volume mapping");
                model.AudioMusicVolume = 40; model.AudioMusicEnabled = false;
                model.Refresh();
                Assert(model.CanApplyAudio && bridge.AudioApplyCount == 0 && model.AudioMusicVolume == 40,
                    "polling lost audio draft or applied it without save");
                model.ApplyAudioCommand.Execute(null);
                Assert(bridge.AudioApplyCount == 1 && bridge.AppliedAudioRevision == 1
                    && bridge.AppliedAudio.MusicMuted && bridge.AppliedAudio.MusicVolume == .4f
                    && bridge.AppliedAudio.EffectsMuted && bridge.AppliedAudio.EffectsVolume == .25f && !model.CanEditAudio,
                    "audio save did not retain muted volume or await acknowledgement");
                model.ApplyAudioCommand.Execute(null);
                Assert(bridge.AudioApplyCount == 1, "duplicate audio save");
                bridge.Audio = new AudioSettingsSnapshot(2, AudioSyncState.Ready, bridge.AppliedAudio); model.Refresh();
                Assert(!model.HasAudioChanges && model.AudioStatus.Contains("回读确认"), "audio acknowledgement missing");
                model.AudioEffectsVolume = 5;
                bridge.Audio = new AudioSettingsSnapshot(3, AudioSyncState.Ready, current); model.Refresh();
                Assert(model.AudioEffectsVolume == 25 && !model.HasAudioChanges && bridge.AudioApplyCount == 1,
                    "in-game audio update caused feedback write or retained stale draft");
                model.AudioMusicVolume = 35; model.ApplyAudioCommand.Execute(null);
                bridge.Audio = new AudioSettingsSnapshot(4, AudioSyncState.Conflict, current); model.Refresh();
                Assert(model.AudioMusicVolume == 75 && model.AudioStatus.Contains("未覆盖"), "audio conflict clobbered game values");
                model.AudioEffectsEnabled = true; bridge.AudioError = new InvalidOperationException("fixture audio error");
                model.ApplyAudioCommand.Execute(null);
                Assert(model.HasAudioChanges && model.AudioError.Contains("fixture"), "audio failure was hidden");
                bridge.AudioError = null; model.RevertAudioCommand.Execute(null);
                Assert(!model.HasAudioChanges && model.AudioError.Length == 0, "audio revert retained error or draft");
                model.ResetAudioCommand.Execute(null);
                Assert(model.HasAudioChanges && model.AudioMusicVolume == 100 && model.AudioEffectsEnabled
                    && bridge.AudioApplyCount == 2 && bridge.GraphicsApplyCount == 0, "audio reset affected game or graphics without save");
                bridge.Audio = new AudioSettingsSnapshot(4, AudioSyncState.Failed, null); model.Refresh();
                Assert(!model.CanEditAudio && model.AudioError.Length != 0, "audio sync failure retained write controls");
                bridge.Snapshot = new SessionSnapshot { State = SessionState.Idle }; model.Refresh();
                Assert(!model.CanEditAudio && !model.CanApplyAudio, "game exit retained audio controls");
            }
            Assert(new AudioSettingsValues(musicVolume: 0, effectsVolume: 1).IsValid
                && !new AudioSettingsValues(musicVolume: float.NaN).IsValid
                && !new AudioSettingsValues(effectsVolume: float.PositiveInfinity).IsValid
                && !new AudioSettingsValues(musicVolume: -.01f).IsValid
                && !new AudioSettingsValues(effectsVolume: 1.01f).IsValid, "audio bounds");
        }
        internal static void Render(string directory, string output)
        {
            var bridge = new FakeLauncherBridge
            {
                Snapshot = new SessionSnapshot { State = SessionState.Running },
                Audio = new AudioSettingsSnapshot(1, AudioSyncState.Ready, new AudioSettingsValues(false, .75f, true, .25f))
            };
            using (var model = CreateModel(bridge, directory))
            {
                RunTask(model.ValidationTask); model.Refresh();
                var panel = new AudioSettingsPanel { DataContext = model };
                var root = new Grid { Children = { panel }, Background = (System.Windows.Media.Brush)Application.Current.Resources["PanelBackgroundBrush"] };
                System.Windows.Media.TextOptions.SetTextFormattingMode(root, System.Windows.Media.TextFormattingMode.Display);
                root.SetValue(System.Windows.Documents.TextElement.FontFamilyProperty, Application.Current.Resources["UiFontFamily"]);
                root.SetValue(System.Windows.Documents.TextElement.ForegroundProperty, Application.Current.Resources["TextBrush"]);
                foreach (var scale in new[] { 1.0, 1.5, 2.0 })
                {
                    LauncherLayoutTests.Render(root, null, output, "audio-" + (int)(scale * 100), 640, 390, scale);
                    LauncherLayoutTests.AssertSettingsHeader(panel, "ApplyAudioButton");
                    LauncherLayoutTests.Render(root, null, output, "audio-compact-" + (int)(scale * 100), 480, 390, scale);
                    LauncherLayoutTests.AssertSettingsHeader(panel, "ApplyAudioButton");
                }
                ((Slider)panel.FindName("MusicVolume")).Value = 40; Pump();
                ((CheckBox)panel.FindName("EffectsEnabled")).IsChecked = true; Pump();
                Assert(model.AudioMusicVolume == 40 && model.AudioEffectsEnabled && model.HasAudioChanges,
                    "audio sliders/switches were not two-way bound");
            }
        }
        private static MainWindowViewModel CreateModel(FakeLauncherBridge bridge, string directory) =>
            new MainWindowViewModel(bridge, new SettingsService(Path.Combine(directory, "audio")),
                new UserSettings { ClientDirectory = @"C:\Games\T7" }, null,
                path => Task.FromResult(ValidDirectory(path)), new FakeDesktopInteraction());
    }
}
