using System;
using System.Runtime.Serialization;
using T7R.Core;

namespace T7R.Battle
{
    public sealed class LocalResourceOptions
    {
        public LocalResourceOptions(string clientDirectory, string cacheDirectory, uint rulesVersion,
            uint resourceVersion, string clientVersion)
        {
            if (!NativeBridgeContract.IsUtf8PathAcceptable(clientDirectory))
                throw new ArgumentException("Expected an absolute client directory.", nameof(clientDirectory));
            if (!NativeBridgeContract.IsUtf8PathAcceptable(cacheDirectory))
                throw new ArgumentException("Expected an absolute cache directory.", nameof(cacheDirectory));
            if (rulesVersion == 0) throw new ArgumentOutOfRangeException(nameof(rulesVersion));
            if (resourceVersion == 0) throw new ArgumentOutOfRangeException(nameof(resourceVersion));
            if (string.IsNullOrEmpty(clientVersion) || clientVersion.Length > 64)
                throw new ArgumentException("Expected a client version identifier.", nameof(clientVersion));
            foreach (var c in clientVersion)
                if (!(c >= 'a' && c <= 'z') && !(c >= 'A' && c <= 'Z') && !(c >= '0' && c <= '9') && c != '_' && c != '.' && c != '-')
                    throw new ArgumentException("Invalid client version identifier.", nameof(clientVersion));
            ClientDirectory = clientDirectory; CacheDirectory = cacheDirectory;
            RulesVersion = rulesVersion; ResourceVersion = resourceVersion; ClientVersion = clientVersion;
        }

        public string ClientDirectory { get; }
        public string CacheDirectory { get; }
        public uint RulesVersion { get; }
        public uint ResourceVersion { get; }
        public string ClientVersion { get; }

        internal byte[] Serialize() => PlatformProfileJson.Serialize(new Request
        {
            ClientDirectory = ClientDirectory, CacheDirectory = CacheDirectory,
            RulesVersion = RulesVersion, ResourceVersion = ResourceVersion, ClientVersion = ClientVersion
        });

        [DataContract]
        private sealed class Request
        {
            [DataMember(Name = "clientDirectory")] public string ClientDirectory;
            [DataMember(Name = "cacheDirectory")] public string CacheDirectory;
            [DataMember(Name = "rulesVersion")] public uint RulesVersion;
            [DataMember(Name = "resourceVersion")] public uint ResourceVersion;
            [DataMember(Name = "clientVersion")] public string ClientVersion;
        }
    }
}
