using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using T7R.Battle;
using T7R.Core;

namespace T7R.BattleHarness
{
    internal static class BotSelectionTests
    {
        internal static void Run()
        {
            var resources = LocalProfileTests.Resources;
            var catalogue = new LocalProfileCatalogue(resources);
            var defaults = catalogue.Create();
            var player = new PlatformProfile(0, new[] { 1101 }, defaults.Weapons);
            foreach (byte camp in new byte[] { 1, 2 })
            {
                var match = LocalBattleMatch.Create(resources, player, 1, 1, 42, camp);
                var repeated = LocalBattleMatch.Create(resources, player, 2, 1, 42, camp);
                var bots = match.Members.Where(member => member.Kind == 2).ToArray();
                Program.Check(match.Members[0].HeroId == 1101 && match.Members[0].Camp == camp,
                    "bot selection leaves the player's formation and camp intact");
                Program.Check(bots.Select(member => (int)member.HeroId).Distinct().OrderBy(id => id)
                    .SequenceEqual(defaults.Weapons.Keys.OrderBy(id => id)),
                    "bots draw from all playable heroes, not just the player's formation");
                Program.Check(bots.Length > bots.Select(member => member.HeroId).Distinct().Count(),
                    "different bots may use the same hero");
                Program.Check(match.Members.Select(member => member.HeroId)
                    .SequenceEqual(repeated.Members.Select(member => member.HeroId)),
                    "the same match seed reproduces bot hero choices");
                var next = LocalBattleMatch.Create(resources, player, 3, 1, 73, camp);
                Program.Check(!bots.Select(member => member.HeroId)
                    .SequenceEqual(next.Members.Skip(1).Select(member => member.HeroId)),
                    "a new match seed can choose a different roster");
                foreach (var bot in bots)
                {
                    var expected = catalogue.MatchEquipment((int)bot.HeroId,
                        defaults.Weapons[(int)bot.HeroId], out var main);
                    Program.Check(bot.WeaponId == main && bot.Equipment.SequenceEqual(expected),
                        "each bot uses its selected hero's own default equipment");
                }
            }
            foreach (var mapId in new[] { 6006, 6009 })
            {
                var trainingResources = Encoding.UTF8.GetBytes(Regex.Replace(Encoding.UTF8.GetString(resources),
                    "\"resourceId\"\\s*:\\s*1028", "\"resourceId\":" + mapId));
                var training = LocalBattleMatch.Create(trainingResources, player, 4, 1, 42);
                Program.Check(training.Members.Count == 2 && training.Members[0].Camp == 2
                    && training.Members[1].Camp == 1, "training uses the native red player camp and one opponent");
                var heroId = mapId == 6006 ? 10001u : 10002u;
                var weaponId = mapId == 6006 ? 1030111u : 1040511u;
                Program.Check(training.Members.All(member => member.HeroId == heroId
                    && member.WeaponId == weaponId && member.Equipment.Length == 1
                    && member.Equipment[0].WeaponId == weaponId),
                    "training uses the mode's original hero and weapon instead of the saved formation");
                var empty = new PlatformProfile(0, new int[0], defaults.Weapons);
                var emptyTraining = LocalBattleMatch.Create(trainingResources, empty, 5, 1, 73);
                Program.Check(emptyTraining.Members[0].HeroId == heroId,
                    "training default hero does not depend on a nonempty saved formation");
                var nativeProfiles = LocalCombatProfiles.FromResources(LocalTrainingMode.Resources);
                Program.Check(nativeProfiles.Any(item => item.HeroId == heroId && item.WeaponId == weaponId)
                    && nativeProfiles.Any(item => item.HeroId == heroId && item.WeaponId == 100000),
                    "training heroes have native movement and dash profiles before entering a map");
                Program.Check(player.Formation.SequenceEqual(new[] { 1101 }) && player.Revision == 0,
                    "training does not modify the saved formation or profile revision");
            }
        }
    }
}
