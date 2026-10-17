using System;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using T7R.Battle;

namespace T7R.BattleHarness
{
    internal static class LocalLobbyMatchTests
    {
        internal static async Task Run(string library)
        {
            var directory = Path.Combine(Path.GetTempPath(), "t7r-empty-lobby-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            try
            {
                var path = Path.Combine(directory, "profile.json");
                var backend = new LocalPlatformBackend("测试玩家", LocalProfileTests.Resources, path);
                await backend.SetFormationAsync(Array.Empty<int>(), CancellationToken.None);
                backend = new LocalPlatformBackend("测试玩家", LocalProfileTests.Resources, path);
                using (var gateway = new ClientGateway(library, LocalProfileTests.Resources, backend))
                {
                    ulong epoch = 1;
                    await gateway.StartAsync(epoch);
                    await gateway.StartLocalBattleAsync(epoch, 1, 42);
                    var preparations = 0;
                    gateway.SetProfileChangedHandler(async cancellation =>
                    {
                        Program.Check(gateway.TryReserveBattleRearm(), "lobby profile can reserve a new staged binding");
                        await gateway.StopBattleAsync();
                        await gateway.StartLocalBattleAsync(++epoch, 1, 42);
                        Interlocked.Increment(ref preparations);
                    });
                    using (var logic = ClientGatewayTests.Connect(gateway.GetSnapshot().LogicPort))
                    {
                        ClientGatewayTests.AssertBody(logic, "login");
                        ClientGatewayTests.AssertBody(logic, "sync");
                        Match(logic.GetStream(), 630001);
                        Program.Check(gateway.BattleStaged(), "empty formation rejection keeps the lobby open");
                        Edit(logic.GetStream(), "formation-change", 2);
                        Program.Check(preparations == 1 && gateway.BattleStaged(),
                            "a rejected match does not prevent lobby edits or rearming");
                        Edit(logic.GetStream(), "formation-empty", 2);
                        Program.Check(preparations == 2 && gateway.BattleStaged()
                            && (await backend.ReadProfileAsync(CancellationToken.None)).Formation.Count == 0,
                            "removing the last hero keeps the lobby and saved empty formation intact");
                    }
                    using (var logic = ClientGatewayTests.Connect(gateway.GetSnapshot().LogicPort))
                    {
                        ClientGatewayTests.AssertBody(logic, "login");
                        ClientGatewayTests.AssertBody(logic, "sync");
                        Match(logic.GetStream(), 630001);
                        Edit(logic.GetStream(), "save-retry", 1);
                        Program.Check(preparations == 3 && gateway.BattleStaged(),
                            "adding a hero after empty formation restores match preparation");
                    }
                    using (var logic = ClientGatewayTests.Connect(gateway.GetSnapshot().LogicPort))
                    {
                        ClientGatewayTests.AssertBody(logic, "login");
                        ClientGatewayTests.AssertBody(logic, "sync");
                        Match(logic.GetStream(), 0);
                        Program.Check(!gateway.BattleStaged(), "nonempty formation still enters matching");
                    }
                    Program.Check(!gateway.Completion.IsFaulted && gateway.GetSnapshot().ConnectionErrors == 0,
                        "empty formation never faults the gateway or disconnects the lobby");
                    await gateway.StopAsync();
                }
            }
            finally { Directory.Delete(directory, true); }
        }

        private static string[] Row(string file, string name) => File.ReadAllLines(
            Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "fixtures", file))
            .Single(line => line.StartsWith(name + " ", StringComparison.Ordinal)).Split(' ');

        private static void Match(Stream stream, int result)
        {
            var request = ClientGatewayTests.Hex(Row("room-entry-uplink-v1.txt", "match")[3]);
            stream.Write(request, 0, request.Length);
            var expected = ClientGatewayTests.Hex(Row("room-entry-wire-v1.txt", "match-result")[1]);
            var code = BitConverter.GetBytes(result);
            if (BitConverter.IsLittleEndian) Array.Reverse(code);
            Array.Copy(code, 0, expected, expected.Length - 201, 4);
            var response = ReadResponse(stream, 0x20);
            Program.Check(response.SequenceEqual(expected), "match result preserves the original response pattern and error code");
        }

        private static void Edit(Stream stream, string name, int changedHeroes)
        {
            var row = Row("loadout-v1.txt", name);
            var request = ClientGatewayTests.Hex(row[3]);
            stream.Write(request, 0, request.Length);
            var result = ReadResponse(stream, 0x22);
            Program.Check(result.SequenceEqual(ClientGatewayTests.Hex(row[4].Split(',')[0].Split(':')[1])),
                "formation edit succeeds");
            for (var index = 0; index < changedHeroes + 1; ++index)
                ReadResponse(stream, 0x28);
            ReadResponse(stream, 0x22);
        }

        private static byte[] ReadResponse(Stream stream, int command)
        {
            var header = ClientGatewayTests.Read(stream, 12);
            var size = (header[8] << 24) | (header[9] << 16) | (header[10] << 8) | header[11];
            Program.Check(size >= 11 && size < 4 * 1024 * 1024, "bounded lobby reply");
            var body = ClientGatewayTests.Read(stream, size);
            Program.Check(((body[1] << 8) | body[2]) == command, "lobby reply command");
            return body.Skip(11).ToArray();
        }
    }
}
