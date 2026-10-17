using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Xml;
using System.Xml.Linq;
using T7R.Core;

namespace T7R.Battle
{
    internal static class PlatformProfileJson
    {
        internal const int MaxBytes = 1024 * 1024;

        internal static PlatformProfile Read(byte[] bytes)
        {
            if (bytes.Length > MaxBytes) throw new InvalidDataException("Player profile exceeds size limit.");
            try
            {
                using (var reader = JsonReaderWriterFactory.CreateJsonReader(bytes, new XmlDictionaryReaderQuotas
                { MaxDepth = 16, MaxArrayLength = MaxBytes, MaxStringContentLength = MaxBytes, MaxNameTableCharCount = MaxBytes }))
                {
                    var root = XDocument.Load(reader).Root;
                    var fields = Object(root, "schemaVersion", "revision", "formation", "weapons");
                    if (Integer(fields["schemaVersion"]) != 1) throw new InvalidDataException("Unsupported player profile schema.");
                    var formation = Array(fields["formation"]).Select(Int32).ToArray();
                    var weapons = Object(fields["weapons"]).Select(pair =>
                    {
                        if (!int.TryParse(pair.Key, NumberStyles.None, CultureInfo.InvariantCulture, out var id) ||
                            id <= 0 || id.ToString(CultureInfo.InvariantCulture) != pair.Key)
                            throw new InvalidDataException("Invalid profile hero identifier.");
                        var slots = Array(pair.Value).Select(item =>
                        {
                            var slot = Object(item, "slot", "group", "index");
                            return new PlatformWeaponSlot(Int32(slot["slot"]), Int32(slot["group"]), Int32(slot["index"]));
                        }).ToArray();
                        return new KeyValuePair<int, IReadOnlyList<PlatformWeaponSlot>>(id, slots);
                    });
                    return new PlatformProfile(Integer(fields["revision"]), formation, weapons);
                }
            }
            catch (Exception error) when (error is XmlException || error is ArgumentException || error is OverflowException || error is SerializationException)
            { throw new InvalidDataException("Invalid player profile.", error); }
        }

        internal static byte[] Write(PlatformProfile profile) => Serialize(ToData(profile));

        internal static ProfileData ToData(PlatformProfile profile) => new ProfileData
        {
            SchemaVersion = 1, Revision = profile.Revision, Formation = profile.Formation.ToArray(),
            Weapons = profile.Weapons.ToDictionary(pair => pair.Key.ToString(CultureInfo.InvariantCulture),
                pair => pair.Value.Select(item => new SlotData { Slot = item.Slot, Group = item.Group, Index = item.Index }).ToArray())
        };

        internal static byte[] Serialize<T>(T value)
        {
            using (var stream = new MemoryStream())
            {
                Serializer<T>().WriteObject(stream, value);
                if (stream.Length > MaxBytes) throw new InvalidDataException("Platform JSON exceeds size limit.");
                return stream.ToArray();
            }
        }

        internal static T Deserialize<T>(byte[] bytes)
        {
            using (var stream = new MemoryStream(bytes, false)) return (T)Serializer<T>().ReadObject(stream);
        }
        private static DataContractJsonSerializer Serializer<T>() => new DataContractJsonSerializer(typeof(T),
            new DataContractJsonSerializerSettings { UseSimpleDictionaryFormat = true, MaxItemsInObjectGraph = MaxBytes });

        private static Dictionary<string, XElement> Object(XElement value, params string[] fields)
        {
            if (value == null || (string)value.Attribute("type") != "object") throw new InvalidDataException("Expected profile object.");
            var result = value.Elements().ToDictionary(element => (string)element.Attribute("item") ?? element.Name.LocalName);
            if (fields.Length > 0 && (result.Count != fields.Length || fields.Any(field => !result.ContainsKey(field))))
                throw new InvalidDataException("Unexpected profile fields.");
            return result;
        }
        private static IEnumerable<XElement> Array(XElement value)
        {
            if ((string)value.Attribute("type") != "array") throw new InvalidDataException("Expected profile array.");
            return value.Elements();
        }
        private static ulong Integer(XElement value)
        {
            if ((string)value.Attribute("type") != "number" ||
                !ulong.TryParse(value.Value, NumberStyles.None, CultureInfo.InvariantCulture, out var result))
                throw new InvalidDataException("Expected exact nonnegative profile integer.");
            return result;
        }
        private static int Int32(XElement value) => checked((int)Integer(value));

        [DataContract] internal sealed class ProfileData
        {
            [DataMember(Name = "schemaVersion", Order = 0)] public int SchemaVersion { get; set; }
            [DataMember(Name = "revision", Order = 1)] public ulong Revision { get; set; }
            [DataMember(Name = "formation", Order = 2)] public int[] Formation { get; set; }
            [DataMember(Name = "weapons", Order = 3)] public Dictionary<string, SlotData[]> Weapons { get; set; }
        }
        [DataContract] internal sealed class SlotData
        {
            [DataMember(Name = "slot", IsRequired = true)] public int Slot { get; set; }
            [DataMember(Name = "group", IsRequired = true)] public int Group { get; set; }
            [DataMember(Name = "index", IsRequired = true)] public int Index { get; set; }
            internal PlatformWeaponSlot ToSlot() => new PlatformWeaponSlot(Slot, Group, Index);
        }
    }
}
