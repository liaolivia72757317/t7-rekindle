using System;
using System.IO;

namespace T7R.Battle
{
    public sealed class BattleMotionAttempt
    {
        private readonly double[] _position;
        public BattleSourceStamp Stamp { get; }
        public ulong WorldBatchId { get; }
        public uint ActorId { get; }
        public ushort ClientRid { get; }
        public ulong Entity { get; }
        public double[] GetPosition() => (double[])_position.Clone();
        public double Heading { get; }
        public ulong AttemptedForwardAtMs { get; }

        public BattleMotionAttempt(BattleSourceStamp stamp, ulong worldBatchId, uint actorId,
            ushort clientRid, ulong entity, double[] position, double heading, ulong attemptedForwardAtMs)
        {
            if (stamp == null || worldBatchId == 0 || actorId < 1 || actorId > BattleLimits.MaxActors || clientRid == 0
                || entity == 0 || !BattleSemanticCodec.ValidPoint(position)
                || double.IsNaN(heading) || double.IsInfinity(heading) || Math.Abs(heading) > 180
                || attemptedForwardAtMs > stamp.AtMs
                || attemptedForwardAtMs != 0 && stamp.AtMs - attemptedForwardAtMs > 250)
                throw new ArgumentException("Invalid motion attempt.");
            Stamp = stamp; WorldBatchId = worldBatchId; ActorId = actorId; ClientRid = clientRid;
            Entity = entity; _position = (double[])position.Clone(); Heading = heading;
            AttemptedForwardAtMs = attemptedForwardAtMs;
        }
    }

    public sealed class BattleSampledContact
    {
        public BattleContactParticipant Source { get; }
        public BattleContactParticipant Target { get; }
        public BattleContactTiming Timing { get; }
        public BattleContactImpact Impact { get; }
        public BattleContactGeometry Geometry { get; }

        public BattleSampledContact(BattleContactParticipant source, BattleContactParticipant target,
            BattleContactTiming timing, BattleContactImpact impact, BattleContactGeometry geometry)
        {
            Source = source ?? throw new ArgumentNullException(nameof(source));
            Target = target ?? throw new ArgumentNullException(nameof(target));
            Timing = timing ?? throw new ArgumentNullException(nameof(timing));
            Impact = impact ?? throw new ArgumentNullException(nameof(impact));
            Geometry = geometry ?? throw new ArgumentNullException(nameof(geometry));
            if (source.ActorId == 0 || source.ClientRid == 0 || source.Entity == 0 || source.ActorId == target.ActorId
                || (target.ActorId == 0) != (target.ClientRid == 0) || target.ActorId != 0 && target.Entity == 0
                || target.ActorId != 0 && impact.DefenceWeaponId == 0 && impact.Region == BattleHitRegion.None
                || target.ActorId == 0 && impact.DefenceWeaponId != 0)
                throw new ArgumentException("Invalid contact evidence.");
        }
    }

    public sealed class BattleContactBatch
    {
        public ushort Version { get; }
        private readonly BattleSampledContact[] _events;
        public BattleSourceStamp Stamp { get; }
        public ulong WorldBatchId { get; }
        public uint Dropped { get; }
        public BattleSampledContact[] GetEvents() => (BattleSampledContact[])_events.Clone();

        public BattleContactBatch(BattleSourceStamp stamp, ulong worldBatchId, uint dropped,
            BattleSampledContact[] events) : this(stamp, worldBatchId, dropped, events, 1) { }

        public BattleContactBatch(BattleSourceStamp stamp, ulong worldBatchId, uint dropped,
            BattleSampledContact[] events, ushort version)
        {
            if ((version < 1 || version > 4) || stamp == null || worldBatchId == 0
                || dropped != 0 || events == null || events.Length > 8192)
                throw new ArgumentException("Invalid or incomplete contact batch.");
            foreach (var value in events)
                if (value == null || value.Timing.OccurredAtMs > stamp.AtMs
                    || version < 4 && value.Timing.TargetPhaseAtMs < 0
                    || value.Timing.TargetObservedAtMs.HasValue != (version >= 2)
                    || value.Timing.SourceObservedAtMs.HasValue != (version >= 3))
                    throw new ArgumentException("Contact exceeds batch watermark.");
            Stamp = stamp; WorldBatchId = worldBatchId; Dropped = dropped;
            Version = version;
            _events = (BattleSampledContact[])events.Clone();
        }
    }

    public static partial class BattleSemanticCodec
    {
        internal static bool ValidPoint(double[] point)
        {
            if (point == null || point.Length != 3) return false;
            foreach (var value in point)
                if (double.IsNaN(value) || double.IsInfinity(value) || Math.Abs(value) >= 100000) return false;
            return true;
        }

        public static byte[] Encode(BattleMotionAttempt value)
        {
            if (value == null) throw new ArgumentNullException(nameof(value));
            var bytes = new byte[100]; PutStamp(bytes, value.Stamp);
            Put(bytes, 36, value.WorldBatchId, 8); Put(bytes, 44, value.ActorId, 4);
            Put(bytes, 48, value.ClientRid, 2); Put(bytes, 52, value.Entity, 8);
            var position = value.GetPosition();
            for (int i = 0; i < 3; ++i) PutDouble(bytes, 60 + i * 8, position[i]);
            PutDouble(bytes, 84, value.Heading); Put(bytes, 92, value.AttemptedForwardAtMs, 8);
            return bytes;
        }

        public static BattleMotionAttempt DecodeMotionAttempt(byte[] bytes)
        {
            if (bytes == null || bytes.Length != 100 || Read(bytes, 50, 2) != 0)
                throw new InvalidDataException("Invalid motion attempt length or reserved field.");
            try { return new BattleMotionAttempt(ReadStamp(bytes), Read(bytes, 36, 8),
                (uint)Read(bytes, 44, 4), (ushort)Read(bytes, 48, 2), Read(bytes, 52, 8),
                new[] { ReadDouble(bytes, 60), ReadDouble(bytes, 68), ReadDouble(bytes, 76) },
                ReadDouble(bytes, 84), Read(bytes, 92, 8)); }
            catch (ArgumentException error) { throw new InvalidDataException("Invalid motion attempt.", error); }
        }

        public static byte[] Encode(BattleContactBatch value)
        {
            if (value == null) throw new ArgumentNullException(nameof(value));
            int size = 280 + (Math.Min(value.Version, (ushort)3) - 1) * 8;
            var events = value.GetEvents(); var bytes = new byte[52 + events.Length * size];
            PutStamp(bytes, value.Stamp); Put(bytes, 36, value.WorldBatchId, 8);
            Put(bytes, 0, value.Version, 2);
            Put(bytes, 44, value.Dropped, 4); Put(bytes, 48, (ulong)events.Length, 2);
            for (int i = 0; i < events.Length; ++i)
            {
                var e = events[i]; int at = 52 + i * size;
                Put(bytes, at, e.Source.ActorId, 4); Put(bytes, at + 4, e.Target.ActorId, 4);
                Put(bytes, at + 8, e.Source.ClientRid, 2); Put(bytes, at + 10, e.Target.ClientRid, 2);
                Put(bytes, at + 12, e.Source.Entity, 8); Put(bytes, at + 20, e.Target.Entity, 8);
                Put(bytes, at + 28, e.Timing.Sequence, 2); bytes[at + 30] = (byte)e.Timing.TargetIntent;
                bytes[at + 31] = (byte)e.Timing.TargetPhase; Put(bytes, at + 32, e.Timing.PhaseAtMs, 8);
                PutDouble(bytes, at + 40, e.Timing.TargetPhaseAtMs); PutDouble(bytes, at + 48, e.Timing.OccurredAtMs);
                PutDouble(bytes, at + 56, e.Impact.Along); Put(bytes, at + 64, e.Impact.DefenceWeaponId, 4);
                bytes[at + 68] = (byte)e.Impact.Region; bytes[at + 69] = unchecked((byte)e.Impact.BodyIndex);
                bytes[at + 70] = unchecked((byte)e.Impact.Material); bytes[at + 71] = (byte)e.Impact.Flags;
                var points = new[] { e.Geometry.SourcePosition, e.Geometry.TargetPosition, e.Geometry.SourceVelocity,
                    e.Geometry.TargetVelocity, e.Geometry.WeaponVelocity, e.Geometry.Position, e.Geometry.Normal, e.Geometry.Direction };
                for (int p = 0; p < 8; ++p)
                    for (int axis = 0; axis < 3; ++axis)
                        PutDouble(bytes, at + 72 + p * 24 + axis * 8, points[p][axis]);
                PutDouble(bytes, at + 264, e.Source.Heading); PutDouble(bytes, at + 272, e.Target.Heading);
                if (value.Version >= 2) Put(bytes, at + 280, e.Timing.TargetObservedAtMs.Value, 8);
                if (value.Version >= 3) Put(bytes, at + 288, e.Timing.SourceObservedAtMs.Value, 8);
            }
            return bytes;
        }

        public static BattleContactBatch DecodeContactBatch(byte[] bytes)
        {
            if (bytes == null || bytes.Length < 52 || Read(bytes, 50, 2) != 0)
                throw new InvalidDataException("Invalid contact batch header.");
            int count = (int)Read(bytes, 48, 2);
            ushort version = (ushort)Read(bytes, 0, 2);
            if ((version < 1 || version > 4) || Read(bytes, 2, 2) != 0)
                throw new InvalidDataException("Invalid contact version or reserved field.");
            int size = 280 + (Math.Min(version, (ushort)3) - 1) * 8;
            if (count > 8192 || bytes.Length != 52 + count * size)
                throw new InvalidDataException("Invalid contact batch length.");
            try
            {
                var stamp = new BattleSourceStamp(Read(bytes, 4, 8), Read(bytes, 12, 8),
                    Read(bytes, 20, 8), Read(bytes, 28, 8));
                var events = new BattleSampledContact[count];
                for (int i = 0; i < count; ++i)
                {
                    int at = 52 + i * size;
                    var points = new BattleVector3[8];
                    for (int p = 0; p < 8; ++p)
                        points[p] = new BattleVector3(ReadDouble(bytes, at + 72 + p * 24),
                            ReadDouble(bytes, at + 80 + p * 24), ReadDouble(bytes, at + 88 + p * 24));
                    events[i] = new BattleSampledContact(
                        new BattleContactParticipant((uint)Read(bytes, at, 4), (ushort)Read(bytes, at + 8, 2),
                            Read(bytes, at + 12, 8), ReadDouble(bytes, at + 264)),
                        new BattleContactParticipant((uint)Read(bytes, at + 4, 4), (ushort)Read(bytes, at + 10, 2),
                            Read(bytes, at + 20, 8), ReadDouble(bytes, at + 272)),
                        new BattleContactTiming((ushort)Read(bytes, at + 28, 2), (BattleIntent)bytes[at + 30],
                            (BattleActionPhase)bytes[at + 31], Read(bytes, at + 32, 8), ReadDouble(bytes, at + 40), ReadDouble(bytes, at + 48),
                            version >= 2 ? (ulong?)Read(bytes, at + 280, 8) : null,
                            version >= 3 ? (ulong?)Read(bytes, at + 288, 8) : null, version),
                        new BattleContactImpact(ReadDouble(bytes, at + 56), (uint)Read(bytes, at + 64, 4),
                            (BattleHitRegion)bytes[at + 68], unchecked((sbyte)bytes[at + 69]), unchecked((sbyte)bytes[at + 70]), (BattleContactFlags)bytes[at + 71]),
                        new BattleContactGeometry(points[0], points[1], points[2], points[3], points[4], points[5], points[6], points[7]));
                }
                return new BattleContactBatch(stamp, Read(bytes, 36, 8), (uint)Read(bytes, 44, 4), events, version);
            }
            catch (ArgumentException error) { throw new InvalidDataException("Invalid contact batch.", error); }
        }
    }
}
