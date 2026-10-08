using System;

namespace T7.Rekindle.Desktop.Services
{
    public sealed class OutputDeviceOption
    {
        public OutputDeviceOption(Guid identifier, string name)
        { Id = identifier == Guid.Empty ? string.Empty : identifier.ToString("D"); Name = name; }
        public string Id { get; }
        public string Name { get; }
    }
}
