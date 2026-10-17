using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading.Tasks;

namespace T7R.Battle
{
    internal sealed class BattleMatchMember
    {
        internal uint ActorId, HeroId, WeaponId;
        internal ushort ClientRid;
        internal byte Camp, Kind;
        internal BattleMatchEquipment[] Equipment = Array.Empty<BattleMatchEquipment>();
    }

    internal struct BattleMatchEquipment
    {
        internal uint WeaponId;
        internal byte Slot;
    }

    internal sealed class BattleMatchConfiguration
    {
        internal ulong Epoch, StartedAtMs, DurationMs, SourceId;
        internal uint KillTarget, Seed;
        internal IReadOnlyList<BattleMatchMember> Members;

        internal BattleNativeConfig ToNative()
        {
            if (Epoch == 0 || SourceId == 0 || DurationMs == 0 || KillTarget == 0 ||
                Members == null || Members.Count < 1 || Members.Count > BattleLimits.MaxActors)
                throw new ArgumentException("Invalid battle configuration.");
            var native = new BattleNativeConfig
            {
                AbiVersion = BattleLimits.ConfigAbiVersion, StructSize = checked((uint)Marshal.SizeOf<BattleNativeConfig>()),
                Epoch = Epoch, StartedAtMs = StartedAtMs, DurationMs = DurationMs,
                KillTarget = KillTarget, Seed = Seed, SourceId = SourceId,
                MemberCount = checked((uint)Members.Count), Members = new BattleNativeMember[BattleLimits.MaxActors]
            };
            var actors = new HashSet<uint>();
            var rids = new HashSet<ushort>();
            for (int index = 0; index < Members.Count; ++index)
            {
                var member = Members[index];
                if (member == null || member.ActorId < 1 || member.ActorId > BattleLimits.MaxActors ||
                    member.ClientRid == 0 || member.Camp < 1 || member.Camp > 2 ||
                    member.Kind < 1 || member.Kind > 2 || member.HeroId == 0 ||
                    member.WeaponId == 0 || member.Equipment == null ||
                    member.Equipment.Length > 4 || !actors.Add(member.ActorId) ||
                    !rids.Add(member.ClientRid))
                    throw new ArgumentException("Invalid battle member binding.");
                var selected = new BattleNativeEquipment[4];
                for (int slot = 0; slot < member.Equipment.Length; ++slot)
                {
                    var value = member.Equipment[slot];
                    if (value.Slot == 0 || value.WeaponId == 0)
                        throw new ArgumentException("Invalid battle equipment.");
                    selected[slot] = new BattleNativeEquipment { WeaponId = value.WeaponId, Slot = value.Slot };
                }
                native.Members[index] = new BattleNativeMember
                {
                    ActorId = member.ActorId, ClientRid = member.ClientRid,
                    Camp = member.Camp, Kind = member.Kind, HeroId = member.HeroId,
                    WeaponId = member.WeaponId, EquipmentCount = (uint)member.Equipment.Length,
                    Equipment = selected
                };
            }
            for (int index = Members.Count; index < native.Members.Length; ++index)
                native.Members[index].Equipment = new BattleNativeEquipment[4];
            return native;
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct BattleNativeEquipment
    {
        internal uint WeaponId;
        internal byte Slot, Reserved0, Reserved1, Reserved2;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct BattleNativeMember
    {
        internal uint ActorId;
        internal ushort ClientRid;
        internal byte Camp, Kind;
        internal uint HeroId, WeaponId, EquipmentCount;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 4)]
        internal BattleNativeEquipment[] Equipment;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct BattleNativeConfig
    {
        internal uint AbiVersion, StructSize;
        internal ulong Epoch, StartedAtMs, DurationMs;
        internal uint KillTarget, Seed;
        internal ulong SourceId;
        internal uint MemberCount, Reserved;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = BattleLimits.MaxActors)]
        internal BattleNativeMember[] Members;
    }

    public sealed partial class ClientGateway
    {
        private volatile bool _battleActive;

        internal ulong NativeHandle => _native.HandleValue;
        internal NativeCombatProfile[] ClientCombatProfiles() => LocalCombatProfiles.FromResources(_native.ResourceBundleJson())
            .Concat(LocalCombatProfiles.FromResources(LocalTrainingMode.Resources)).ToArray();

        internal async Task SelectMapAsync(uint mapId)
        {
            await _lifecycle.WaitAsync().ConfigureAwait(false);
            try
            {
                EnsureOpen();
                if (_battleActive) throw new InvalidOperationException("Map selection requires a stopped battle.");
                await Task.Run(() => _native.SelectMap(mapId)).ConfigureAwait(false);
            }
            finally { _lifecycle.Release(); }
        }

        internal async Task<BattleNativeConfig> StartLocalBattleAsync(ulong epoch, ulong sourceId,
            uint seed, byte playerCamp = 1)
        {
            var profile = await _backend.ReadProfileAsync(System.Threading.CancellationToken.None)
                .ConfigureAwait(false);
            var config = LocalBattleMatch.Create(_native.ResourceBundleJson(), profile,
                epoch, sourceId, seed, playerCamp);
            await StartBattleAsync(config).ConfigureAwait(false);
            return config.ToNative();
        }

        internal async Task StartBattleAsync(BattleMatchConfiguration config)
        {
            if (config == null) throw new ArgumentNullException(nameof(config));
            var native = config.ToNative();
            await _lifecycle.WaitAsync().ConfigureAwait(false);
            try
            {
                EnsureOpen();
                if (_worker == null || _battleActive)
                    throw new InvalidOperationException("Battle requires a running gateway without another match.");
                await Task.Run(() => _native.StartBattle(native)).ConfigureAwait(false);
                _battleActive = true;
            }
            finally { _lifecycle.Release(); }
        }

        internal async Task StopBattleAsync()
        {
            await _lifecycle.WaitAsync().ConfigureAwait(false);
            try
            {
                EnsureOpen();
                var active = _battleActive;
                _battleActive = false;
                if (active) await Task.Run(() => _native.StopBattle()).ConfigureAwait(false);
            }
            finally { _lifecycle.Release(); }
        }

        internal ulong BattleClockMs()
        {
            _lifecycle.Wait();
            try
            {
                EnsureOpen();
                if (!_battleActive) throw new InvalidOperationException("Battle clock requires a running match.");
                return _native.BattleClockMs();
            }
            finally { _lifecycle.Release(); }
        }

        internal bool BattleReturnedToRoom()
        {
            _lifecycle.Wait();
            try
            {
                EnsureOpen();
                if (!_battleActive) throw new InvalidOperationException("Room return requires a running match.");
                return _native.BattleReturnedToRoom();
            }
            finally { _lifecycle.Release(); }
        }

        internal bool BattleStaged()
        {
            _lifecycle.Wait();
            try
            {
                EnsureOpen();
                return _battleActive && _native.BattlePreparationOpen();
            }
            finally { _lifecycle.Release(); }
        }

        internal bool TryReserveBattleRearm()
        {
            _lifecycle.Wait();
            try
            {
                EnsureOpen();
                return _battleActive && _native.TryReserveBattleRearm();
            }
            finally { _lifecycle.Release(); }
        }
    }
}
