using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using T7R.Core;

namespace T7R.Battle
{
    public sealed class LocalPlatformBackend : IPlatformBackend
    {
        private readonly PlatformIdentity _identity;
        private readonly LocalProfileCatalogue _catalogue;
        private readonly LocalProfileStore _store;
        private readonly SemaphoreSlim _profileGate = new SemaphoreSlim(1, 1);
        private PlatformProfile _profile;
        private IReadOnlyList<string> _warnings = Array.AsReadOnly(Array.Empty<string>());

        public LocalPlatformBackend(string playerName, byte[] resources, string offlineProfilePath)
        {
            _identity = new PlatformIdentity(playerName, 7, 100);
            _catalogue = new LocalProfileCatalogue(resources);
            _store = new LocalProfileStore(offlineProfilePath);
        }

        public IReadOnlyList<string> ProfileWarnings => Volatile.Read(ref _warnings);

        public Task<PlatformIdentity> ReadIdentityAsync(CancellationToken cancellation)
        {
            cancellation.ThrowIfCancellationRequested();
            return Task.FromResult(_identity);
        }

        public Task<PlatformProfile> ReadProfileAsync(CancellationToken cancellation) => WithProfile(profile => profile, cancellation);

        public Task<PlatformProfileUpdate> SetFormationAsync(IReadOnlyList<int> formation, CancellationToken cancellation)
        {
            var selected = (formation ?? throw new ArgumentNullException(nameof(formation))).ToArray();
            return WithProfile(profile => Commit(profile, new PlatformProfile(profile.Revision, selected, profile.Weapons), cancellation), cancellation);
        }

        public Task<PlatformProfileUpdate> SetWeaponsAsync(int heroId, IReadOnlyList<PlatformWeaponSlot> slots, CancellationToken cancellation)
        {
            var selected = (slots ?? throw new ArgumentNullException(nameof(slots))).ToArray();
            if (selected.Any(slot => slot == null)) throw new ArgumentException("Missing weapon selection.", nameof(slots));
            return WithProfile(profile =>
            {
                var weapons = profile.Weapons.ToDictionary(pair => pair.Key, pair => pair.Value);
                weapons[heroId] = selected.OrderBy(slot => slot.Slot).ToArray();
                return Commit(profile, new PlatformProfile(profile.Revision, profile.Formation, weapons), cancellation);
            }, cancellation);
        }

        private PlatformProfileUpdate Commit(PlatformProfile previous, PlatformProfile candidate, CancellationToken cancellation)
        {
            _catalogue.Validate(candidate);
            if (previous.Formation.SequenceEqual(candidate.Formation) && previous.Weapons.All(pair =>
                pair.Value.Select(SlotKey).SequenceEqual(candidate.Weapons[pair.Key].Select(SlotKey))))
                return new PlatformProfileUpdate(previous, previous);
            candidate = new PlatformProfile(checked(previous.Revision + 1), candidate.Formation, candidate.Weapons);
            cancellation.ThrowIfCancellationRequested();
            _store.Save(candidate, previous);
            _profile = candidate;
            return new PlatformProfileUpdate(previous, candidate);
        }

        private async Task<T> WithProfile<T>(Func<PlatformProfile, T> action, CancellationToken cancellation)
        {
            await _profileGate.WaitAsync(cancellation).ConfigureAwait(false);
            try
            {
                return await Task.Run(() =>
                {
                    cancellation.ThrowIfCancellationRequested();
                    if (_profile == null)
                    {
                        _profile = _store.Load(_catalogue, out var warnings);
                        Volatile.Write(ref _warnings, warnings);
                    }
                    cancellation.ThrowIfCancellationRequested();
                    return action(_profile);
                }, cancellation).ConfigureAwait(false);
            }
            finally { _profileGate.Release(); }
        }

        private static int SlotKey(PlatformWeaponSlot value) => value.Slot * 48 + value.Group * 6 + value.Index;
    }
}
