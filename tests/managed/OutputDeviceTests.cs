using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows.Controls;
using T7.Rekindle.Core;
using T7.Rekindle.Desktop.Services;
using T7.Rekindle.Desktop.ViewModels;
using T7.Rekindle.Desktop.Views;
using static T7.ManagedHarness.LauncherTests;

namespace T7.ManagedHarness
{
    internal static class OutputDeviceTests
    {
        private static readonly Guid DeviceA = new Guid("12345678-1234-5678-9abc-123456789abc");
        private static readonly Guid DeviceB = new Guid("abcdef01-1234-5678-9abc-123456789abc");
        private static readonly OutputDeviceOption[] Devices =
        {
            new OutputDeviceOption(DeviceA, "Graphics adapter A"),
            new OutputDeviceOption(DeviceB, "Graphics adapter B")
        };

        internal static void Run(string root)
        {
            TestPersistence(Path.Combine(root, "output-device-persistence"));
            TestSelection(Path.Combine(root, "output-device-selection"));
            TestUnavailable(Path.Combine(root, "output-device-unavailable"));
            TestSaveFailure(Path.Combine(root, "output-device-failure"));
        }

        private static MainWindowViewModel Model(SettingsService service, UserSettings initial, FakeLauncherBridge bridge,
            Func<IReadOnlyList<OutputDeviceOption>> devices = null, Func<Guid, IReadOnlyList<string>> resolutions = null) =>
            new MainWindowViewModel(bridge, service, initial, null,
                path => Task.FromResult(ValidDirectory(path)), new FakeDesktopInteraction(),
                isClientRunning: _ => false, readOutputDevices: devices ?? (() => Devices),
                readOutputDeviceResolutions: resolutions ?? (_ => new[] { "1280x720", "1920x1080" }));

        private static void TestPersistence(string root)
        {
            var service = new SettingsService(root);
            Assert(service.Load().OutputDeviceId == "", "new settings did not select the system default");
            service.Save(new UserSettings { OutputDeviceId = DeviceB.ToString("D"), SkipStartupAnimation = true });
            Assert(service.Load().OutputDeviceId == DeviceB.ToString("D") && service.Load().SkipStartupAnimation,
                "output device did not round-trip through settings.json");
            Assert(File.ReadAllText(Path.Combine(root, "settings.json")).Contains("\"outputDeviceId\""), "output device persisted outside settings.json");
            File.WriteAllText(Path.Combine(root, "settings.json"), "{\"schemaVersion\":1,\"updateChannel\":\"stable\"}");
            Assert(service.Load().OutputDeviceId == "" && service.LastWarning == "", "legacy settings did not use the system default");
            Assert(!SettingsSchema.IsValid(new UserSettings { OutputDeviceId = "adapter 1" })
                && !SettingsSchema.IsValid(new UserSettings { OutputDeviceId = null }), "invalid device identifiers accepted");
            using (var model = Model(service, new UserSettings { OutputDeviceId = DeviceB.ToString("D").ToUpperInvariant() }, new FakeLauncherBridge()))
                Assert(model.SelectedOutputDeviceId == DeviceB.ToString("D") && model.OutputDeviceError == "" && model.OutputDevices.Count == 3,
                    "identifier casing invented a missing adapter");
        }

        private static void TestSelection(string root)
        {
            var directory = GameSettingsFileTests.CreateFixture(Path.Combine(root, "client"));
            var service = new SettingsService(Path.Combine(root, "launcher"));
            var initial = new UserSettings { ClientDirectory = Path.GetDirectoryName(directory), OutputDeviceId = DeviceB.ToString("D") };
            var bytes = File.ReadAllBytes(GameSettingsFileTests.ConfigPath(directory));
            var bridge = new FakeLauncherBridge();
            var reads = new List<Guid>();
            using (var model = Model(service, initial, bridge, resolutions: id =>
            {
                reads.Add(id);
                return id == DeviceA ? new[] { "800x600", "1280x720" } : new[] { "1600x900", "1920x1080" };
            }))
            {
                RunTask(model.ValidationTask); model.Refresh();
                Assert(model.OutputDevices[0].Id == "" && reads.Last() == DeviceB, "saved device or default option was lost");
                model.SelectedOutputDeviceId = DeviceA.ToString("D");
                Assert(service.Load().OutputDeviceId == DeviceA.ToString("D") && reads.Last() == DeviceA
                    && model.GraphicsResolutions.SequenceEqual(new[] { "800x600", "1280x720", "1920x1080" }),
                    "offline adapter selection did not save or refresh display modes");
                Assert(bridge.OutputDeviceSetCount == 0 && bridge.GraphicsApplyCount == 0
                    && File.ReadAllBytes(GameSettingsFileTests.ConfigPath(directory)).SequenceEqual(bytes),
                    "adapter selection modified game settings or a running device");
                RunTask(model.CheckCommand.ExecuteAsync(null));
                Assert(bridge.OutputDevice == DeviceA && bridge.OutputDeviceSetCount == 1, "preflight ignored the selected adapter");
                bridge.PendingCheck = new TaskCompletionSource<OperationSnapshot>();
                var start = model.StartCommand.ExecuteAsync(null);
                Assert(!model.CanChangeOutputDevice, "device selection remained editable during startup");
                model.SelectedOutputDeviceId = DeviceB.ToString("D");
                Assert(model.SelectedOutputDeviceId == DeviceA.ToString("D"), "startup device changed after preflight began");
                bridge.PendingCheck.SetResult(new OperationSnapshot { Status = OperationStatus.Succeeded });
                RunTask(start);
                Assert(bridge.StartCount == 1 && bridge.OutputDevice == DeviceA && model.OutputDeviceStatus.Contains("等待"),
                    "startup lost the adapter or claimed success before renderer readback");
                bridge.Graphics = new GraphicsSettingsSnapshot(1, GraphicsSyncState.Ready, new GraphicsSettingsValues(1280, 720));
                model.Refresh();
                Assert(model.OutputDeviceStatus.Contains("已确认") && model.OutputDeviceStatus.Contains(Devices[0].Name), "renderer confirmation missing");
                var readCount = reads.Count;
                var setCount = bridge.OutputDeviceSetCount;
                model.SelectedOutputDeviceId = DeviceB.ToString("D");
                model.Refresh();
                Assert(service.Load().OutputDeviceId == DeviceB.ToString("D") && model.OutputDeviceStatus.Contains("下次启动")
                    && model.OutputDeviceStatus.Contains(Devices[0].Name) && reads.Count == readCount && bridge.OutputDeviceSetCount == setCount
                    && model.GraphicsResolutions.SequenceEqual(new[] { "800x600", "1280x720" }),
                    "pending adapter selection changed the active device or its resolutions");
                RunTask(model.StopCommand.ExecuteAsync(null));
                Assert(reads.Last() == DeviceB && model.GraphicsResolutions.Contains("1600x900"), "game exit retained old adapter modes");
                model.SelectedOutputDeviceId = "";
                Assert(service.Load().OutputDeviceId == "" && reads.Last() == Guid.Empty, "system default selection did not reset persisted identity");
            }
        }

        private static void TestUnavailable(string root)
        {
            var initial = new UserSettings { OutputDeviceId = DeviceB.ToString("D") };
            using (var model = Model(new SettingsService(root), initial, new FakeLauncherBridge(), () => new[] { Devices[0] }))
            {
                Assert(model.SelectedOutputDeviceId == initial.OutputDeviceId && model.OutputDevices.Last().Id == initial.OutputDeviceId
                    && model.OutputDeviceError.Length > 0, "missing saved device silently fell back to the default");
                model.SelectedOutputDeviceId = "";
                Assert(model.OutputDeviceError == "", "valid reselection retained the missing device error");
            }
            using (var model = Model(new SettingsService(root), new UserSettings(), new FakeLauncherBridge(),
                () => throw new InvalidOperationException("enumeration fixture")))
                Assert(model.OutputDevices.Count == 1 && model.OutputDeviceError.Length > 0, "device enumeration failure was hidden");
        }

        private static void TestSaveFailure(string root)
        {
            File.WriteAllText(root, "blocked settings directory");
            using (var model = Model(new SettingsService(root), new UserSettings(), new FakeLauncherBridge()))
            {
                model.SelectedOutputDeviceId = DeviceA.ToString("D");
                Assert(model.SelectedOutputDeviceId == "" && model.OutputDeviceError.Length > 0, "failed save changed the device selection");
            }
        }

        internal static void Render(string root, string output)
        {
            var service = new SettingsService(Path.Combine(root, "output-device-ui"));
            using (var model = Model(service, new UserSettings(), new FakeLauncherBridge()))
            {
                RunTask(model.ValidationTask); model.SettingsTabIndex = 2;
                var page = new GameSettingsPage { DataContext = model };
                page.FontFamily = (System.Windows.Media.FontFamily)System.Windows.Application.Current.Resources["UiFontFamily"];
                page.Foreground = (System.Windows.Media.Brush)System.Windows.Application.Current.Resources["TextBrush"];
                System.Windows.Media.TextOptions.SetTextFormattingMode(page, System.Windows.Media.TextFormattingMode.Display);
                var canvas = new Grid { Children = { page }, Background = page.Background };
                canvas.SetValue(System.Windows.Documents.TextElement.FontFamilyProperty, page.FontFamily);
                System.Windows.Media.TextOptions.SetTextFormattingMode(canvas, System.Windows.Media.TextFormattingMode.Display);
                var selector = (ComboBox)page.FindName("OutputDeviceSelector");
                foreach (var scale in new[] { 1.0, 1.5, 2.0 })
                {
                    LauncherLayoutTests.Render(canvas, null, output, "output-device-" + (int)(scale * 100), 680, 650, scale);
                    LauncherLayoutTests.AssertWithin(selector, page, 680, 650);
                    LauncherLayoutTests.Render(canvas, null, output, "output-device-compact-" + (int)(scale * 100), 480, 500, scale);
                    LauncherLayoutTests.AssertWithin(selector, page, 480, 500);
                }
                Assert(selector.Items.Count == 3 && (string)selector.SelectedValue == "", "output device control lost the default selection");
                Assert(HasText(selector, "系统默认"), "output device control displayed a type name instead of the default label");
                selector.SelectedIndex = 2; Pump();
                Assert(model.SelectedOutputDeviceId == DeviceB.ToString("D") && service.Load().OutputDeviceId == DeviceB.ToString("D"),
                    "output device selection was not two-way bound and saved");
                Assert(HasText(selector, Devices[1].Name), "selected output device name was not rendered");
                model.SelectedOutputDeviceId = ""; Pump();
                Assert(selector.SelectedIndex == 0, "restoring default did not update the dropdown");
            }
        }

        private static bool HasText(System.Windows.DependencyObject element, string text)
        {
            if (element is TextBlock block && block.Text == text) return true;
            for (var i = 0; i < System.Windows.Media.VisualTreeHelper.GetChildrenCount(element); i++)
                if (HasText(System.Windows.Media.VisualTreeHelper.GetChild(element, i), text)) return true;
            return false;
        }
    }
}
