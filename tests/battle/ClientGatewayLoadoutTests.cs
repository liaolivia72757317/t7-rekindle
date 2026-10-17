using System;
using System.IO;
using System.Linq;
using System.Net.Sockets;
using System.Threading;
using System.Threading.Tasks;
using T7R.Battle;

namespace T7R.BattleHarness
{
    internal static class ClientGatewayLoadoutTests
    {
        internal static async Task Run(string library)
        {
            var directory = Path.Combine(Path.GetTempPath(), "t7r-gateway-loadout-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            var path = Path.Combine(directory, "player.json");
            try
            {
                var backend = new LocalPlatformBackend("测试玩家", LocalProfileTests.Resources, path);
                using (var gateway = new ClientGateway(library, LocalProfileTests.Resources, backend))
                {
                    await gateway.StartAsync(1);
                    var preparedRevision = 0;
                    gateway.SetProfileChangedHandler(async cancellation =>
                    {
                        var profile = await backend.ReadProfileAsync(cancellation);
                        Program.Check(profile.Revision == (ulong)Interlocked.Increment(ref preparedRevision),
                            "profile preparation runs after each committed revision and before its reply");
                    });
                    using (var logic = ClientGatewayTests.Connect(gateway.GetSnapshot().LogicPort))
                    {
                        ClientGatewayTests.AssertBody(logic, "login");
                        ClientGatewayTests.AssertBody(logic, "sync");
                        foreach (var row in File.ReadAllLines(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "fixtures", "loadout-v1.txt"))
                            .Where(line => line.Length > 0 && !line.StartsWith("#", StringComparison.Ordinal)).Select(line => line.Split(' ')))
                        {
                            if (row[0] == "save-failed")
                            {
                                using (var locked = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read)) Exchange(logic, row);
                                Program.Check((await backend.ReadProfileAsync(CancellationToken.None)).Revision == 3, "gateway persistence failure is not published");
                            }
                            else Exchange(logic, row);
                        }
                        var final = await backend.ReadProfileAsync(CancellationToken.None);
                        Program.Check(final.Revision == 5 && final.Formation.SequenceEqual(new[] { 1101 }) && final.Weapons[1101].Count == 0,
                            "native lobby requests commit the original profile semantics");
                        Program.Check(preparedRevision == 5, "no-op or rejected profile writes do not rearm a battle");
                    }
                    await gateway.StopAsync();
                    Program.Check(gateway.GetSnapshot().PendingRequests == 0 && gateway.GetSnapshot().ConnectionErrors == 0, "lobby traffic shuts down cleanly");
                }
                var restored = new LocalPlatformBackend("测试玩家", LocalProfileTests.Resources, path);
                Program.Check((await restored.ReadProfileAsync(CancellationToken.None)).Revision == 5, "next launcher loads committed configuration");
                using (var gateway = new ClientGateway(library, LocalProfileTests.Resources, restored))
                {
                    await gateway.StartAsync(2);
                    using (var logic = ClientGatewayTests.Connect(gateway.GetSnapshot().LogicPort))
                    {
                        ClientGatewayTests.AssertBody(logic, "login");
                        ClientGatewayTests.AssertBody(logic, "sync");
                        var row = File.ReadAllLines(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "fixtures", "loadout-v1.txt"))
                            .Single(line => line.StartsWith("formations ", StringComparison.Ordinal)).Split(' ');
                        Exchange(logic, row);
                    }
                    await gateway.StopAsync();
                }
            }
            finally { Directory.Delete(directory, true); }
            Console.WriteLine("Rust lobby -> C# profile -> durable storage: shared Python loadout corpus passed");
        }

        private static void Exchange(TcpClient client, string[] row)
        {
            var incoming = ClientGatewayTests.Hex(row[3]);
            var stream = client.GetStream();
            stream.Write(incoming, 0, incoming.Length);
            foreach (var expected in row[4].Split(','))
            {
                var pair = expected.Split(':');
                var header = ClientGatewayTests.Read(stream, 12);
                Program.Check(header.Take(8).SequenceEqual(ClientGatewayTests.Hex("550e05000000000c")), "lobby plain frame header");
                var size = (header[8] << 24) | (header[9] << 16) | (header[10] << 8) | header[11];
                Program.Check(size >= 11 && size < 4 * 1024 * 1024, "bounded lobby frame");
                var body = ClientGatewayTests.Read(stream, size);
                Program.Check(((body[1] << 8) | body[2]) == int.Parse(pair[0]), "lobby response command: " + row[0]);
                Program.Check(body.Skip(11).SequenceEqual(ClientGatewayTests.Hex(pair[1])), "lobby body and publication order match Python: " + row[0]);
            }
        }
    }
}
