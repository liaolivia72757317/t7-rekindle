using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Runtime.Serialization;

namespace T7R.Battle
{
    [StructLayout(LayoutKind.Sequential, Pack = 8)]
    internal struct NativeCombatProfile
    {
        internal int HeroId, WeaponId;
        internal float ForwardSlow, LateralSlow, BackwardSlow, ForwardSprint;
        internal uint DashDurationMs, DashMoveMs, DashSpeedMilli;
    }

    internal static class LocalCombatProfiles
    {
        internal static NativeCombatProfile[] FromResources(byte[] resources)
        {
            if (resources == null || resources.Length == 0 || resources.Length > 64 * 1024 * 1024)
                throw new ArgumentException("Expected bounded resource bundle.", nameof(resources));
            var bundle = PlatformProfileJson.Deserialize<BundleData>(resources);
            if (bundle?.SchemaVersion != 1 || bundle.CombatProfiles == null)
                throw new InvalidDataException("Missing combat resource profiles.");
            var result = new List<NativeCombatProfile>();
            foreach (var hero in bundle.CombatProfiles.OrderBy(pair => pair.Key, StringComparer.Ordinal))
            {
                if (!int.TryParse(hero.Key, NumberStyles.None, CultureInfo.InvariantCulture, out var heroId)
                    || heroId <= 0 || hero.Value == null)
                    throw new InvalidDataException("Invalid combat hero binding.");
                foreach (var weapon in hero.Value.OrderBy(pair => pair.Key, StringComparer.Ordinal))
                {
                    if (!int.TryParse(weapon.Key, NumberStyles.None, CultureInfo.InvariantCulture, out var weaponId)
                        || weaponId <= 0 || weapon.Value == null)
                        throw new InvalidDataException("Invalid combat weapon binding.");
                    if (weapon.Value.Shield) continue;
                    var movement = weapon.Value.Movement;
                    var dash = weapon.Value.Dash;
                    if (movement == null || !movement.Enabled || movement.WeaponId != weaponId
                        || movement.ForwardSlow <= 0 || movement.LateralSlow <= 0
                        || movement.BackwardSlow <= 0 || movement.ForwardSprint <= movement.ForwardSlow
                        || movement.ForwardSprint <= movement.LateralSlow
                        || float.IsNaN(movement.ForwardSlow) || float.IsNaN(movement.LateralSlow)
                        || float.IsNaN(movement.BackwardSlow) || float.IsNaN(movement.ForwardSprint)
                        || float.IsInfinity(movement.ForwardSlow) || float.IsInfinity(movement.LateralSlow)
                        || float.IsInfinity(movement.BackwardSlow) || float.IsInfinity(movement.ForwardSprint))
                        throw new InvalidDataException("Invalid combat movement profile.");
                    if (dash != null && dash.Enabled && (dash.WeaponId != weaponId || dash.MoveMs == 0
                        || dash.MoveMs > dash.DurationMs || dash.MoveMs > 65535 || dash.SpeedMilli == 0))
                        throw new InvalidDataException("Invalid combat dash profile.");
                    result.Add(new NativeCombatProfile
                    {
                        HeroId = heroId, WeaponId = weaponId,
                        ForwardSlow = movement.ForwardSlow, LateralSlow = movement.LateralSlow,
                        BackwardSlow = movement.BackwardSlow, ForwardSprint = movement.ForwardSprint,
                        DashDurationMs = dash != null && dash.Enabled ? dash.DurationMs : 0,
                        DashMoveMs = dash != null && dash.Enabled ? dash.MoveMs : 0,
                        DashSpeedMilli = dash != null && dash.Enabled ? dash.SpeedMilli : 0
                    });
                }
            }
            if (result.Count == 0 || result.Count > 2048)
                throw new InvalidDataException("Invalid combat profile count.");
            return result.ToArray();
        }

        [DataContract] private sealed class BundleData
        {
            [DataMember(Name = "schemaVersion", IsRequired = true)] public int SchemaVersion { get; set; }
            [DataMember(Name = "combatProfiles", IsRequired = true)]
            public Dictionary<string, Dictionary<string, WeaponData>> CombatProfiles { get; set; }
        }
        [DataContract] private sealed class WeaponData
        {
            [DataMember(Name = "shield", IsRequired = true)] public bool Shield { get; set; }
            [DataMember(Name = "movementAnimation")] public MovementData Movement { get; set; }
            [DataMember(Name = "dashAttack")] public DashData Dash { get; set; }
        }
        [DataContract] private sealed class MovementData
        {
            [DataMember(Name = "enabled")] public bool Enabled { get; set; }
            [DataMember(Name = "weaponId")] public int WeaponId { get; set; }
            [DataMember(Name = "forwardSlow")] public float ForwardSlow { get; set; }
            [DataMember(Name = "lateralSlow")] public float LateralSlow { get; set; }
            [DataMember(Name = "backwardSlow")] public float BackwardSlow { get; set; }
            [DataMember(Name = "forwardSprint")] public float ForwardSprint { get; set; }
        }
        [DataContract] private sealed class DashData
        {
            [DataMember(Name = "enabled")] public bool Enabled { get; set; }
            [DataMember(Name = "weaponId")] public int WeaponId { get; set; }
            [DataMember(Name = "durationMs")] public uint DurationMs { get; set; }
            [DataMember(Name = "moveMs")] public uint MoveMs { get; set; }
            [DataMember(Name = "speedMilli")] public uint SpeedMilli { get; set; }
        }
    }
}
