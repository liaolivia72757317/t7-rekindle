using System;

namespace T7R.Battle
{
    public enum BattleHitRegion : byte { None, Head, Torso, Limb }
    [Flags]
    public enum BattleContactFlags : byte { None = 0, VelocityReady = 1, SourceAirborne = 2, TargetAirborne = 4 }

    public sealed class BattleContactParticipant
    {
        public uint ActorId { get; }
        public ushort ClientRid { get; }
        public ulong Entity { get; }
        public double Heading { get; }
        public BattleContactParticipant(uint actorId, ushort clientRid, ulong entity, double heading)
        {
            if (actorId > BattleLimits.MaxActors || double.IsNaN(heading) || double.IsInfinity(heading) || Math.Abs(heading) > 180)
                throw new ArgumentException("Invalid contact participant.");
            ActorId = actorId; ClientRid = clientRid; Entity = entity; Heading = heading;
        }
    }

    public sealed class BattleContactTiming
    {
        public ushort Sequence { get; }
        public BattleIntent TargetIntent { get; }
        public BattleActionPhase TargetPhase { get; }
        public ulong PhaseAtMs { get; }
        public double TargetPhaseAtMs { get; }
        public double OccurredAtMs { get; }
        public ulong? TargetObservedAtMs { get; }
        public ulong? SourceObservedAtMs { get; }
        public BattleContactTiming(ushort sequence, BattleIntent targetIntent, BattleActionPhase targetPhase,
            ulong phaseAtMs, double targetPhaseAtMs, double occurredAtMs,
            ulong? targetObservedAtMs = null, ulong? sourceObservedAtMs = null)
            : this(sequence, targetIntent, targetPhase, phaseAtMs, targetPhaseAtMs, occurredAtMs,
                  targetObservedAtMs, sourceObservedAtMs, 3) { }

        public BattleContactTiming(ushort sequence, BattleIntent targetIntent, BattleActionPhase targetPhase,
            ulong phaseAtMs, double targetPhaseAtMs, double occurredAtMs,
            ulong? targetObservedAtMs, ulong? sourceObservedAtMs, ushort version)
        {
            if (version < 1 || version > 4 || sequence == 0 || targetIntent > BattleIntent.Dash || targetPhase > BattleActionPhase.Hurt
                || double.IsNaN(occurredAtMs) || double.IsInfinity(occurredAtMs) || occurredAtMs < 0
                || double.IsNaN(targetPhaseAtMs) || double.IsInfinity(targetPhaseAtMs) || version < 4 && targetPhaseAtMs < 0
                || (sourceObservedAtMs.HasValue ? phaseAtMs > sourceObservedAtMs.Value || occurredAtMs > sourceObservedAtMs.Value : phaseAtMs > occurredAtMs)
                || (targetObservedAtMs.HasValue ? occurredAtMs > targetObservedAtMs.Value || targetPhaseAtMs > targetObservedAtMs.Value : targetPhaseAtMs > occurredAtMs))
                throw new ArgumentException("Invalid contact timing.");
            Sequence = sequence; TargetIntent = targetIntent; TargetPhase = targetPhase; PhaseAtMs = phaseAtMs;
            TargetPhaseAtMs = targetPhaseAtMs; OccurredAtMs = occurredAtMs;
            TargetObservedAtMs = targetObservedAtMs; SourceObservedAtMs = sourceObservedAtMs;
        }
    }

    public sealed class BattleContactImpact
    {
        public double Along { get; }
        public uint DefenceWeaponId { get; }
        public BattleHitRegion Region { get; }
        public sbyte BodyIndex { get; }
        public sbyte Material { get; }
        public BattleContactFlags Flags { get; }
        public BattleContactImpact(double along, uint defenceWeaponId, BattleHitRegion region,
            sbyte bodyIndex, sbyte material, BattleContactFlags flags)
        {
            if (double.IsNaN(along) || double.IsInfinity(along) || along < 0 || along > 1
                || region > BattleHitRegion.Limb || bodyIndex < -1 || material < -1 || material > 7
                || (flags & ~(BattleContactFlags.VelocityReady | BattleContactFlags.SourceAirborne | BattleContactFlags.TargetAirborne)) != 0)
                throw new ArgumentException("Invalid contact impact.");
            Along = along; DefenceWeaponId = defenceWeaponId; Region = region;
            BodyIndex = bodyIndex; Material = material; Flags = flags;
        }
    }

    public sealed class BattleContactGeometry
    {
        public BattleVector3 SourcePosition { get; }
        public BattleVector3 TargetPosition { get; }
        public BattleVector3 SourceVelocity { get; }
        public BattleVector3 TargetVelocity { get; }
        public BattleVector3 WeaponVelocity { get; }
        public BattleVector3 Position { get; }
        public BattleVector3 Normal { get; }
        public BattleVector3 Direction { get; }
        public BattleContactGeometry(BattleVector3 sourcePosition, BattleVector3 targetPosition,
            BattleVector3 sourceVelocity, BattleVector3 targetVelocity, BattleVector3 weaponVelocity,
            BattleVector3 position, BattleVector3 normal, BattleVector3 direction)
        {
            if (!normal.IsUnit || !direction.IsUnit) throw new ArgumentException("Invalid contact vector.");
            SourcePosition = sourcePosition; TargetPosition = targetPosition; SourceVelocity = sourceVelocity;
            TargetVelocity = targetVelocity; WeaponVelocity = weaponVelocity; Position = position;
            Normal = normal; Direction = direction;
        }
    }
}
