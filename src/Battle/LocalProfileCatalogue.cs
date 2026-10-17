using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.Serialization;
using T7R.Core;

namespace T7R.Battle
{
    internal sealed class LocalProfileCatalogue
    {
        private readonly Dictionary<int, HeroData> _heroes;
        private readonly Dictionary<int, WeaponData> _weapons;

        internal LocalProfileCatalogue(byte[] resources)
        {
            if (resources == null || resources.Length == 0 || resources.Length > 64 * 1024 * 1024)
                throw new ArgumentException("Expected bounded resource bundle.", nameof(resources));
            var bundle = PlatformProfileJson.Deserialize<BundleData>(resources);
            var data = bundle?.Catalogue;
            if (bundle?.SchemaVersion != 1 || data?.Heroes == null || data.Weapons == null ||
                data.Heroes.Length == 0 || data.Heroes.Length > 500 || data.Weapons.Length > 10000 ||
                data.Heroes.Any(hero => hero == null || hero.ResourceId <= 0) ||
                data.Weapons.Any(weapon => weapon == null || weapon.ResourceId <= 0 || weapon.Family < 0))
                throw new InvalidDataException("Invalid platform resource catalogue.");
            _heroes = data.Heroes.ToDictionary(hero => hero.ResourceId);
            _weapons = data.Weapons.ToDictionary(weapon => weapon.ResourceId);
            if (!_heroes.ContainsKey(1101) || !_weapons.ContainsKey(100000)) throw new InvalidDataException("Missing default hero or fists.");
            foreach (var hero in _heroes.Values)
            {
                if (hero.Groups?.Length != 8 || hero.Defaults == null || hero.Defaults.Length > 48 ||
                    hero.Groups.Any(group => group?.Length != 6 || group.Any(id => id < 0 || (id != 0 && !_weapons.ContainsKey(id)))) ||
                    !hero.Groups.SelectMany(group => group).Any(id => id != 0 && !Shield(id)))
                    throw new InvalidDataException("Invalid hero weapon groups.");
                foreach (var pair in hero.Defaults)
                {
                    if (pair?.Length != 2) throw new InvalidDataException("Invalid default weapon selection.");
                    Weapon(hero.ResourceId, new PlatformWeaponSlot(1, pair[0], pair[1]));
                }
            }
        }

        internal PlatformProfile Create()
        {
            var weapons = new Dictionary<int, IReadOnlyList<PlatformWeaponSlot>>();
            foreach (var hero in _heroes.Values)
            {
                var choices = hero.Defaults.Select(pair => new PlatformWeaponSlot(1, pair[0], pair[1])).ToList();
                if (!choices.Any(item => !Shield(Weapon(hero.ResourceId, item))))
                {
                    var first = hero.Groups.SelectMany((group, g) => group.Select((id, i) => new { id, g, i }))
                        .First(item => item.id != 0 && !Shield(item.id));
                    choices.Insert(0, new PlatformWeaponSlot(1, first.g, first.i));
                }
                weapons.Add(hero.ResourceId, choices.OrderBy(item => Shield(Weapon(hero.ResourceId, item))).Take(4)
                    .Select((item, index) => new PlatformWeaponSlot(index + 1, item.Group, item.Index)).ToArray());
            }
            var profile = new PlatformProfile(0, new[] { 1101 }, weapons);
            Validate(profile);
            return profile;
        }

        internal void Validate(PlatformProfile profile)
        {
            if (profile.Weapons.Count != _heroes.Count || _heroes.Keys.Any(id => !profile.Weapons.ContainsKey(id)))
                throw new ArgumentException("Profile catalogue does not match resources.");
            foreach (var pair in profile.Weapons)
            {
                var weapons = pair.Value.Select(item => Weapon(pair.Key, item)).ToArray();
                if (weapons.Distinct().Count() != weapons.Length || weapons.Count(Shield) > 1)
                    throw new ArgumentException("Duplicate weapon or multiple shields.");
            }
        }
        internal BattleMatchEquipment[] MatchEquipment(int heroId,
            IReadOnlyList<PlatformWeaponSlot> slots, out uint mainWeaponId)
        {
            if (slots == null) throw new ArgumentNullException(nameof(slots));
            var selected = slots.OrderBy(item => item.Slot)
                .Select(item => new BattleMatchEquipment
                {
                    Slot = checked((byte)item.Slot), WeaponId = checked((uint)Weapon(heroId, item))
                }).ToArray();
            mainWeaponId = selected.Select(item => item.WeaponId)
                .FirstOrDefault(id => !Shield(checked((int)id)));
            if (mainWeaponId == 0) mainWeaponId = 100000;
            return selected;
        }
        private int Weapon(int heroId, PlatformWeaponSlot slot)
        {
            if (!_heroes.TryGetValue(heroId, out var hero) || hero.Groups[slot.Group][slot.Index] == 0)
                throw new ArgumentException("Weapon is not available for this hero.");
            return hero.Groups[slot.Group][slot.Index];
        }
        private bool Shield(int weaponId) => _weapons[weaponId].Family == 12;

        [DataContract] private sealed class BundleData
        {
            [DataMember(Name = "schemaVersion", IsRequired = true)] public int SchemaVersion { get; set; }
            [DataMember(Name = "catalogue", IsRequired = true)] public CatalogueData Catalogue { get; set; }
        }
        [DataContract] private sealed class CatalogueData
        {
            [DataMember(Name = "heroes", IsRequired = true)] public HeroData[] Heroes { get; set; }
            [DataMember(Name = "weapons", IsRequired = true)] public WeaponData[] Weapons { get; set; }
        }
        [DataContract] private sealed class HeroData
        {
            [DataMember(Name = "resourceId", IsRequired = true)] public int ResourceId { get; set; }
            [DataMember(Name = "groups", IsRequired = true)] public int[][] Groups { get; set; }
            [DataMember(Name = "defaults", IsRequired = true)] public int[][] Defaults { get; set; }
        }
        [DataContract] private sealed class WeaponData
        {
            [DataMember(Name = "resourceId", IsRequired = true)] public int ResourceId { get; set; }
            [DataMember(Name = "family", IsRequired = true)] public int Family { get; set; }
        }
    }
}
