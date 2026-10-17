using System;
using System.IO;
using System.Linq;
using System.Text;
using T7R.Battle;
using T7R.Core;

namespace T7R.BattleHarness
{
    internal static class AdmissionTests
    {
        internal static void Run()
        {
            var required = new BattleRequirements(BattleAuthorityMode.ClientSampled, 3, 7, 1028);
            var capabilities = BattleCapabilities.ClientSampled(3, 7, 1028);
            Program.Check(capabilities.Supports(required), "matching battle capability");
            Program.Check(!capabilities.Supports(new BattleRequirements(BattleAuthorityMode.ServerSimulated, 3, 7, 1028)), "no authority downgrade");
            Program.Check(!capabilities.Supports(new BattleRequirements(BattleAuthorityMode.ClientSampled, 4, 7, 1028)), "rules mismatch");
            Program.Check(!capabilities.Supports(new BattleRequirements(BattleAuthorityMode.ClientSampled, 3, 8, 1028)), "resources mismatch");
            Program.Check(!capabilities.Supports(new BattleRequirements(BattleAuthorityMode.ClientSampled, 3, 7, 1029)), "map mismatch");
            Program.Throws<ArgumentOutOfRangeException>(() => new BattleRequirements((BattleAuthorityMode)0, 3, 7, 1028));
            Program.Throws<ArgumentOutOfRangeException>(() => BattleCapabilities.ClientSampled(0, 7, 1028));
            foreach (var line in File.ReadAllLines(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "fixtures", "battle-v1.txt")))
            {
                if (line.Length == 0 || line.StartsWith("#", StringComparison.Ordinal)) continue;
                var parts = line.Split(' ');
                var wire = Enumerable.Range(0, parts[1].Length / 2).Select(i => Convert.ToByte(parts[1].Substring(i * 2, 2), 16)).ToArray();
                var frame = BattleFrameCodec.DecodeBody(wire.Skip(4).ToArray());
                Program.Check(BattleFrameCodec.Encode(frame).SequenceEqual(wire), "shared battle vector " + parts[0]);
                if (parts[0] != "admit") continue;
                var payload = frame.GetPayload();
                var admission = BattleAdmissionCodec.Decode(payload);
                Program.Check(capabilities.Supports(admission.Requirements), "shared admission requirements");
                Program.Check(Encoding.ASCII.GetString(admission.GetTicket()) == "fixture-ticket", "opaque ticket bytes");
                Program.Check(BattleAdmissionCodec.Encode(admission).SequenceEqual(payload), "admission round trip");
                for (int length = 0; length < payload.Length; ++length)
                    Program.Throws<InvalidDataException>(() => BattleAdmissionCodec.Decode(payload.Take(length).ToArray()));
                Program.Throws<InvalidDataException>(() => BattleAdmissionCodec.Decode(payload.Concat(new byte[1]).ToArray()));
                foreach (int index in new[] { 0, 1, 2, 3 })
                {
                    var invalid = (byte[])payload.Clone(); invalid[index] = 255;
                    Program.Throws<InvalidDataException>(() => BattleAdmissionCodec.Decode(invalid));
                }
            }
            var ticket = new byte[] { 0, 255, 1 };
            var value = new BattleAdmission(required, ticket); ticket[0] = 9;
            var copy = value.GetTicket(); copy[0] = 8;
            Program.Check(value.GetTicket()[0] == 0, "ticket ownership");
            Program.Throws<ArgumentException>(() => new BattleAdmission(required, new byte[0]));
            Program.Throws<ArgumentException>(() => new BattleAdmission(required, new byte[257]));
        }
    }
}
