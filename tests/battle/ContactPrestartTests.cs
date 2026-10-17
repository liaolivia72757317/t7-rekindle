using System;
using System.IO;
using System.Linq;
using T7R.Battle;

namespace T7R.BattleHarness
{
    internal static class ContactPrestartTests
    {
        internal static void Run()
        {
            foreach (var line in File.ReadAllLines(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "fixtures", "battle-contacts-v4.txt")))
            {
                var parts = line.Split(' ');
                var wire = Enumerable.Range(0, parts[1].Length / 2)
                    .Select(i => Convert.ToByte(parts[1].Substring(i * 2, 2), 16)).ToArray();
                var frame = BattleFrameCodec.DecodeBody(wire.Skip(4).ToArray());
                var bytes = frame.GetPayload();
                var batch = BattleSemanticCodec.DecodeContactBatch(bytes);
                Program.Check(batch.Version == 4 && BattleSemanticCodec.Encode(batch).SequenceEqual(bytes), "contact v4 round trip");
                Program.Check(BattleFrameCodec.Encode(frame).SequenceEqual(wire), "contact v4 frame");
                if (parts[0] == "contacts") CheckContact(bytes, batch);
                for (int length = 0; length < bytes.Length; ++length)
                    Program.Throws<InvalidDataException>(() => BattleSemanticCodec.DecodeContactBatch(bytes.Take(length).ToArray()));
                var unknown = (byte[])bytes.Clone(); unknown[1] = 6;
                Program.Throws<InvalidDataException>(() => BattleSemanticCodec.DecodeContactBatch(unknown));
            }
        }

        private static void CheckContact(byte[] bytes, BattleContactBatch batch)
        {
            var timing = batch.GetEvents()[0].Timing;
            Program.Check(timing.TargetPhaseAtMs == -4999 && timing.PhaseAtMs == 1000
                && timing.OccurredAtMs == 999.5 && timing.TargetObservedAtMs == 1002
                && timing.SourceObservedAtMs == 1003 && bytes.Length == 52 + 296, "prestart phase is not clamped");
            Program.Throws<ArgumentException>(() => new BattleContactTiming(timing.Sequence,
                timing.TargetIntent, timing.TargetPhase, timing.PhaseAtMs, -4999, timing.OccurredAtMs,
                timing.TargetObservedAtMs, timing.SourceObservedAtMs));
            Program.Throws<ArgumentException>(() => new BattleContactBatch(batch.Stamp, batch.WorldBatchId, 0, batch.GetEvents(), 3));
            for (ushort version = 1; version <= 3; ++version)
            {
                var legacy = bytes.Take(52 + 280 + (version - 1) * 8).ToArray();
                legacy[1] = (byte)version;
                Array.Copy(new byte[] { 0, 0, 0, 0, 0, 0, 3, 222 }, 0, legacy, 84, 8); // Source phase 990.
                Program.Throws<InvalidDataException>(() => BattleSemanticCodec.DecodeContactBatch(legacy));
                PutDouble(legacy, 92, 0);
                Program.Check(BattleSemanticCodec.DecodeContactBatch(legacy).Version == version, "legacy positive phase remains valid");
            }
            foreach (double at in new[] { double.NaN, double.PositiveInfinity, double.NegativeInfinity, 1003 })
            {
                var bad = (byte[])bytes.Clone(); PutDouble(bad, 92, at);
                Program.Throws<InvalidDataException>(() => BattleSemanticCodec.DecodeContactBatch(bad));
            }
            foreach (double at in new[] { -0.25, double.NaN, double.PositiveInfinity, 1001 })
            {
                var bad = (byte[])bytes.Clone(); PutDouble(bad, 100, at);
                Program.Throws<InvalidDataException>(() => BattleSemanticCodec.DecodeContactBatch(bad));
            }
            foreach (int offset in new[] { 332, 340 })
            {
                var bad = (byte[])bytes.Clone(); Array.Clear(bad, offset, 8);
                Program.Throws<InvalidDataException>(() => BattleSemanticCodec.DecodeContactBatch(bad));
            }
        }

        private static void PutDouble(byte[] bytes, int offset, double value)
        {
            var number = BitConverter.GetBytes(value);
            if (BitConverter.IsLittleEndian) Array.Reverse(number);
            Array.Copy(number, 0, bytes, offset, number.Length);
        }
    }
}
