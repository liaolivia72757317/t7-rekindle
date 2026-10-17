using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.Serialization;
using T7R.Core;

namespace T7R.Battle
{
    internal static class LocalTrainingMode
    {
        private static readonly Lazy<byte[]> Data = new Lazy<byte[]>(ReadResources);
        private static readonly Lazy<Dictionary<string, Mode>> Modes =
            new Lazy<Dictionary<string, Mode>>(() => PlatformProfileJson.Deserialize<Bundle>(Data.Value).Modes);

        internal static byte[] Resources => Data.Value;
        internal static Mode Find(uint mapId) => Modes.Value.TryGetValue(
            mapId.ToString(System.Globalization.CultureInfo.InvariantCulture), out var mode) ? mode : null;

        private static byte[] ReadResources()
        {
            using (var stream = typeof(LocalTrainingMode).Assembly.GetManifestResourceStream("T7R.TrainingLoadouts"))
            using (var buffer = new MemoryStream())
            {
                if (stream == null) throw new InvalidDataException("Missing training loadouts.");
                stream.CopyTo(buffer);
                return buffer.ToArray();
            }
        }

        [DataContract] private sealed class Bundle
        {
            [DataMember(Name = "modes", IsRequired = true)] public Dictionary<string, Mode> Modes { get; set; }
        }

        [DataContract] internal sealed class Mode
        {
            [DataMember(Name = "heroId", IsRequired = true)] public uint HeroId { get; set; }
            [DataMember(Name = "weaponId", IsRequired = true)] public uint WeaponId { get; set; }
            [DataMember(Name = "equipment", IsRequired = true)] public Equipment[] Equipment { get; set; }

            internal BattleMatchMember Member(uint actorId, byte kind, byte camp) => new BattleMatchMember
            {
                ActorId = actorId, ClientRid = checked((ushort)actorId), Kind = kind, Camp = camp,
                HeroId = HeroId, WeaponId = WeaponId,
                Equipment = Equipment.Select(item => new BattleMatchEquipment
                    { Slot = item.Slot, WeaponId = item.WeaponId }).ToArray()
            };
        }

        [DataContract] internal sealed class Equipment
        {
            [DataMember(Name = "slot", IsRequired = true)] public byte Slot { get; set; }
            [DataMember(Name = "weaponId", IsRequired = true)] public uint WeaponId { get; set; }
        }
    }
}
