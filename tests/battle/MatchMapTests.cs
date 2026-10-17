using System;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using T7R.Battle;

namespace T7R.BattleHarness
{
    internal static class MatchMapTests
    {
        private static readonly uint[] Maps = { 20001, 1003, 1030, 1021, 20011, 1001, 6003, 20013, 20015, 90003, 1034, 20012 };
        private static readonly uint[] TeamMaps = { 1003, 1030, 1021, 1001, 6003, 1034 };

        internal static async Task Run(string library)
        {
            Program.Throws<ArgumentException>(() => ClientGateway.ChooseMatchMap(null));
            Program.Throws<ArgumentException>(() => ClientGateway.ChooseMatchMap(Array.Empty<uint>()));
            Program.Throws<ArgumentException>(() => ClientGateway.ChooseMatchMap(new uint[] { 1003, 1003 }));
            Program.Throws<ArgumentException>(() => ClientGateway.ChooseMatchMap(new uint[] { 0 }));
            for (var index = 0; index < 100; ++index)
            {
                Program.Check(ClientGateway.ChooseMatchMap(new uint[] { 1030 }) == 1030, "single checked map is deterministic");
                Program.Check(Maps.Contains(ClientGateway.ChooseMatchMap(Maps)), "random map belongs to the checked pool");
            }
            using (var gateway = new ClientGateway(library, LocalProfileTests.Resources, LocalProfileTests.CreateBackend("测试玩家")))
            {
                ulong epoch = 1;
                await gateway.StartAsync(epoch);
                var blueCounts = new[] { 19, 10, 24, 14, 20, 15, 16, 18, 16, 5, 10, 30 };
                var redCounts = new[] { 18, 10, 24, 18, 17, 17, 13, 17, 15, 5, 10, 30 };
                for (var index = 0; index < Maps.Length; ++index)
                {
                    var map = Maps[index];
                    await gateway.SelectMapAsync(map);
                    foreach (byte camp in new byte[] { 1, 2 })
                    {
                        var config = await gateway.StartLocalBattleAsync(++epoch, 1, 42, camp);
                        var members = config.Members.Take((int)config.MemberCount).ToArray();
                        Program.Check(gateway.GetSnapshot().MapId == map
                            && config.MemberCount == blueCounts[index] + redCounts[index]
                            && config.KillTarget == 60 && config.DurationMs == 1200000,
                            "every map retains the native map capacity and existing kill-count rules");
                        Program.Check(members.Count(member => member.Camp == 1) == blueCounts[index]
                            && members.Count(member => member.Camp == 2) == redCounts[index]
                            && members.Single(member => member.Kind == 1).Camp == camp
                            && members[0].ActorId == 1 && members[0].ClientRid == 1
                            && members.Count(member => member.Kind == 2) == members.Length - 1,
                            "one player consumes one native slot in either camp without balancing teams");
                        Program.Throws<InvalidOperationException>(() => gateway.SelectMapAsync(1028).GetAwaiter().GetResult());
                        await gateway.StopBattleAsync();
                    }
                }
                Program.Throws<InvalidOperationException>(() => gateway.SelectMapAsync(9999).GetAwaiter().GetResult());
                Program.Check(gateway.GetSnapshot().MapId == Maps.Last(), "rejected map preserves the current resources");
                var callbacks = 0;
                gateway.SetMatchMapSelectedHandler(async (map, cancellation) =>
                {
                    cancellation.ThrowIfCancellationRequested();
                    Program.Check(gateway.TryReserveBattleRearm(), "match selection reserves the staged battle");
                    await gateway.StopBattleAsync();
                    await gateway.SelectMapAsync(map);
                    await gateway.StartLocalBattleAsync(++epoch, 1, 42);
                    callbacks++;
                });
                foreach (var name in new[] { "empty", "single", "subset", "all", "jieqiao", "team" })
                {
                    await gateway.SelectMapAsync(1028);
                    await gateway.StartLocalBattleAsync(++epoch, 1, 42);
                    using (var logic = ClientGatewayTests.Connect(gateway.GetSnapshot().LogicPort))
                    {
                        ClientGatewayTests.AssertBody(logic, "login");
                        ClientGatewayTests.AssertBody(logic, "sync");
                        var row = Fixture("match-maps-v1.txt", name);
                        var input = ClientGatewayTests.Hex(row[2]);
                        var before = callbacks;
                        var rejected = name == "empty" || name == "jieqiao";
                        logic.GetStream().Write(input, 0, input.Length);
                        var result = Read(logic.GetStream(), 0x20);
                        Program.Check(Int32(result, result.Length - 201) == (rejected ? 630001 : 0), "match selection result");
                        if (rejected)
                        {
                            Program.Check(callbacks == before && gateway.BattleStaged() && gateway.GetSnapshot().MapId == 1028,
                                "empty and non-team map selections reject without consuming the staged match");
                        }
                        else
                        {
                            Program.Check(callbacks == before + 1, "map preparation completes before match response");
                            var chosen = gateway.GetSnapshot().MapId;
                            var checkedMaps = name == "single" ? new uint[] { 1030 }
                                : name == "subset" ? new uint[] { 1003, 1021 } : TeamMaps;
                            Program.Check(checkedMaps.Contains(chosen), "unchecked maps never enter the match");
                            Read(logic.GetStream(), 0x20);
                            Read(logic.GetStream(), 0x1e);
                            var enter = ClientGatewayTests.Hex(Fixture("room-entry-uplink-v1.txt", "enter")[3]);
                            logic.GetStream().Write(enter, 0, enter.Length);
                            var room = Read(logic.GetStream(), 0x1e);
                            Program.Check((uint)Int32(room, room.Length - 20) == chosen,
                                "the existing lobby connection enters the newly prepared map");
                        }
                    }
                    await gateway.StopBattleAsync();
                }
                Program.Check(gateway.GetSnapshot().ConnectionErrors == 0 && !gateway.Completion.IsFaulted,
                    "map changes preserve healthy lobby and platform lifecycles");
                await gateway.StopAsync();
            }
        }

        private static string[] Fixture(string file, string name) => File.ReadAllLines(
            Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "fixtures", file))
            .Single(line => line.StartsWith(name + " ", StringComparison.Ordinal)).Split(' ');

        private static int Int32(byte[] bytes, int offset) =>
            (bytes[offset] << 24) | (bytes[offset + 1] << 16) | (bytes[offset + 2] << 8) | bytes[offset + 3];

        private static byte[] Read(Stream stream, int command)
        {
            var header = ClientGatewayTests.Read(stream, 12);
            var size = Int32(header, 8);
            Program.Check(size >= 11 && size <= 4 * 1024 * 1024, "bounded map match reply");
            var body = ClientGatewayTests.Read(stream, size);
            Program.Check(((body[1] << 8) | body[2]) == command, "map match reply command");
            return body.Skip(11).ToArray();
        }
    }
}
