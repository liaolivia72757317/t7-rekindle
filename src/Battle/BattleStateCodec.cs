using System;
using System.Collections.Generic;
using System.IO;

namespace T7R.Battle
{
    public sealed class BattleStateActor
    {
        public uint ActorId { get; }
        public BattleCamp Camp { get; }
        public BattleActorKind Kind { get; }
        public uint HeroId { get; }
        public uint Health { get; }
        public double Guard { get; }
        public BattleScore Score { get; }
        public BattleActionState Action { get; }
        public BattleLifeSnapshot Life { get; }
        public BattleEquipmentState Equipment { get; }

        public BattleStateActor(uint actorId, BattleCamp camp, BattleActorKind kind,
            uint heroId, uint health, double guard, BattleScore score,
            BattleActionState action, BattleLifeSnapshot life, BattleEquipmentState equipment)
        {
            if (actorId < 1 || actorId > BattleLimits.MaxActors || camp < BattleCamp.One || camp > BattleCamp.Two
                || kind < BattleActorKind.Human || kind > BattleActorKind.Bot || heroId == 0
                || health > int.MaxValue || double.IsNaN(guard) || double.IsInfinity(guard) || guard < 0)
                throw new ArgumentException("Invalid authoritative actor state.");
            Action = action ?? throw new ArgumentNullException(nameof(action));
            Life = life ?? throw new ArgumentNullException(nameof(life));
            Equipment = equipment ?? throw new ArgumentNullException(nameof(equipment));
            ActorId = actorId; Camp = camp; Kind = kind; HeroId = heroId;
            Health = health; Guard = guard; Score = score;
        }
    }

    public sealed class BattleMatchState
    {
        private readonly BattleStateActor[] _actors;
        public byte Mode { get; }
        public ulong Epoch { get; }
        public ulong Revision { get; }
        public ulong BaseRevision { get; }
        public ulong AtMs { get; }
        public BattleStateActor[] GetActors() => (BattleStateActor[])_actors.Clone();

        public BattleMatchState(byte mode, ulong epoch, ulong revision, ulong baseRevision,
            ulong atMs, BattleStateActor[] actors)
        {
            if (epoch == 0 || revision == 0 || actors == null || actors.Length == 0 || actors.Length > BattleLimits.MaxActors
                || mode != 1 && mode != 2 || mode == 1 && (revision < 2 || baseRevision != revision - 1)
                || mode == 2 && baseRevision != 0)
                throw new ArgumentException("Invalid match state header.");
            var ids = new HashSet<uint>();
            foreach (var actor in actors)
                if (actor == null || !ids.Add(actor.ActorId) || actor.Action.ChangedAtMs > atMs
                    || actor.Life.ChangedAtMs > atMs)
                    throw new ArgumentException("Invalid match state actor.");
            Mode = mode; Epoch = epoch; Revision = revision; BaseRevision = baseRevision;
            AtMs = atMs; _actors = (BattleStateActor[])actors.Clone();
        }
    }

    public static partial class BattleSemanticCodec
    {
        private const int StateActorSize = 152;

        public static byte[] Encode(BattleMatchState value)
        {
            if (value == null) throw new ArgumentNullException(nameof(value));
            var actors = value.GetActors(); int itemSize = value.Mode == 1 ? StateActorSize + 4 : StateActorSize;
            var bytes = new byte[40 + actors.Length * itemSize];
            Put(bytes, 0, Version, 2); bytes[2] = value.Mode;
            Put(bytes, 4, value.Epoch, 8); Put(bytes, 12, value.Revision, 8);
            Put(bytes, 20, value.BaseRevision, 8); Put(bytes, 28, value.AtMs, 8);
            Put(bytes, 36, (ulong)actors.Length, 2);
            for (int index = 0; index < actors.Length; ++index)
            {
                int at = 40 + index * itemSize;
                if (value.Mode == 1) { bytes[at] = 1; at += 4; }
                PutStateActor(bytes, at, actors[index]);
            }
            return bytes;
        }

        public static BattleMatchState DecodeMatchState(byte[] bytes)
        {
            if (bytes == null || bytes.Length < 40 || Read(bytes, 0, 2) != Version
                || bytes[3] != 0 || Read(bytes, 38, 2) != 0)
                throw new InvalidDataException("Invalid match state version or reserved field.");
            int mode = bytes[2], count = (int)Read(bytes, 36, 2);
            int itemSize = mode == 1 ? StateActorSize + 4 : mode == 2 ? StateActorSize : 0;
            if (itemSize == 0 || count < 1 || count > BattleLimits.MaxActors || bytes.Length != 40 + count * itemSize)
                throw new InvalidDataException("Invalid match state length.");
            try
            {
                var actors = new BattleStateActor[count];
                for (int index = 0; index < count; ++index)
                {
                    int at = 40 + index * itemSize;
                    if (mode == 1)
                    {
                        if (bytes[at] != 1 || Read(bytes, at + 1, 3) != 0)
                            throw new InvalidDataException("Invalid state event type.");
                        at += 4;
                    }
                    actors[index] = ReadStateActor(bytes, at);
                }
                return new BattleMatchState((byte)mode, Read(bytes, 4, 8), Read(bytes, 12, 8),
                    Read(bytes, 20, 8), Read(bytes, 28, 8), actors);
            }
            catch (ArgumentException error) { throw new InvalidDataException("Invalid match state.", error); }
        }

        private static void PutStateActor(byte[] bytes, int at, BattleStateActor actor)
        {
            Put(bytes, at, actor.ActorId, 4); bytes[at + 4] = (byte)actor.Camp;
            bytes[at + 5] = (byte)actor.Kind; bytes[at + 6] = (byte)actor.Life.State;
            bytes[at + 7] = (byte)actor.Action.Intent; bytes[at + 8] = (byte)actor.Action.Phase;
            bytes[at + 9] = (byte)actor.Action.Flags; Put(bytes, at + 10, actor.Action.Sequence, 2);
            Put(bytes, at + 12, actor.Action.DurationMs, 2); Put(bytes, at + 16, actor.HeroId, 4);
            Put(bytes, at + 20, actor.Health, 4); PutDouble(bytes, at + 24, actor.Guard);
            Put(bytes, at + 32, actor.Score.Kills, 4); Put(bytes, at + 36, actor.Score.Deaths, 4);
            Put(bytes, at + 40, actor.Score.Points, 4); Put(bytes, at + 44, actor.Score.Streak, 4);
            Put(bytes, at + 48, actor.Action.WeaponId, 4); Put(bytes, at + 52, actor.Action.OffhandId, 4);
            Put(bytes, at + 56, actor.Action.ChangedAtMs, 8); Put(bytes, at + 64, actor.Life.ChangedAtMs, 8);
            Put(bytes, at + 72, actor.Life.RespawnAtMs, 8);
            var position = actor.Life.SpawnPosition;
            for (int i = 0; i < 3; ++i) PutDouble(bytes, at + 80 + i * 8, position[i]);
            PutDouble(bytes, at + 104, actor.Life.SpawnHeading);
            Put(bytes, at + 112, actor.Equipment.MainWeaponId, 4);
            var shields = actor.Equipment.GetShields();
            bytes[at + 116] = (byte)shields.Length;
            for (int i = 0; i < shields.Length; ++i)
            {
                Put(bytes, at + 120 + i * 8, shields[i].WeaponId, 4);
                Put(bytes, at + 124 + i * 8, shields[i].Health, 4);
            }
        }

        private static BattleStateActor ReadStateActor(byte[] bytes, int at)
        {
            if (Read(bytes, at + 14, 2) != 0 || Read(bytes, at + 117, 3) != 0)
                throw new InvalidDataException("Invalid state actor reserved field.");
            int shieldCount = bytes[at + 116];
            if (shieldCount > 4) throw new InvalidDataException("Invalid shield count.");
            var shields = new BattleShieldState[shieldCount];
            for (int i = 0; i < 4; ++i)
            {
                uint id = (uint)Read(bytes, at + 120 + i * 8, 4);
                uint health = (uint)Read(bytes, at + 124 + i * 8, 4);
                if (i < shieldCount) { shields[i] = new BattleShieldState(id, health); }
                else if (id != 0 || health != 0) throw new InvalidDataException("Invalid shield padding.");
            }
            return new BattleStateActor((uint)Read(bytes, at, 4), (BattleCamp)bytes[at + 4], (BattleActorKind)bytes[at + 5],
                (uint)Read(bytes, at + 16, 4), (uint)Read(bytes, at + 20, 4), ReadDouble(bytes, at + 24),
                new BattleScore((uint)Read(bytes, at + 32, 4), (uint)Read(bytes, at + 36, 4),
                    (uint)Read(bytes, at + 40, 4), (uint)Read(bytes, at + 44, 4)),
                new BattleActionState((BattleIntent)bytes[at + 7], (BattleActionPhase)bytes[at + 8], (BattleActionFlags)bytes[at + 9],
                    (ushort)Read(bytes, at + 10, 2), (ushort)Read(bytes, at + 12, 2),
                    (uint)Read(bytes, at + 48, 4), (uint)Read(bytes, at + 52, 4), Read(bytes, at + 56, 8)),
                new BattleLifeSnapshot((BattleLifeState)bytes[at + 6], Read(bytes, at + 64, 8), Read(bytes, at + 72, 8),
                    new BattleVector3(ReadDouble(bytes, at + 80), ReadDouble(bytes, at + 88), ReadDouble(bytes, at + 96)),
                    ReadDouble(bytes, at + 104)),
                new BattleEquipmentState((uint)Read(bytes, at + 112, 4), shields));
        }
    }
}
