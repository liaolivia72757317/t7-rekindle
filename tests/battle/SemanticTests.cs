using System;
using System.IO;
using System.Linq;
using T7R.Battle;
using T7R.Core;

namespace T7R.BattleHarness
{
    internal static class SemanticTests
    {
        private static void ContactRegions()
        {
            foreach (var name in new[] { "battle-semantic-v1.txt", "battle-contacts-v2.txt", "battle-contacts-v3.txt", "battle-contacts-v4.txt" })
            {
                var hex = File.ReadAllLines(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "fixtures", name))
                    .Single(line => line.StartsWith("contacts ", StringComparison.Ordinal)).Split(' ')[1];
                var wire = Enumerable.Range(0, hex.Length / 2)
                    .Select(i => Convert.ToByte(hex.Substring(i * 2, 2), 16)).ToArray();
                var original = BattleFrameCodec.DecodeBody(wire.Skip(4).ToArray()).GetPayload();
                foreach (uint target in new uint[] { 0, 2 })
                foreach (uint weapon in new uint[] { 0, 701, 702 })
                foreach (byte region in new byte[] { 0, 1, 2, 3, 4 })
                {
                    var payload = (byte[])original.Clone();
                    Array.Clear(payload, 56, 4); payload[59] = (byte)target;
                    Array.Clear(payload, 62, 2); payload[63] = (byte)target;
                    Array.Clear(payload, 72, 8); payload[79] = (byte)(target == 0 ? 0 : 102);
                    for (int i = 0; i < 4; ++i) payload[116 + i] = (byte)(weapon >> (24 - 8 * i));
                    payload[120] = region;
                    var valid = region <= 3 && (target != 0 || weapon == 0)
                        && (target == 0 || weapon != 0 || region != 0);
                    if (!valid)
                    {
                        Program.Throws<InvalidDataException>(() => BattleSemanticCodec.DecodeContactBatch(payload));
                        continue;
                    }
                    var batch = BattleSemanticCodec.DecodeContactBatch(payload);
                    Program.Check(BattleSemanticCodec.Encode(batch).SequenceEqual(payload), "contact region round trip");
                    var impact = batch.GetEvents()[0].Impact;
                    Program.Check((byte)impact.Region == region && impact.DefenceWeaponId == weapon,
                        "weapon contact must retain its absent body region and actual weapon ID");
                }
            }
        }

        private static void ContactV2()
        {
            foreach (var line in File.ReadAllLines(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "fixtures", "battle-contacts-v2.txt")))
            {
                var parts = line.Split(' ');
                var wire = Enumerable.Range(0, parts[1].Length / 2)
                    .Select(i => Convert.ToByte(parts[1].Substring(i * 2, 2), 16)).ToArray();
                var frame = BattleFrameCodec.DecodeBody(wire.Skip(4).ToArray());
                var payload = frame.GetPayload();
                var batch = BattleSemanticCodec.DecodeContactBatch(payload);
                Program.Check(batch.Version == 2 && BattleSemanticCodec.Encode(batch).SequenceEqual(payload), "contact v2 round trip");
                Program.Check(BattleFrameCodec.Encode(frame).SequenceEqual(wire), "contact v2 frame");
                if (parts[0] == "contacts")
                {
                    var e = batch.GetEvents()[0];
                    Program.Check(e.Timing.OccurredAtMs == 999.5 && e.Timing.TargetPhaseAtMs == 1000.5
                        && e.Timing.TargetObservedAtMs == 1002 && e.Timing.TargetObservedAtMs > batch.Stamp.AtMs, "distinct contact clocks");
                    Program.Throws<ArgumentException>(() => new BattleContactBatch(batch.Stamp, batch.WorldBatchId, 0, batch.GetEvents()));
                    foreach (ulong at in new ulong[] { 0, 999, 1000 })
                    {
                        var bad = (byte[])payload.Clone();
                        for (int i = 0; i < 8; ++i) bad[332 + i] = (byte)(at >> (56 - 8 * i));
                        Program.Throws<InvalidDataException>(() => BattleSemanticCodec.DecodeContactBatch(bad));
                    }
                    var legacy = payload.Take(payload.Length - 8).ToArray(); legacy[1] = 1;
                    Program.Throws<InvalidDataException>(() => BattleSemanticCodec.DecodeContactBatch(legacy));
                }
                for (int length = 0; length < payload.Length; ++length)
                    Program.Throws<InvalidDataException>(() => BattleSemanticCodec.DecodeContactBatch(payload.Take(length).ToArray()));
                var unknown = (byte[])payload.Clone(); unknown[1] = 5;
                Program.Throws<InvalidDataException>(() => BattleSemanticCodec.DecodeContactBatch(unknown));
            }
        }

        private static void ContactV3()
        {
            foreach (var line in File.ReadAllLines(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "fixtures", "battle-contacts-v3.txt")))
            {
                var parts = line.Split(' ');
                var wire = Enumerable.Range(0, parts[1].Length / 2)
                    .Select(i => Convert.ToByte(parts[1].Substring(i * 2, 2), 16)).ToArray();
                var frame = BattleFrameCodec.DecodeBody(wire.Skip(4).ToArray());
                var payload = frame.GetPayload();
                var batch = BattleSemanticCodec.DecodeContactBatch(payload);
                Program.Check(batch.Version == 3 && BattleSemanticCodec.Encode(batch).SequenceEqual(payload), "contact v3 round trip");
                Program.Check(BattleFrameCodec.Encode(frame).SequenceEqual(wire), "contact v3 frame");
                if (parts[0] == "contacts")
                {
                    var e = batch.GetEvents()[0];
                    Program.Check(e.Timing.OccurredAtMs == 999.5 && e.Timing.TargetPhaseAtMs == 1000.5
                        && e.Timing.TargetObservedAtMs == 1002 && e.Timing.SourceObservedAtMs == 1003
                        && e.Timing.PhaseAtMs == 1000 && e.Timing.PhaseAtMs > e.Timing.OccurredAtMs, "distinct contact clocks");
                    Program.Throws<ArgumentException>(() => new BattleContactBatch(batch.Stamp, batch.WorldBatchId, 0, batch.GetEvents()));
                    foreach (ulong at in new ulong[] { 0, 999 })
                    {
                        var bad = (byte[])payload.Clone();
                        for (int i = 0; i < 8; ++i) bad[340 + i] = (byte)(at >> (56 - 8 * i));
                        Program.Throws<InvalidDataException>(() => BattleSemanticCodec.DecodeContactBatch(bad));
                    }
                    var legacy = payload.Take(payload.Length - 8).ToArray(); legacy[1] = 2;
                    Program.Throws<InvalidDataException>(() => BattleSemanticCodec.DecodeContactBatch(legacy));
                }
                for (int length = 0; length < payload.Length; ++length)
                    Program.Throws<InvalidDataException>(() => BattleSemanticCodec.DecodeContactBatch(payload.Take(length).ToArray()));
                var unknown = (byte[])payload.Clone(); unknown[1] = 5;
                Program.Throws<InvalidDataException>(() => BattleSemanticCodec.DecodeContactBatch(unknown));
            }
        }

        internal static void Run()
        {
            ContactPrestartTests.Run();
            HeroHealth();
            ContactRegions();
            var shields = new[] { new BattleShieldState(300, 60) };
            var equipment = new BattleEquipmentState(701, shields);
            shields[0] = new BattleShieldState(301, 0);
            equipment.GetShields()[0] = new BattleShieldState(302, 0);
            Program.Check(equipment.GetShields()[0].WeaponId == 300 && equipment.GetShields()[0].Health == 60,
                "equipment state owns immutable shield pairs");
            Program.Throws<ArgumentException>(() => new BattleEquipmentState(701, new[] { new BattleShieldState(300, 60), new BattleShieldState(300, 50) }));
            Program.Throws<ArgumentException>(() => new BattleVector3(double.NaN, 0, 0));
            foreach (var line in File.ReadAllLines(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "fixtures", "battle-semantic-v1.txt")))
            {
                if (line.Length == 0 || line.StartsWith("#", StringComparison.Ordinal)) continue;
                var parts = line.Split(' ');
                var wire = Enumerable.Range(0, parts[1].Length / 2)
                    .Select(index => Convert.ToByte(parts[1].Substring(index * 2, 2), 16)).ToArray();
                var frame = BattleFrameCodec.DecodeBody(wire.Skip(4).ToArray());
                Program.Check(BattleFrameCodec.Encode(frame).SequenceEqual(wire), "semantic frame " + parts[0]);
                var payload = frame.GetPayload(); byte[] roundTrip; Func<byte[], object> decode;
                if (parts[0] == "world")
                {
                    Program.Check(frame.Kind == BattleMessageKind.WorldSample, "world kind");
                    var sample = BattleSemanticCodec.DecodeWorldSample(payload);
                    Program.Check(sample.Stamp.Epoch == 7 && sample.Stamp.SourceId == 9 && sample.GetActors()[0].ClientRid == 1, "world identity");
                    roundTrip = BattleSemanticCodec.Encode(sample);
                    decode = bytes => BattleSemanticCodec.DecodeWorldSample(bytes);
                }
                else if (parts[0] == "intent")
                {
                    Program.Check(frame.Kind == BattleMessageKind.PlayerIntent, "intent kind");
                    var intent = BattleSemanticCodec.DecodePlayerIntent(payload);
                    Program.Check(intent.ActorId == 1 && intent.Command == 1 && intent.Flags == 5, "intent identity");
                    roundTrip = BattleSemanticCodec.Encode(intent);
                    decode = bytes => BattleSemanticCodec.DecodePlayerIntent(bytes);
                }
                else if (parts[0] == "abort")
                {
                    Program.Check(frame.Kind == BattleMessageKind.MatchAborted, "abort kind");
                    var abort = BattleSemanticCodec.DecodeMatchAborted(payload);
                    Program.Check(abort.Epoch == 7 && abort.AtMs == 1001 && abort.Reason == 1, "abort identity");
                    roundTrip = BattleSemanticCodec.Encode(abort);
                    decode = bytes => BattleSemanticCodec.DecodeMatchAborted(bytes);
                }
                else if (parts[0] == "ended")
                {
                    Program.Check(frame.Kind == BattleMessageKind.MatchEnded, "ended kind");
                    var ended = BattleSemanticCodec.DecodeMatchEnded(payload);
                    Program.Check(ended.Epoch == 7 && ended.EndedAtMs == 1001 && ended.GetMembers()[0].Points == 5, "ended result");
                    roundTrip = BattleSemanticCodec.Encode(ended);
                    decode = bytes => BattleSemanticCodec.DecodeMatchEnded(bytes);
                }
                else if (parts[0] == "motion_false" || parts[0] == "motion_true")
                {
                    Program.Check(frame.Kind == BattleMessageKind.MotionAttempt, "motion kind");
                    var motion = BattleSemanticCodec.DecodeMotionAttempt(payload);
                    Program.Check(motion.ActorId == 1 && (motion.AttemptedForwardAtMs != 0) == (parts[0] == "motion_true"), "motion identity");
                    roundTrip = BattleSemanticCodec.Encode(motion);
                    decode = bytes => BattleSemanticCodec.DecodeMotionAttempt(bytes);
                }
                else if (parts[0] == "contacts" || parts[0] == "watermark")
                {
                    Program.Check(frame.Kind == BattleMessageKind.ContactBatch, "contacts kind");
                    var batch = BattleSemanticCodec.DecodeContactBatch(payload);
                    Program.Check(batch.GetEvents().Length == (parts[0] == "contacts" ? 1 : 0), "contacts count");
                    if (parts[0] == "contacts") Program.Check(batch.GetEvents()[0].Timing.OccurredAtMs == 999.5, "contact time");
                    roundTrip = BattleSemanticCodec.Encode(batch);
                    decode = bytes => BattleSemanticCodec.DecodeContactBatch(bytes);
                }
                else if (parts[0] == "state_snapshot" || parts[0] == "state_delta")
                {
                    Program.Check(frame.Kind == BattleMessageKind.MatchState, "state kind");
                    var state = BattleSemanticCodec.DecodeMatchState(payload);
                    Program.Check(state.Epoch == 7 && state.Revision == (parts[0] == "state_delta" ? 2UL : 1UL), "state revision");
                    Program.Check(state.GetActors().Length == (parts[0] == "state_delta" ? 1 : 2), "state actor count");
                    Program.Check(state.GetActors()[0].Kind == BattleActorKind.Human, "named actor kind");
                    Program.Check(state.GetActors()[0].Equipment.GetShields()[0].WeaponId == 300
                        && state.GetActors()[0].Equipment.GetShields()[0].Health == (parts[0] == "state_delta" ? 50u : 60u), "state shield");
                    if (parts[0] == "state_snapshot")
                        Program.Check(state.GetActors()[1].Life.State == BattleLifeState.Dead && state.GetActors()[1].Life.RespawnAtMs == 5950, "state respawn");
                    roundTrip = BattleSemanticCodec.Encode(state);
                    decode = bytes => BattleSemanticCodec.DecodeMatchState(bytes);
                }
                else throw new InvalidDataException("Unknown semantic vector: " + parts[0]);
                Program.Check(roundTrip.SequenceEqual(payload), "semantic payload " + parts[0]);
                if (parts[0] == "contacts")
                {
                    var dropped = (byte[])payload.Clone(); dropped[47] = 1;
                    Program.Throws<InvalidDataException>(() => BattleSemanticCodec.DecodeContactBatch(dropped));
                }
                if (parts[0] == "motion_true")
                {
                    var futureAttempt = (byte[])payload.Clone(); futureAttempt[98] = 3; futureAttempt[99] = 233;
                    Program.Throws<InvalidDataException>(() => BattleSemanticCodec.DecodeMotionAttempt(futureAttempt));
                }
                if (parts[0] == "state_delta")
                {
                    var gap = (byte[])payload.Clone(); gap[27] = 0;
                    Program.Throws<InvalidDataException>(() => BattleSemanticCodec.DecodeMatchState(gap));
                    var unknownEvent = (byte[])payload.Clone(); unknownEvent[40] = 2;
                    Program.Throws<InvalidDataException>(() => BattleSemanticCodec.DecodeMatchState(unknownEvent));
                }
                var future = (byte[])payload.Clone(); future[1] = 5;
                Program.Throws<InvalidDataException>(() => decode(future));
                for (int length = 0; length < payload.Length; ++length)
                {
                    var truncated = payload.Take(length).ToArray();
                    Program.Throws<InvalidDataException>(() => decode(truncated));
                }
            }
            ContactV2();
            ContactV3();
            var stamp = new BattleSourceStamp(7, 9, 1, 1000);
            var actor = new BattleSampledActor(1, 1, 2, 3, new[] { 1.5, -2.0, 0.0 }, 90, 2, 2);
            var fullRoster = Enumerable.Range(1, 60).Select(id => new BattleSampledActor((uint)id,
                (ushort)id, 1, (ulong)(100 + id), new[] { (double)id, 0.0, 0.0 }, 0, 0, 0)).ToArray();
            var world = BattleSemanticCodec.DecodeWorldSample(BattleSemanticCodec.Encode(new BattleWorldSample(stamp, fullRoster)));
            Program.Check(world.GetActors().Length == 60 && world.GetActors().Last().ActorId == 60,
                "world round trip retains every native roster member");
            var scores = Enumerable.Range(1, 60).Select(id => new BattleResultMember((uint)id,
                (byte)(id <= 30 ? 1 : 2), 3, (uint)id, 0, 0, 0, 0)).ToArray();
            var result = new BattleMatchEnded(7, 1000, 2000, 2, 3, 0, 0, scores);
            var endedResult = BattleSemanticCodec.DecodeMatchEnded(BattleSemanticCodec.Encode(result));
            Program.Check(endedResult.GetMembers().Length == 60 && endedResult.GetMembers().Last().Rank == 60,
                "settlement round trip retains the final native roster rank");
            Program.Throws<ArgumentException>(() => new BattleWorldSample(stamp, new[] { actor, actor }));
            Program.Throws<ArgumentException>(() => new BattlePlayerIntent(stamp, 1, 1, 11, 1));
            Program.Throws<ArgumentException>(() => new BattleMatchAborted(0, 0, 1));
        }

        private static void HeroHealth()
        {
            foreach (var name in new[] { "state_snapshot", "state_delta" })
            {
                var hex = File.ReadAllLines(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "fixtures", "battle-semantic-v1.txt"))
                    .Single(line => line.StartsWith(name + " ", StringComparison.Ordinal)).Split(' ')[1];
                var wire = Enumerable.Range(0, hex.Length / 2)
                    .Select(i => Convert.ToByte(hex.Substring(i * 2, 2), 16)).ToArray();
                var original = BattleFrameCodec.DecodeBody(wire.Skip(4).ToArray()).GetPayload();
                int offset = name == "state_delta" ? 64 : 60;
                foreach (uint health in new uint[] { 0, 99, 150, int.MaxValue, (uint)int.MaxValue + 1, uint.MaxValue })
                {
                    var payload = (byte[])original.Clone();
                    for (int i = 0; i < 4; ++i) payload[offset + i] = (byte)(health >> (24 - 8 * i));
                    if (health > int.MaxValue)
                    {
                        Program.Throws<InvalidDataException>(() => BattleSemanticCodec.DecodeMatchState(payload));
                        continue;
                    }
                    var decoded = BattleSemanticCodec.DecodeMatchState(payload);
                    Program.Check(decoded.GetActors()[0].Health == health, "absolute hero health");
                    Program.Check(BattleSemanticCodec.Encode(decoded).SequenceEqual(payload), "hero health round trip");
                }
            }
        }
    }
}
