using System;
using System.IO;
using System.Threading.Tasks;
using System.Windows.Controls;
using Newtonsoft.Json.Linq;
using T7R.Core;
using T7R.Desktop.Services;
using T7R.Desktop.ViewModels;
using T7R.Desktop.Views;
using static T7R.ManagedHarness.LauncherTests;

namespace T7R.ManagedHarness
{
    internal static class GuardDebugTests
    {
        internal static void Run(string directory)
        {
            var service = new SettingsService(directory);
            Assert(!service.Load().ShowGuardDebug, "guard debug must default off");
            service.Save(new UserSettings { PlayerName = "测试玩家", ShowGuardDebug = true });
            var path = Path.Combine(directory, "settings.json");
            var json = JObject.Parse(File.ReadAllText(path));
            Assert((bool)json["showGuardDebug"], "guard debug camelCase persistence");
            json.Remove("showGuardDebug"); File.WriteAllText(path, json.ToString());
            Assert(!service.Load().ShowGuardDebug, "old settings must default off");
            var bridge = new FakeLauncherBridge();
            using (var model = CreateModel(bridge, service, service.Load()))
            {
                RunTask(model.ValidationTask);
                var page = new GameSettingsPage { DataContext = model };
                var toggle = (CheckBox)page.FindName("GuardDebugSwitch");
                Pump();
                Assert(toggle != null && toggle.IsChecked == false, "guard toggle default binding");
                bridge.Snapshot.State = SessionState.Running;
                toggle.IsChecked = true; Pump();
                Assert(model.ShowGuardDebug && bridge.ShowGuardDebug && service.Load().ShowGuardDebug,
                    "running guard debug must apply and persist immediately");
                model.MinimizeToTray = true; model.SaveSettings();
                service.SaveMatchMaps(new uint[] { 1003 });
                Assert(service.Load().ShowGuardDebug && service.Load().PlayerName == "测试玩家", "unrelated save lost guard preference");
                bridge.RejectedGuardDebug = false;
                toggle.IsChecked = false; Pump();
                Assert(toggle.IsChecked == true && bridge.ShowGuardDebug && service.Load().ShowGuardDebug
                    && model.PreferenceError.Length != 0, "native failure must roll back the toggle");
                bridge.RejectedGuardDebug = null;
                using (File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read))
                {
                    toggle.IsChecked = false; Pump();
                    Assert(toggle.IsChecked == true && bridge.ShowGuardDebug && model.PreferenceError.Length != 0,
                        "save failure must restore the renderer and toggle");
                }
                toggle.IsChecked = false; Pump();
                Assert(!model.ShowGuardDebug && !bridge.ShowGuardDebug && !service.Load().ShowGuardDebug
                    && model.PreferenceError.Length == 0, "guard toggle retry failed");
                toggle.IsChecked = true; Pump();
            }
            using (var restarted = CreateModel(bridge, service, service.Load()))
                Assert(restarted.ShowGuardDebug && bridge.ShowGuardDebug, "restart must apply the saved guard setting");
        }

        private static MainWindowViewModel CreateModel(FakeLauncherBridge bridge, SettingsService service, UserSettings settings)
            => new MainWindowViewModel(bridge, service, settings, null,
                new FakeClientEnvironment(path => Task.FromResult(ValidDirectory(path))), new FakeDesktopInteraction(),
                updates: new LauncherUpdateFactory(check: channel => Task.FromResult(new LauncherUpdateInfo { Channel = channel })));
    }
}
