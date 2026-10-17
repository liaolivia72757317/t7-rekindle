using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using T7R.Battle;
using T7R.Core;

namespace T7R.BattleHarness
{
    internal static class LocalProfileTests
    {
        internal static byte[] Resources => File.ReadAllBytes(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "fixtures", "bundle-v1.json"));

        internal static LocalPlatformBackend CreateBackend(string playerName) =>
            new LocalPlatformBackend(playerName, Resources,
                Path.Combine(Path.GetTempPath(), "t7r-profile-" + Guid.NewGuid().ToString("N"), "profile.json"));

        internal static async Task Run()
        {
            var directory = Path.Combine(Path.GetTempPath(), "t7r-profile-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            try
            {
                await Changes(Path.Combine(directory, "player.json"));
                await Recovery(Path.Combine(directory, "recovery.json"));
                await Validation(Path.Combine(directory, "invalid.json"));
                await FailedSave(Path.Combine(directory, "failure.json"));
            }
            finally { Directory.Delete(directory, true); }
        }

        private static async Task Changes(string path)
        {
            var backend = new LocalPlatformBackend("玩家", Resources, path);
            var original = await backend.ReadProfileAsync(CancellationToken.None);
            Program.Check(original.Revision == 0 && original.Formation.SequenceEqual(new[] { 1101 }), "original default formation");
            Program.Check(original.Weapons.Count == 2 && original.Weapons[1101].Count == 2, "default equipment for all heroes");
            Program.Check(original.Weapons[1101][0].Group == 0 && original.Weapons[1101][1].Group == 2, "melee before shield");
            Program.Throws<NotSupportedException>(() => ((IList<int>)original.Formation)[0] = 2102);
            Program.Throws<NotSupportedException>(() => ((IList<PlatformWeaponSlot>)original.Weapons[1101])[0] = new PlatformWeaponSlot(1, 1, 0));
            Program.Check(!File.Exists(path), "reading defaults does not write a profile");
            var unchanged = await backend.SetFormationAsync(new[] { 1101 }, CancellationToken.None);
            Program.Check(ReferenceEquals(unchanged.Profile, original) && !File.Exists(path), "no-op does not write or increment revision");
            var input = new[] { 2102, 1101 };
            var change = await backend.SetFormationAsync(input, CancellationToken.None);
            input[0] = 1101;
            Program.Check(change.Profile.Revision == 1 && change.Profile.Formation[0] == 2102, "immutable changed formation");
            Program.Check(ReferenceEquals(change.Previous, original) && original.Formation.Count == 1, "transaction retains previous immutable snapshot");
            var slots = new[] { new PlatformWeaponSlot(3, 2, 0), new PlatformWeaponSlot(1, 1, 0) };
            var equipped = await backend.SetWeaponsAsync(2102, slots, CancellationToken.None);
            Program.Check(equipped.Profile.Revision == 2 && equipped.Profile.Weapons[2102][0].Slot == 1, "equipment is stored in slot order");
            var loaded = await new LocalPlatformBackend("玩家", Resources, path).ReadProfileAsync(CancellationToken.None);
            Program.Check(loaded.Revision == 2 && loaded.Weapons[2102][0].Group == 1, "disk round trip");
            var backup = new LocalPlatformBackend("玩家", Resources, path + ".bak");
            Program.Check((await backup.ReadProfileAsync(CancellationToken.None)).Revision == 1, "backup contains previous committed configuration");
            var empty = await backend.SetFormationAsync(Array.Empty<int>(), CancellationToken.None);
            Program.Check(empty.Profile.Formation.Count == 0 && empty.Profile.Revision == 3, "empty lobby formation is valid");
            var staged = LocalBattleMatch.Create(Resources, empty.Profile, 1, 1, 42);
            Program.Check(staged.Members.Count == 20 && staged.Members[0].HeroId == 1101,
                "empty formation retains a lobby-only staged binding");
            var emptyReloaded = await new LocalPlatformBackend("玩家", Resources, path).ReadProfileAsync(CancellationToken.None);
            Program.Check(emptyReloaded.Formation.Count == 0 && emptyReloaded.Revision == 3,
                "staging an empty formation does not replace the saved profile");
            await backend.SetWeaponsAsync(1101, Array.Empty<PlatformWeaponSlot>(), CancellationToken.None);
            Program.Check((await backend.ReadProfileAsync(CancellationToken.None)).Weapons[1101].Count == 0, "empty equipment preserves fists fallback");
            Program.Throws<OperationCanceledException>(() => backend.SetFormationAsync(new[] { 1101 }, new CancellationToken(true)).GetAwaiter().GetResult());
            Program.Check((await backend.ReadProfileAsync(CancellationToken.None)).Revision == 4, "cancelled operation changes nothing");
            var first = backend.SetFormationAsync(new[] { 1101 }, CancellationToken.None);
            var second = backend.SetWeaponsAsync(2102, Array.Empty<PlatformWeaponSlot>(), CancellationToken.None);
            await Task.WhenAll(first, second);
            var final = await backend.ReadProfileAsync(CancellationToken.None);
            Program.Check(final.Revision == 6 && final.Formation[0] == 1101 && final.Weapons[2102].Count == 0, "concurrent requests serialize without lost updates");
        }

        private static async Task Recovery(string path)
        {
            var backend = new LocalPlatformBackend("玩家", Resources, path);
            await backend.SetFormationAsync(new[] { 2102 }, CancellationToken.None);
            await backend.SetFormationAsync(new[] { 2102, 1101 }, CancellationToken.None);
            File.WriteAllText(path, "broken", Encoding.UTF8);
            var recovered = new LocalPlatformBackend("玩家", Resources, path);
            Program.Check((await recovered.ReadProfileAsync(CancellationToken.None)).Revision == 1, "invalid primary falls back to previous snapshot");
            Program.Check(recovered.ProfileWarnings.Count == 2 && File.ReadAllText(path) == "broken", "recovery reports diagnostics and preserves bad file");
            await recovered.SetFormationAsync(new[] { 1101 }, CancellationToken.None);
            Program.Check((await new LocalPlatformBackend("玩家", Resources, path + ".bak").ReadProfileAsync(CancellationToken.None)).Revision == 1, "save backs up recovered data rather than corrupt primary");
            File.WriteAllText(path, "broken", Encoding.UTF8);
            File.WriteAllText(path + ".bak", "broken", Encoding.UTF8);
            Program.Throws<InvalidDataException>(() => new LocalPlatformBackend("玩家", Resources, path).ReadProfileAsync(CancellationToken.None).GetAwaiter().GetResult());
        }

        private static async Task Validation(string path)
        {
            foreach (var relative in new[] { "player.json", @"C:player.json", @"\player.json" })
                Program.Throws<ArgumentException>(() => new LocalPlatformBackend("玩家", Resources, relative));
            var backend = new LocalPlatformBackend("玩家", Resources, path);
            foreach (var ids in new[] { new[] { 1101, 1101 }, new[] { 9999 }, new[] { 1101, 2102, 3, 4, 5 } })
                Program.Throws<ArgumentException>(() => backend.SetFormationAsync(ids, CancellationToken.None).GetAwaiter().GetResult());
            Program.Throws<ArgumentException>(() => backend.SetWeaponsAsync(1101, new[] { new PlatformWeaponSlot(1, 7, 5) }, CancellationToken.None).GetAwaiter().GetResult());
            Program.Throws<ArgumentException>(() => backend.SetWeaponsAsync(1101, new[] { new PlatformWeaponSlot(1, 0, 0), new PlatformWeaponSlot(2, 0, 0) }, CancellationToken.None).GetAwaiter().GetResult());
            Program.Check(!File.Exists(path), "invalid changes do not write");
            await backend.SetFormationAsync(new[] { 2102 }, CancellationToken.None);
            var valid = File.ReadAllText(path, Encoding.UTF8);
            // A distinct path ensures no valid backup hides the invalid primary.
            var bad = path + ".bad";
            foreach (var text in new[] { valid.Replace("\"revision\":1", "\"revision\":1.0"),
                valid.Replace("\"revision\":1", "\"revision\":18446744073709551615"),
                valid.Replace("\"revision\":1", "\"revision\":1,\"extra\":0"),
                valid.Replace("\"schemaVersion\":1", "\"schemaVersion\":1,\"schemaVersion\":1"),
                valid.Replace("\"schemaVersion\":1", "\"schemaVersion\":true"), new string(' ', 1024 * 1024 + 1) })
            {
                Program.Check(text != valid, "invalid fixture actually changes JSON");
                File.WriteAllText(bad, text, new UTF8Encoding(false));
                Program.Throws<InvalidDataException>(() => new LocalPlatformBackend("玩家", Resources, bad).ReadProfileAsync(CancellationToken.None).GetAwaiter().GetResult());
            }
            var large = valid.Replace("\"revision\":1", "\"revision\":9007199254740993");
            File.WriteAllText(bad, large, new UTF8Encoding(false));
            var precise = new LocalPlatformBackend("玩家", Resources, bad);
            Program.Check((await precise.SetFormationAsync(new[] { 1101 }, CancellationToken.None)).Profile.Revision == 9007199254740994UL, "u64 revision is never a floating point number");
            var exhausted = valid.Replace("\"revision\":1", "\"revision\":18446744073709551614");
            File.WriteAllText(bad, exhausted, new UTF8Encoding(false));
            var terminal = new LocalPlatformBackend("玩家", Resources, bad);
            Program.Check((await terminal.SetFormationAsync(new[] { 2102 }, CancellationToken.None)).Profile.Revision == ulong.MaxValue - 1, "terminal revision allows no-op");
            Program.Throws<ArgumentException>(() => terminal.SetFormationAsync(new[] { 1101 }, CancellationToken.None).GetAwaiter().GetResult());
            Program.Check(File.ReadAllText(bad, Encoding.UTF8) == exhausted, "revision exhaustion leaves disk unchanged");
        }

        private static async Task FailedSave(string path)
        {
            var backend = new LocalPlatformBackend("玩家", Resources, path);
            await backend.SetFormationAsync(new[] { 2102 }, CancellationToken.None);
            var before = File.ReadAllBytes(path);
            using (var locked = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
                Program.Throws<IOException>(() => backend.SetFormationAsync(new[] { 1101 }, CancellationToken.None).GetAwaiter().GetResult());
            Program.Check((await backend.ReadProfileAsync(CancellationToken.None)).Revision == 1, "failed save leaves in-memory revision unchanged");
            Program.Check(before.SequenceEqual(File.ReadAllBytes(path)), "failed save leaves primary unchanged");
            Program.Check(!Directory.EnumerateFiles(Path.GetDirectoryName(path), "*.tmp").Any(), "failed save cleans its own temporary file");
            Program.Check((await backend.SetFormationAsync(new[] { 1101 }, CancellationToken.None)).Profile.Revision == 2, "retry commits once");
        }
    }
}
