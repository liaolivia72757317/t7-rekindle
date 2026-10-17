using System;
using System.Collections.Generic;
using System.IO;
using T7R.Core;

namespace T7R.Battle
{
    public sealed class BattleSourceStamp
    {
        public ulong Epoch { get; }
        public ulong SourceId { get; }
        public ulong BatchId { get; }
        public ulong AtMs { get; }
        public BattleSourceStamp(ulong epoch, ulong sourceId, ulong batchId, ulong atMs)
        {
            if (epoch == 0 || sourceId == 0 || batchId == 0) throw new ArgumentException("Invalid battle source stamp.");
            Epoch = epoch; SourceId = sourceId; BatchId = batchId; AtMs = atMs;
        }
    }

    public sealed class BattleSampledActor
    {
        private readonly double[] _position;
        public uint ActorId { get; }
        public ushort ClientRid { get; }
        public ushort Sequence { get; }
        public ulong Entity { get; }
        public double[] GetPosition() => (double[])_position.Clone();
        public double Heading { get; }
        public uint ContactTargetId { get; }
        public byte ContactState { get; }
        public BattleSampledActor(uint actorId, ushort clientRid, ushort sequence, ulong entity,
            double[] position, double heading, uint contactTargetId, byte contactState)
        {
            if (actorId < 1 || actorId > BattleLimits.MaxActors || clientRid == 0 || entity == 0 || position == null || position.Length != 3
                || Array.Exists(position, value => double.IsNaN(value) || double.IsInfinity(value) || Math.Abs(value) >= 100000)
                || double.IsNaN(heading) || double.IsInfinity(heading) || Math.Abs(heading) > 180
                || contactTargetId > BattleLimits.MaxActors || contactState > 2 || (contactState != 0 && contactTargetId == 0))
                throw new ArgumentException("Invalid sampled actor.");
            ActorId = actorId; ClientRid = clientRid; Sequence = sequence; Entity = entity;
            _position = (double[])position.Clone(); Heading = heading;
            ContactTargetId = contactTargetId; ContactState = contactState;
        }
    }

    public sealed class BattleWorldSample
    {
        private readonly BattleSampledActor[] _actors;
        public BattleSourceStamp Stamp { get; }
        public BattleSampledActor[] GetActors() => (BattleSampledActor[])_actors.Clone();
        public BattleWorldSample(BattleSourceStamp stamp, BattleSampledActor[] actors)
        {
            Stamp = stamp ?? throw new ArgumentNullException(nameof(stamp));
            if (actors == null || actors.Length > BattleLimits.MaxActors) throw new ArgumentException("Invalid sampled actor count.", nameof(actors));
            var ids = new HashSet<uint>(); var rids = new HashSet<ushort>();
            foreach (var actor in actors)
                if (actor == null || !ids.Add(actor.ActorId) || !rids.Add(actor.ClientRid))
                    throw new ArgumentException("Duplicate or null sampled actor.", nameof(actors));
            _actors = (BattleSampledActor[])actors.Clone();
        }
    }

    public sealed class BattlePlayerIntent
    {
        public BattleSourceStamp Stamp { get; }
        public uint ActorId { get; }
        public ushort ClientRid { get; }
        public byte Command { get; }
        public byte Flags { get; }
        public BattlePlayerIntent(BattleSourceStamp stamp, uint actorId, ushort clientRid, byte command, byte flags)
        {
            Stamp = stamp ?? throw new ArgumentNullException(nameof(stamp));
            if (actorId < 1 || actorId > BattleLimits.MaxActors || clientRid == 0 || command < 1 || command > 12
                || (flags & ~7) != 0 || (command >= 11 && flags != 0)) throw new ArgumentException("Invalid player intent.");
            ActorId = actorId; ClientRid = clientRid; Command = command; Flags = flags;
        }
    }

    public sealed class BattleMatchAborted
    {
        public ulong Epoch { get; }
        public ulong AtMs { get; }
        public byte Reason { get; }
        public BattleMatchAborted(ulong epoch, ulong atMs, byte reason)
        {
            if (epoch == 0 || reason < 1 || reason > 4) throw new ArgumentException("Invalid match abort.");
            Epoch = epoch; AtMs = atMs; Reason = reason;
        }
    }

    public sealed class BattleResultMember
    {
        public uint ActorId { get; }
        public byte Camp { get; }
        public byte Result { get; }
        public uint Rank { get; }
        public uint Kills { get; }
        public uint Deaths { get; }
        public uint Points { get; }
        public uint Streak { get; }
        public BattleResultMember(uint actorId, byte camp, byte result, uint rank,
            uint kills, uint deaths, uint points, uint streak)
        {
            if (actorId < 1 || actorId > BattleLimits.MaxActors || camp < 1 || camp > 2 || result > 3 || rank == 0)
                throw new ArgumentException("Invalid match result member.");
            ActorId = actorId; Camp = camp; Result = result; Rank = rank;
            Kills = kills; Deaths = deaths; Points = points; Streak = streak;
        }
    }

    public sealed class BattleMatchEnded
    {
        private readonly BattleResultMember[] _members;
        public ulong Epoch { get; }
        public ulong StartedAtMs { get; }
        public ulong EndedAtMs { get; }
        public byte Reason { get; }
        public byte Result { get; }
        public uint CampOneKills { get; }
        public uint CampTwoKills { get; }
        public BattleResultMember[] GetMembers() => (BattleResultMember[])_members.Clone();
        public BattleMatchEnded(ulong epoch, ulong startedAtMs, ulong endedAtMs, byte reason, byte result,
            uint campOneKills, uint campTwoKills, BattleResultMember[] members)
        {
            if (epoch == 0 || endedAtMs < startedAtMs || reason < 1 || reason > 3 || result > 3
                || ((reason == 1) != (result == 0)) || members == null || members.Length < 1 || members.Length > BattleLimits.MaxActors)
                throw new ArgumentException("Invalid match result header.");
            var ids = new HashSet<uint>();
            foreach (var member in members)
                if (member == null || !ids.Add(member.ActorId)) throw new ArgumentException("Invalid match result members.");
            Epoch = epoch; StartedAtMs = startedAtMs; EndedAtMs = endedAtMs;
            Reason = reason; Result = result; CampOneKills = campOneKills; CampTwoKills = campTwoKills;
            _members = (BattleResultMember[])members.Clone();
        }
    }

    public static partial class BattleSemanticCodec
    {
        public const ushort Version = 1;
        public static byte[] Encode(BattleWorldSample sample)
        {
            if (sample == null) throw new ArgumentNullException(nameof(sample));
            var actors = sample.GetActors();
            var bytes = new byte[40 + actors.Length * 56];
            PutStamp(bytes, sample.Stamp); Put(bytes, 36, (ulong)actors.Length, 2);
            for (int index = 0; index < actors.Length; ++index)
            {
                var actor = actors[index]; int at = 40 + index * 56;
                Put(bytes, at, actor.ActorId, 4); Put(bytes, at + 4, actor.ClientRid, 2);
                Put(bytes, at + 6, actor.Sequence, 2); Put(bytes, at + 8, actor.Entity, 8);
                var position = actor.GetPosition();
                for (int i = 0; i < 3; ++i) PutDouble(bytes, at + 16 + i * 8, position[i]);
                PutDouble(bytes, at + 40, actor.Heading); Put(bytes, at + 48, actor.ContactTargetId, 4);
                bytes[at + 52] = actor.ContactState;
            }
            return bytes;
        }

        public static BattleWorldSample DecodeWorldSample(byte[] bytes)
        {
            if (bytes == null || bytes.Length < 40 || Read(bytes, 38, 2) != 0) throw new InvalidDataException("Invalid world sample header.");
            int count = (int)Read(bytes, 36, 2);
            if (count > BattleLimits.MaxActors || bytes.Length != 40 + count * 56) throw new InvalidDataException("Invalid world sample count.");
            try
            {
                var stamp = ReadStamp(bytes); var actors = new BattleSampledActor[count];
                for (int index = 0; index < count; ++index)
                {
                    int at = 40 + index * 56;
                    if (Read(bytes, at + 53, 3) != 0) throw new InvalidDataException("Invalid actor reserved field.");
                    actors[index] = new BattleSampledActor((uint)Read(bytes, at, 4), (ushort)Read(bytes, at + 4, 2),
                        (ushort)Read(bytes, at + 6, 2), Read(bytes, at + 8, 8),
                        new[] { ReadDouble(bytes, at + 16), ReadDouble(bytes, at + 24), ReadDouble(bytes, at + 32) },
                        ReadDouble(bytes, at + 40), (uint)Read(bytes, at + 48, 4), bytes[at + 52]);
                }
                return new BattleWorldSample(stamp, actors);
            }
            catch (ArgumentException error) { throw new InvalidDataException("Invalid world sample.", error); }
        }

        public static byte[] Encode(BattlePlayerIntent intent)
        {
            if (intent == null) throw new ArgumentNullException(nameof(intent));
            var bytes = new byte[44]; PutStamp(bytes, intent.Stamp);
            Put(bytes, 36, intent.ActorId, 4); Put(bytes, 40, intent.ClientRid, 2);
            bytes[42] = intent.Command; bytes[43] = intent.Flags;
            return bytes;
        }

        public static BattlePlayerIntent DecodePlayerIntent(byte[] bytes)
        {
            if (bytes == null || bytes.Length != 44) throw new InvalidDataException("Invalid player intent length.");
            try { return new BattlePlayerIntent(ReadStamp(bytes), (uint)Read(bytes, 36, 4),
                (ushort)Read(bytes, 40, 2), bytes[42], bytes[43]); }
            catch (ArgumentException error) { throw new InvalidDataException("Invalid player intent.", error); }
        }

        public static byte[] Encode(BattleMatchAborted value)
        {
            if (value == null) throw new ArgumentNullException(nameof(value));
            var bytes = new byte[20]; Put(bytes, 0, Version, 2); bytes[2] = value.Reason;
            Put(bytes, 4, value.Epoch, 8); Put(bytes, 12, value.AtMs, 8); return bytes;
        }

        public static BattleMatchAborted DecodeMatchAborted(byte[] bytes)
        {
            if (bytes == null || bytes.Length != 20 || Read(bytes, 0, 2) != Version || bytes[3] != 0)
                throw new InvalidDataException("Invalid match abort version or length.");
            try { return new BattleMatchAborted(Read(bytes, 4, 8), Read(bytes, 12, 8), bytes[2]); }
            catch (ArgumentException error) { throw new InvalidDataException("Invalid match abort.", error); }
        }

        public static byte[] Encode(BattleMatchEnded value)
        {
            if (value == null) throw new ArgumentNullException(nameof(value));
            var members = value.GetMembers(); var bytes = new byte[40 + members.Length * 28];
            Put(bytes, 0, Version, 2); bytes[2] = value.Reason; bytes[3] = value.Result;
            Put(bytes, 4, value.Epoch, 8); Put(bytes, 12, value.StartedAtMs, 8);
            Put(bytes, 20, value.EndedAtMs, 8); Put(bytes, 28, value.CampOneKills, 4);
            Put(bytes, 32, value.CampTwoKills, 4); Put(bytes, 36, (ulong)members.Length, 2);
            for (int index = 0; index < members.Length; ++index)
            {
                var member = members[index]; int at = 40 + index * 28;
                Put(bytes, at, member.ActorId, 4); bytes[at + 4] = member.Camp; bytes[at + 5] = member.Result;
                Put(bytes, at + 8, member.Rank, 4); Put(bytes, at + 12, member.Kills, 4);
                Put(bytes, at + 16, member.Deaths, 4); Put(bytes, at + 20, member.Points, 4);
                Put(bytes, at + 24, member.Streak, 4);
            }
            return bytes;
        }

        public static BattleMatchEnded DecodeMatchEnded(byte[] bytes)
        {
            if (bytes == null || bytes.Length < 40 || Read(bytes, 0, 2) != Version || Read(bytes, 38, 2) != 0)
                throw new InvalidDataException("Invalid match result version or length.");
            int count = (int)Read(bytes, 36, 2);
            if (count < 1 || count > BattleLimits.MaxActors || bytes.Length != 40 + count * 28)
                throw new InvalidDataException("Invalid match result member count.");
            try
            {
                var members = new BattleResultMember[count];
                for (int index = 0; index < count; ++index)
                {
                    int at = 40 + index * 28;
                    if (Read(bytes, at + 6, 2) != 0) throw new InvalidDataException("Invalid result member reserved field.");
                    members[index] = new BattleResultMember((uint)Read(bytes, at, 4), bytes[at + 4], bytes[at + 5],
                        (uint)Read(bytes, at + 8, 4), (uint)Read(bytes, at + 12, 4),
                        (uint)Read(bytes, at + 16, 4), (uint)Read(bytes, at + 20, 4), (uint)Read(bytes, at + 24, 4));
                }
                return new BattleMatchEnded(Read(bytes, 4, 8), Read(bytes, 12, 8), Read(bytes, 20, 8),
                    bytes[2], bytes[3], (uint)Read(bytes, 28, 4), (uint)Read(bytes, 32, 4), members);
            }
            catch (ArgumentException error) { throw new InvalidDataException("Invalid match result.", error); }
        }

        private static void PutStamp(byte[] bytes, BattleSourceStamp stamp)
        {
            Put(bytes, 0, Version, 2); Put(bytes, 4, stamp.Epoch, 8);
            Put(bytes, 12, stamp.SourceId, 8); Put(bytes, 20, stamp.BatchId, 8); Put(bytes, 28, stamp.AtMs, 8);
        }
        private static BattleSourceStamp ReadStamp(byte[] bytes)
        {
            if (Read(bytes, 0, 2) != Version || Read(bytes, 2, 2) != 0)
                throw new InvalidDataException("Invalid semantic version or reserved field.");
            return new BattleSourceStamp(Read(bytes, 4, 8), Read(bytes, 12, 8), Read(bytes, 20, 8), Read(bytes, 28, 8));
        }
        private static ulong Read(byte[] bytes, int at, int size)
        {
            ulong value = 0; for (int i = 0; i < size; ++i) value = (value << 8) | bytes[at + i]; return value;
        }
        private static void Put(byte[] bytes, int at, ulong value, int size)
        {
            for (int i = size - 1; i >= 0; --i) { bytes[at + i] = (byte)value; value >>= 8; }
        }
        private static void PutDouble(byte[] bytes, int at, double value) => Put(bytes, at, unchecked((ulong)BitConverter.DoubleToInt64Bits(value)), 8);
        private static double ReadDouble(byte[] bytes, int at) => BitConverter.Int64BitsToDouble(unchecked((long)Read(bytes, at, 8)));
    }
}
