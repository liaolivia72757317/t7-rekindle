using System;
using System.IO;
using System.Linq;
using System.Net.Sockets;
using System.Threading.Tasks;
using T7R.Battle;

namespace T7R.BattleHarness
{
    internal static class ClientGatewayTitleTests
    {
        internal static async Task Run(string library)
        {
            var rows = File.ReadAllLines(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "fixtures", "titles-v1.txt"))
                .Select(line => line.Split(' ')).ToDictionary(row => row[0], row => ClientGatewayTests.Hex(row[1]));
            using (var gateway = new ClientGateway(library, LocalProfileTests.Resources, LocalProfileTests.CreateBackend("测试玩家")))
            {
                await gateway.StartAsync(1);
                using (var logic = ClientGatewayTests.Connect(gateway.GetSnapshot().LogicPort))
                {
                    ClientGatewayTests.AssertBody(logic, "login"); ClientGatewayTests.AssertBody(logic, "sync");
                    const string before = "{\"titles\":[{\"titleId\":1007,\"owned\":true,\"enabled\":true}],\"equippedTitleId\":null}";
                    gateway.PublishTitles(5, before, 0, true);
                    Expect(logic, rows["user"]); Expect(logic, rows["stats"]);
                    foreach (var kind in new[] { "user", "stats" })
                    {
                        var request = rows["query-" + kind];
                        logic.GetStream().Write(request, 0, request.Length);
                        Expect(logic, rows[kind]);
                    }
                    var equip = rows["equip"];
                    logic.GetStream().Write(equip, 0, equip.Length);
                    GatewayTitleRequest pending = default;
                    for (var attempt = 0; attempt < 500 && pending.RequestId == 0; ++attempt)
                    { pending = gateway.ReadTitleRequest(); await Task.Delay(10); }
                    Program.Check(pending.RequestId != 0 && pending.Epoch == 5 && pending.TitleId == 1007,
                        "title write waits for platform confirmation through Rust FFI");
                    Program.Check(!logic.GetStream().DataAvailable, "pending title change sent premature success");
                    gateway.PublishTitles(5, before.Replace("null", "1007"), pending.RequestId, true);
                    Expect(logic, rows["accepted"]); Expect(logic, rows["equipped-user"]); Expect(logic, rows["equipped-stats"]);
                    Program.Check(gateway.ReadTitleRequest().RequestId == 0, "title completion clears request");
                }
                await gateway.StopAsync();
            }
            Console.WriteLine("Rust title publication, query and confirmation match the Python wire corpus");
        }

        private static void Expect(TcpClient client, byte[] expected)
        {
            var stream = client.GetStream();
            var header = ClientGatewayTests.Read(stream, 12);
            var size = (header[8] << 24) | (header[9] << 16) | (header[10] << 8) | header[11];
            Program.Check(size >= 11 && size < 65536, "bounded title response");
            var body = ClientGatewayTests.Read(stream, size);
            Program.Check(body[1] == 0 && body[2] == 31 && body.Skip(11).SequenceEqual(expected), "title wire or output order differs");
        }
    }
}
