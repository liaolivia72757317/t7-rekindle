using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class GraphicsResolutionTests
    {
        internal static void Run(string root)
        {
            TestEnumeration();
            TestOptions(Path.Combine(root, "resolution-options"));
            TestDetectionFailure(Path.Combine(root, "resolution-failure"));
        }

        private static void TestEnumeration()
        {
            Assert(Marshal.SizeOf(typeof(Direct3DEnvironment.DisplayMode)) == 16
                && Marshal.OffsetOf(typeof(Direct3DEnvironment.DisplayMode), "Format").ToInt32() == 12, "D3DDISPLAYMODE ABI layout");
            var formats = new List<uint>();
            var modes = new[]
            {
                new Direct3DEnvironment.DisplayMode { Width = 800, Height = 600, RefreshRate = 60, Format = 22 },
                new Direct3DEnvironment.DisplayMode { Width = 800, Height = 600, RefreshRate = 75, Format = 22 },
                new Direct3DEnvironment.DisplayMode { Width = 1920, Height = 1080, RefreshRate = 60, Format = 22 },
                new Direct3DEnvironment.DisplayMode { Width = 1280, Height = 720, RefreshRate = 60, Format = 22 }
            };
            var result = Direct3DEnvironment.ReadResolutions(format =>
            {
                formats.Add(format);
                return format == 22 ? (uint)modes.Length : 0;
            }, (format, index) => modes[index]);
            Assert(formats.SequenceEqual(new uint[] { 21, 22 }), "resolution enumeration included formats the game filters out");
            Assert(result.SequenceEqual(new[] { "800x600", "1920x1080", "1280x720" }), "refresh rates were not deduplicated in enumeration order");
            var failed = false;
            try { Direct3DEnvironment.ReadResolutions(_ => 0, (_, __) => default); }
            catch (InvalidOperationException) { failed = true; }
            Assert(failed, "empty display modes silently became hardcoded presets");
            failed = false;
            try { Direct3DEnvironment.ReadResolutions(_ => 1, (_, __) => throw new ExternalException("mode enumeration fixture")); }
            catch (ExternalException) { failed = true; }
            Assert(failed, "mode enumeration failure was hidden");
        }

        private static MainWindowViewModel Model(string root, string directory, FakeLauncherBridge bridge,
            Func<IReadOnlyList<string>> readResolutions) => new MainWindowViewModel(bridge,
                new SettingsService(Path.Combine(root, "launcher")),
                new UserSettings { ClientDirectory = Path.GetDirectoryName(directory) }, null,
                path => Task.FromResult(ValidDirectory(path)), new FakeDesktopInteraction(),
                isClientRunning: _ => false, readGraphicsResolutions: readResolutions);

        private static void TestOptions(string root)
        {
            var directory = GameSettingsFileTests.CreateFixture(Path.Combine(root, "client"));
            var bytes = File.ReadAllBytes(GameSettingsFileTests.ConfigPath(directory));
            var options = new[] { "800x600", "1600x900", "1280x720" };
            var bridge = new FakeLauncherBridge();
            var reads = 0;
            using (var model = Model(root, directory, bridge, () => { reads++; return options; }))
            {
                RunTask(model.ValidationTask); model.Refresh();
                Assert(model.GraphicsResolutions.SequenceEqual(options.Concat(new[] { "1920x1080" }))
                    && model.GraphicsResolution == "1920x1080" && !model.HasGraphicsChanges,
                    "offline resolution list ignored display modes or lost the current configuration");
                var before = reads;
                model.GraphicsResolution = "1280x720";
                model.RefreshGameSettings(true);
                Assert(reads == before && model.GraphicsResolution == "1280x720" && model.HasGraphicsChanges,
                    "polling re-enumerated modes or discarded the selected draft");
                model.ResetGraphicsCommand.Execute(null);
                Assert(model.GraphicsResolutions.SequenceEqual(options.Concat(new[] { "1440x900" }))
                    && model.GraphicsResolution == "1440x900", "reset lost its custom resolution or accumulated stale options");
                model.RevertGraphicsCommand.Execute(null);
                Assert(model.GraphicsResolutions.SequenceEqual(options.Concat(new[] { "1920x1080" }))
                    && model.GraphicsResolution == "1920x1080", "revert retained a stale custom resolution");

                options = new[] { "1024x768", "1280x720", "1600x900" };
                bridge.Snapshot = new SessionSnapshot { State = SessionState.Running };
                bridge.Graphics = new GraphicsSettingsSnapshot(1, GraphicsSyncState.Ready, new GraphicsSettingsValues(1600, 900));
                model.Refresh();
                Assert(model.GraphicsResolutions.SequenceEqual(options) && reads > before && model.GraphicsResolution == "1600x900",
                    "session start retained old display modes or duplicated the current resolution");
                model.GraphicsResolution = "1280x720";
                model.Refresh();
                Assert(model.GraphicsResolution == "1280x720" && model.CanApplyGraphics,
                    "memory polling discarded the resolution draft");
                bridge.Graphics = new GraphicsSettingsSnapshot(2, GraphicsSyncState.Ready, new GraphicsSettingsValues(1360, 768));
                model.Refresh();
                Assert(model.GraphicsResolutions.SequenceEqual(options.Concat(new[] { "1360x768" }))
                    && model.GraphicsResolution == "1360x768" && !model.HasGraphicsChanges,
                    "reverse synchronization lost a custom resolution");
                Assert(bridge.GraphicsApplyCount == 0 && File.ReadAllBytes(GameSettingsFileTests.ConfigPath(directory)).SequenceEqual(bytes),
                    "resolution enumeration wrote settings without save");
            }
        }

        private static void TestDetectionFailure(string root)
        {
            var directory = GameSettingsFileTests.CreateFixture(Path.Combine(root, "client"));
            var bridge = new FakeLauncherBridge();
            var fail = true;
            using (var model = Model(root, directory, bridge, () =>
            {
                if (fail) throw new InvalidOperationException("display mode fixture failure");
                return new[] { "1280x720", "1920x1080" };
            }))
            {
                RunTask(model.ValidationTask); model.Refresh();
                Assert(model.CanEditGraphics && model.GraphicsResolutions.SequenceEqual(new[] { "1920x1080" })
                    && model.GraphicsError.Contains("分辨率"), "detection failure fabricated options or hid the warning");
                model.GraphicsFog = true;
                Assert(model.GraphicsError.Contains("分辨率") && model.CanApplyGraphics, "editing hid the detection warning or blocked unrelated settings");
                fail = false;
                bridge.Snapshot = new SessionSnapshot { State = SessionState.Running };
                bridge.Graphics = new GraphicsSettingsSnapshot(1, GraphicsSyncState.Ready, new GraphicsSettingsValues(1920, 1080));
                model.Refresh();
                Assert(model.GraphicsError.Length == 0 && model.GraphicsResolutions.SequenceEqual(new[] { "1280x720", "1920x1080" }),
                    "successful detection retained its old warning");
            }
        }
    }
}
