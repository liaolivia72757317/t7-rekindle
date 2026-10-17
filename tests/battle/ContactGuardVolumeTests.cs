using System;
using System.IO;
using System.Linq;
using T7R.Battle;

namespace T7R.BattleHarness
{
    internal static class ContactGuardVolumeTests
    {
        internal static void Run()
        {
            foreach (var line in File.ReadAllLines(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "fixtures", "battle-contacts-v5.txt")))
            {
                var parts = line.Split(' ');
                var wire = Enumerable.Range(0, parts[1].Length / 2)
                    .Select(i => Convert.ToByte(parts[1].Substring(i * 2, 2), 16)).ToArray();
                var bytes = BattleFrameCodec.DecodeBody(wire.Skip(4).ToArray()).GetPayload();
                var batch = BattleSemanticCodec.DecodeContactBatch(bytes);
                Program.Check(batch.Version == 5 && BattleSemanticCodec.Encode(batch).SequenceEqual(bytes), "v5 byte-exact round trip");
                if (parts[0] != "contacts") continue;
                var impact = batch.GetEvents()[0].Impact;
                Program.Check((impact.Flags & BattleContactFlags.GuardVolume) != 0 && impact.DefenceWeaponId == 701
                    && (byte)impact.Region == 0 && impact.BodyIndex == -1
                    && batch.GetEvents()[0].Timing.TargetPhaseAtMs == -4999, "v5 provenance and prestart clock");
                foreach (var change in new[] { new[] { 1, 4 }, new[] { 119, 0 }, new[] { 120, 2 },
                    new[] { 121, 0 }, new[] { 82, 9 }, new[] { 123, 16 } })
                {
                    var bad = (byte[])bytes.Clone(); bad[change[0]] = (byte)change[1];
                    if (change[0] == 119) Array.Clear(bad, 116, 4);
                    Program.Throws<InvalidDataException>(() => BattleSemanticCodec.DecodeContactBatch(bad));
                }
            }
        }
    }
}
