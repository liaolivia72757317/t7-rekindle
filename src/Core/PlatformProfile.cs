using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;

namespace T7R.Core
{
    public sealed class PlatformWeaponSlot
    {
        public PlatformWeaponSlot(int slot, int group, int index)
        {
            if (slot < 1 || slot > 4 || group < 0 || group >= 8 || index < 0 || index >= 6)
                throw new ArgumentException("Weapon selection is outside the supported slots.");
            Slot = slot; Group = group; Index = index;
        }
        public int Slot { get; }
        public int Group { get; }
        public int Index { get; }
    }

    // A complete, immutable platform snapshot, independent of client packet layout.
    public sealed class PlatformProfile
    {
        public PlatformProfile(ulong revision, IEnumerable<int> formation,
            IEnumerable<KeyValuePair<int, IReadOnlyList<PlatformWeaponSlot>>> weapons)
        {
            if (revision == ulong.MaxValue) throw new ArgumentOutOfRangeException(nameof(revision));
            var ids = (formation ?? throw new ArgumentNullException(nameof(formation))).ToArray();
            if (ids.Length > 4 || ids.Any(id => id <= 0) || ids.Distinct().Count() != ids.Length)
                throw new ArgumentException("Invalid formation.", nameof(formation));
            var selections = new Dictionary<int, IReadOnlyList<PlatformWeaponSlot>>();
            foreach (var pair in weapons ?? throw new ArgumentNullException(nameof(weapons)))
            {
                var items = pair.Value?.ToArray();
                if (pair.Key <= 0 || selections.Count >= 500 || selections.ContainsKey(pair.Key) || items == null ||
                    items.Length > 4 || items.Any(item => item == null) ||
                    items.Select(item => item.Slot).Distinct().Count() != items.Length ||
                    items.Select(item => item.Group * 6 + item.Index).Distinct().Count() != items.Length)
                    throw new ArgumentException("Invalid weapon configuration.", nameof(weapons));
                selections.Add(pair.Key, Array.AsReadOnly(items));
            }
            if (ids.Any(id => !selections.ContainsKey(id))) throw new ArgumentException("Formation references missing hero equipment.");
            Revision = revision;
            Formation = Array.AsReadOnly(ids);
            Weapons = new ReadOnlyDictionary<int, IReadOnlyList<PlatformWeaponSlot>>(selections);
        }
        public ulong Revision { get; }
        public IReadOnlyList<int> Formation { get; }
        public IReadOnlyDictionary<int, IReadOnlyList<PlatformWeaponSlot>> Weapons { get; }
    }

    public sealed class PlatformProfileUpdate
    {
        public PlatformProfileUpdate(PlatformProfile previous, PlatformProfile profile)
        {
            Previous = previous ?? throw new ArgumentNullException(nameof(previous));
            Profile = profile ?? throw new ArgumentNullException(nameof(profile));
        }
        public PlatformProfile Previous { get; }
        public PlatformProfile Profile { get; }
    }
}
