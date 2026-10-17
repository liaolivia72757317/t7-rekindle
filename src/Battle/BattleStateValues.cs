using System;

namespace T7R.Battle
{
    public enum BattleCamp : byte { One = 1, Two = 2 }
    public enum BattleActorKind : byte { Human = 1, Bot = 2 }
    public enum BattleLifeState : byte { Alive = 1, Dead = 2, Selecting = 3, Waiting = 4, Spawning = 5 }
    public enum BattleIntent : byte
    {
        None, AttackUp, AttackRight, AttackDown, AttackLeft,
        GuardUp, GuardRight, GuardDown, GuardLeft, Shield, Dash
    }
    public enum BattleActionPhase : byte { Idle, Prepare, Hold, Strike, Recovery, WeaponChange, Parried, Terrain, ParrySuccess, Hurt }
    [Flags]
    public enum BattleActionFlags : byte { None = 0, Released = 1 }

    public readonly struct BattleVector3
    {
        public double X { get; }
        public double Y { get; }
        public double Z { get; }
        public BattleVector3(double x, double y, double z)
        {
            if (!Valid(x) || !Valid(y) || !Valid(z)) throw new ArgumentException("Invalid battle vector.");
            X = x; Y = y; Z = z;
        }
        internal double this[int axis] => axis == 0 ? X : axis == 1 ? Y : axis == 2 ? Z : throw new ArgumentOutOfRangeException(nameof(axis));
        internal bool IsUnit
        {
            get
            {
                var length = Math.Sqrt(X * X + Y * Y + Z * Z);
                return length >= 0.9 && length <= 1.1;
            }
        }
        private static bool Valid(double value) => !double.IsNaN(value) && !double.IsInfinity(value) && Math.Abs(value) < 100000;
    }

    public readonly struct BattleScore
    {
        public uint Kills { get; }
        public uint Deaths { get; }
        public uint Points { get; }
        public uint Streak { get; }
        public BattleScore(uint kills, uint deaths, uint points, uint streak)
        { Kills = kills; Deaths = deaths; Points = points; Streak = streak; }
    }

    public readonly struct BattleShieldState
    {
        public uint WeaponId { get; }
        public uint Health { get; }
        public BattleShieldState(uint weaponId, uint health)
        {
            if (weaponId == 0) throw new ArgumentException("Missing shield weapon.");
            WeaponId = weaponId; Health = health;
        }
    }

    public sealed class BattleEquipmentState
    {
        private readonly BattleShieldState[] _shields;
        public uint MainWeaponId { get; }
        public BattleShieldState[] GetShields() => (BattleShieldState[])_shields.Clone();
        public BattleEquipmentState(uint mainWeaponId, BattleShieldState[] shields)
        {
            if (mainWeaponId == 0 || shields == null || shields.Length > 4) throw new ArgumentException("Invalid equipment state.");
            for (int i = 0; i < shields.Length; ++i)
                if (shields[i].WeaponId == 0 || i > 0 && shields[i - 1].WeaponId >= shields[i].WeaponId)
                    throw new ArgumentException("Invalid shield order.");
            MainWeaponId = mainWeaponId; _shields = (BattleShieldState[])shields.Clone();
        }
    }

    public sealed class BattleActionState
    {
        public BattleIntent Intent { get; }
        public BattleActionPhase Phase { get; }
        public BattleActionFlags Flags { get; }
        public ushort Sequence { get; }
        public ushort DurationMs { get; }
        public uint WeaponId { get; }
        public uint OffhandId { get; }
        public ulong ChangedAtMs { get; }
        public BattleActionState(BattleIntent intent, BattleActionPhase phase, BattleActionFlags flags,
            ushort sequence, ushort durationMs, uint weaponId, uint offhandId, ulong changedAtMs)
        {
            if (intent > BattleIntent.Dash || phase > BattleActionPhase.Hurt || (flags & ~BattleActionFlags.Released) != 0
                || sequence == 0 || weaponId == 0) throw new ArgumentException("Invalid actor action state.");
            Intent = intent; Phase = phase; Flags = flags; Sequence = sequence; DurationMs = durationMs;
            WeaponId = weaponId; OffhandId = offhandId; ChangedAtMs = changedAtMs;
        }
    }

    public sealed class BattleLifeSnapshot
    {
        public BattleLifeState State { get; }
        public ulong ChangedAtMs { get; }
        public ulong RespawnAtMs { get; }
        public BattleVector3 SpawnPosition { get; }
        public double SpawnHeading { get; }
        public BattleLifeSnapshot(BattleLifeState state, ulong changedAtMs, ulong respawnAtMs,
            BattleVector3 spawnPosition, double spawnHeading)
        {
            if (state < BattleLifeState.Alive || state > BattleLifeState.Spawning
                || (state == BattleLifeState.Dead) != (respawnAtMs != 0)
                || state == BattleLifeState.Dead && respawnAtMs < changedAtMs
                || state == BattleLifeState.Alive && changedAtMs != 0
                || double.IsNaN(spawnHeading) || double.IsInfinity(spawnHeading) || Math.Abs(spawnHeading) > 180
                || state != BattleLifeState.Spawning && (spawnPosition.X != 0 || spawnPosition.Y != 0 || spawnPosition.Z != 0 || spawnHeading != 0))
                throw new ArgumentException("Invalid actor life state.");
            State = state; ChangedAtMs = changedAtMs; RespawnAtMs = respawnAtMs;
            SpawnPosition = spawnPosition; SpawnHeading = spawnHeading;
        }
    }
}
