using System;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using T7R.Battle;
using T7R.Core;

namespace T7R.BattleHarness
{
    internal static class ClientGatewayBattleTests
    {
        private static BattleMatchConfiguration Config(ulong epoch)
        {
            return new BattleMatchConfiguration
            {
                Epoch = epoch, StartedAtMs = 1000, DurationMs = 120000,
                KillTarget = 10, Seed = 42, SourceId = 1,
                Members = new[]
                {
                    Member(1, 1, 1, 1), Member(2, 2, 2, 2)
                }
            };
        }

        private static BattleMatchMember Member(uint actorId, ushort rid, byte camp, byte kind)
        {
            return new BattleMatchMember
            {
                ActorId = actorId, ClientRid = rid, Camp = camp, Kind = kind,
                HeroId = 1101, WeaponId = 701,
                Equipment = new[] { new BattleMatchEquipment { Slot = 1, WeaponId = 701 } }
            };
        }

        internal static async Task Run(string library)
        {
            await LocalLobbyMatchTests.Run(library);
            await MatchMapTests.Run(library);
            Program.Check(Marshal.SizeOf<BattleNativeEquipment>() == 8, "battle equipment ABI");
            Program.Check(Marshal.SizeOf<BattleNativeMember>() == 52, "battle member ABI");
            Program.Check(Marshal.SizeOf<BattleNativeConfig>() == BattleLimits.NativeConfigSize, "battle config ABI");
            Program.Check(Marshal.SizeOf<NativeCombatProfile>() == 36, "combat profile ABI");
            var bundle = File.ReadAllBytes(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,
                "fixtures", "bundle-v1.json"));
            var movement = LocalCombatProfiles.FromResources(bundle);
            Program.Check(movement.Length == 6 && movement[0].HeroId == 1101
                && movement[0].WeaponId == 100000 && movement[0].ForwardSlow == 2.5f
                && Array.TrueForAll(movement, item => item.WeaponId != 703),
                "Rust resource bundle supplies non-shield client movement profiles");
            using (var gateway = new ClientGateway(library, bundle, LocalProfileTests.CreateBackend("测试玩家")))
            {
                Program.Throws<InvalidOperationException>(() =>
                    gateway.StartBattleAsync(Config(7)).GetAwaiter().GetResult());
                await gateway.StartAsync(1);
                var duplicateRid = Config(7);
                duplicateRid.Members = new[] { Member(1, 1, 1, 1), Member(2, 1, 2, 2) };
                Program.Throws<ArgumentException>(() =>
                    gateway.StartBattleAsync(duplicateRid).GetAwaiter().GetResult());
                await gateway.StartBattleAsync(Config(7));
                Program.Check(!gateway.BattleReturnedToRoom(), "staged battle has not returned to room");
                Program.Throws<InvalidOperationException>(() => gateway.BattleClockMs());
                await Task.Delay(150);
                Program.Check(!gateway.Completion.IsFaulted, "battle output pump remains healthy before native samples");
                Program.Throws<InvalidOperationException>(() =>
                    gateway.StartBattleAsync(Config(8)).GetAwaiter().GetResult());
                await gateway.StopBattleAsync();
                Program.Throws<InvalidOperationException>(() => gateway.BattleReturnedToRoom());
                Program.Throws<InvalidOperationException>(() => gateway.BattleClockMs());
                Program.Throws<InvalidOperationException>(() =>
                    gateway.StartBattleAsync(Config(7)).GetAwaiter().GetResult());
                await gateway.StartBattleAsync(Config(8));
                Program.Check(!gateway.BattleReturnedToRoom(), "new match does not inherit room return state");
                await gateway.StopAsync();
                await gateway.StartAsync(2);
                await gateway.StartBattleAsync(Config(9));
                await gateway.StopBattleAsync();
                await gateway.StopAsync();
            }
            var profile = new LocalProfileCatalogue(bundle).Create();
            var local = LocalBattleMatch.Create(bundle, profile, 10, 1, 42);
            Program.Check(local.Members.Count == 20 && local.DurationMs == 1200000
                && local.KillTarget == 60 && local.Members[0].Kind == 1,
                "local match follows the original 1+19 round contract");
            for (var index = 1; index < 20; ++index)
            {
                var member = local.Members[index];
                Program.Check(member.ActorId == index + 1 && member.ClientRid == index + 1
                    && member.Kind == 2 && member.Camp == (index <= 10 ? 2 : 1),
                    "original enemy-first RID and camp roster");
            }
            var otherCampProfile = new PlatformProfile(0, new[] { 2102 }, profile.Weapons);
            var otherCamp = LocalBattleMatch.Create(bundle, otherCampProfile, 11, 1, 43, 2);
            Program.Check(otherCamp.Members[0].Camp == 2 && otherCamp.Members[0].HeroId == 2102
                && otherCamp.Members[1].Camp == 1 && otherCamp.Members[11].Camp == 2,
                "local roster preserves relative camps and selected hero");
            using (var gateway = new ClientGateway(library, bundle, LocalProfileTests.CreateBackend("测试玩家")))
            {
                await gateway.StartAsync(10);
                await gateway.StartBattleAsync(local);
                Program.Throws<InvalidOperationException>(() => gateway.BattleClockMs());
                await gateway.StopBattleAsync();
                await gateway.StartBattleAsync(otherCamp);
                await gateway.StopBattleAsync();
                await gateway.StopAsync();
            }
            var profilePath = Path.Combine(Path.GetTempPath(), "t7r-local-match-" + Guid.NewGuid().ToString("N"), "profile.json");
            using (var gateway = new ClientGateway(library, bundle,
                new LocalPlatformBackend("测试玩家", bundle, profilePath)))
            {
                await gateway.StartAsync(12);
                Program.Check(gateway.NativeHandle != 0, "managed battle retains the process-local gateway handle");
                var staged = await gateway.StartLocalBattleAsync(12, 1, 44);
                Program.Check(staged.MemberCount == 20 && staged.Members[0].ActorId == 1,
                    "local profile creates the same 20-member staged battle on its gateway handle");
                await gateway.StopBattleAsync();
                await gateway.StopAsync();
            }
            var preparationPath = Path.Combine(Path.GetTempPath(), "t7r-match-preparation-" +
                Guid.NewGuid().ToString("N"), "profile.json");
            var preparedBackend = new LocalPlatformBackend("测试玩家", bundle, preparationPath);
            await preparedBackend.SetFormationAsync(new[] { 2102 }, CancellationToken.None);
            using (var gateway = new ClientGateway(library, bundle, preparedBackend))
            {
                await gateway.StartAsync(20);
                var initial = await gateway.StartLocalBattleAsync(20, 1, 20);
                Program.Check(initial.Members[0].HeroId == 2102 && initial.Members[0].WeaponId == 701,
                    "initial staged match uses the committed formation and default weapon");
                var preparations = 0;
                var deferredChanges = 0;
                gateway.SetProfileChangedHandler(async cancellation =>
                {
                    if (!gateway.TryReserveBattleRearm())
                    {
                        Interlocked.Increment(ref deferredChanges);
                        return;
                    }
                    await gateway.StopBattleAsync();
                    var next = await gateway.StartLocalBattleAsync(21, 1, 21);
                    Program.Check(next.Epoch == 21 && next.Members[0].HeroId == 2102
                        && next.Members[0].WeaponId == 702,
                        "profile write stages the selected hero and weapon in the next generation");
                    Interlocked.Increment(ref preparations);
                });
                using (var logic = ClientGatewayTests.Connect(gateway.GetSnapshot().LogicPort))
                {
                    ClientGatewayTests.AssertBody(logic, "login");
                    ClientGatewayTests.AssertBody(logic, "sync");
                    var row = File.ReadAllLines(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,
                        "fixtures", "loadout-v1.txt"))
                        .Single(line => line.StartsWith("weapons-change ", StringComparison.Ordinal)).Split(' ');
                    var stream = logic.GetStream();
                    Exchange(stream, row);
                    Program.Check(preparations == 1 && gateway.BattleStaged(),
                        "committed profile is staged before the original client sees its response");
                    using (var instance = ClientGatewayTests.Connect(gateway.GetSnapshot().InstancePort))
                    {
                        for (var attempt = 0; attempt < 100 && gateway.BattleStaged(); ++attempt)
                            await Task.Delay(10);
                        Program.Check(!gateway.BattleStaged(),
                            "instance binding freezes the staged match before GAME activation");
                        Program.Check(!gateway.TryReserveBattleRearm(),
                            "an attached instance cannot lose its match to a profile refresh");
                        var later = File.ReadAllLines(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,
                            "fixtures", "loadout-v1.txt"))
                            .Single(line => line.StartsWith("weapons-empty ", StringComparison.Ordinal)).Split(' ');
                        Exchange(stream, later);
                        Program.Check(preparations == 1 && deferredChanges == 1
                            && (await preparedBackend.ReadProfileAsync(CancellationToken.None)).Revision == 3,
                            "a committed profile change after instance binding applies to the next match");
                    }
                }
                await gateway.StopBattleAsync();
                await gateway.StopAsync();
            }
            var preparationDirectory = Path.GetDirectoryName(Path.GetFullPath(preparationPath));
            if (!preparationDirectory.StartsWith(Path.GetFullPath(Path.GetTempPath()), StringComparison.OrdinalIgnoreCase)
                || !Path.GetFileName(preparationDirectory).StartsWith("t7r-match-preparation-", StringComparison.Ordinal))
                throw new InvalidOperationException("Unexpected match preparation fixture path.");
            Directory.Delete(preparationDirectory, true);
            Console.WriteLine("Managed match start/stop -> private Rust C ABI checks passed");
        }

        private static void Exchange(Stream stream, string[] row)
        {
            var uplink = ClientGatewayTests.Hex(row[3]);
            stream.Write(uplink, 0, uplink.Length);
            foreach (var expected in row[4].Split(','))
            {
                var header = ClientGatewayTests.Read(stream, 12);
                var length = (header[8] << 24) | (header[9] << 16) | (header[10] << 8) | header[11];
                Program.Check(length >= 11 && length < 4 * 1024 * 1024, "bounded profile reply");
                var body = ClientGatewayTests.Read(stream, length);
                Program.Check(((body[1] << 8) | body[2]) == int.Parse(expected.Split(':')[0]),
                    "profile reply keeps original message order after preparation decision");
            }
        }
    }
}
