using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using T7R.Core;
using T7R.Desktop.Services;

namespace T7R.ManagedHarness
{
    internal sealed class FakeClientEnvironment : IClientEnvironment
    {
        private readonly IClientEnvironment _fallback = new ClientEnvironment();
        private readonly Func<string, Task<ClientDirectoryResult>> _inspect;
        internal Func<string, CancellationToken, Task<ClientDirectoryResult>> Locate { get; set; }
        internal Func<string, Task<ClientVersionResult>> Version { get; set; }
        internal Func<string, bool> Running { get; set; }
        internal Func<IReadOnlyList<OutputDeviceOption>> Devices { get; set; }
        internal Func<Guid, IReadOnlyList<string>> Resolutions { get; set; }
        internal FakeClientEnvironment(Func<string, Task<ClientDirectoryResult>> inspect) { _inspect = inspect; }
        public Task<ClientDirectoryResult> InspectAsync(string path) => _inspect(path);
        public Task<ClientDirectoryResult> LocateAsync(string path, CancellationToken cancellation) =>
            Locate == null ? _fallback.LocateAsync(path, cancellation) : Locate(path, cancellation);
        public Task<ClientVersionResult> ReadVersionAsync(string path) => Version == null ? _fallback.ReadVersionAsync(path) : Version(path);
        public bool IsClientRunning(string path) => Running == null ? _fallback.IsClientRunning(path) : Running(path);
        public IReadOnlyList<OutputDeviceOption> ReadOutputDevices() => Devices == null ? _fallback.ReadOutputDevices() : Devices();
        public IReadOnlyList<string> ReadResolutions(Guid device) => Resolutions == null ? _fallback.ReadResolutions(device) : Resolutions(device);
    }
}
