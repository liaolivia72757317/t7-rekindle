using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;

namespace T7R.Core
{
    // Platform identity is distinct from the original client's compatibility user ID.
    // Implementations must honor cancellation; account/mode policy belongs to the caller.
    public interface IPlatformBackend
    {
        Task<PlatformIdentity> ReadIdentityAsync(CancellationToken cancellation);
        Task<PlatformProfile> ReadProfileAsync(CancellationToken cancellation);
        Task<PlatformProfileUpdate> SetFormationAsync(IReadOnlyList<int> formation, CancellationToken cancellation);
        Task<PlatformProfileUpdate> SetWeaponsAsync(int heroId, IReadOnlyList<PlatformWeaponSlot> slots, CancellationToken cancellation);
    }

    public sealed class PlatformIdentity
    {
        public PlatformIdentity(string displayName, int portrait, int level)
        {
            var error = PlayerNameRules.Validate(displayName);
            if (error.Length != 0) throw new ArgumentException(error, nameof(displayName));
            if (portrait < 0) throw new ArgumentOutOfRangeException(nameof(portrait));
            if (level < 1) throw new ArgumentOutOfRangeException(nameof(level));
            DisplayName = displayName.Trim(); Portrait = portrait; Level = level;
        }
        public string DisplayName { get; }
        public int Portrait { get; }
        public int Level { get; }
    }
}
