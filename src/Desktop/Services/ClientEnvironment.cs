using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using T7R.Core;

namespace T7R.Desktop.Services
{
    internal interface IClientEnvironment
    {
        Task<ClientDirectoryResult> InspectAsync(string path);
        Task<ClientDirectoryResult> LocateAsync(string path, CancellationToken cancellation);
        Task<ClientVersionResult> ReadVersionAsync(string path);
        bool IsClientRunning(string path);
        IReadOnlyList<OutputDeviceOption> ReadOutputDevices();
        IReadOnlyList<string> ReadResolutions(Guid device);
    }

    internal sealed class ClientEnvironment : IClientEnvironment
    {
        public Task<ClientDirectoryResult> InspectAsync(string path) => Task.Run(() => ClientDirectoryService.Inspect(path));
        public Task<ClientDirectoryResult> LocateAsync(string path, CancellationToken cancellation) =>
            Task.Run(() => ClientDirectoryService.Locate(path, cancellation), cancellation);
        public Task<ClientVersionResult> ReadVersionAsync(string path) => Task.Run(() => ClientVersionService.Read(path));
        public bool IsClientRunning(string path) => GameSettingsFileService.IsClientRunning(path);
        public IReadOnlyList<OutputDeviceOption> ReadOutputDevices() => Direct3DEnvironment.ReadOutputDevices();
        public IReadOnlyList<string> ReadResolutions(Guid device) => Direct3DEnvironment.ReadResolutions(device);
    }
}
