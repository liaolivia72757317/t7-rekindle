using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.Serialization;
using T7R.Core;

namespace T7R.Battle
{
    internal static class LocalBattleMatch
    {
        internal static BattleMatchConfiguration Create(byte[] resources, PlatformProfile profile,
            ulong epoch, ulong sourceId, uint seed, byte playerCamp = 1)
        {
            if (profile == null) throw new ArgumentNullException(nameof(profile));
            if (epoch == 0 || sourceId == 0 || (playerCamp != 1 && playerCamp != 2))
                throw new ArgumentException("Invalid local match identity.");
            var catalogue = new LocalProfileCatalogue(resources);
            catalogue.Validate(profile);
            var map = PlatformProfileJson.Deserialize<MapBundle>(resources)?.Map;
            var trainingMode = map == null ? null : LocalTrainingMode.Find(map.ResourceId);
            var training = trainingMode != null;
            if (training) playerCamp = 2;
            if (map?.Spawns == null || !map.Spawns.TryGetValue("1", out var blue)
                || !map.Spawns.TryGetValue("2", out var red) || blue == null || red == null
                || blue.Length == 0 || red.Length == 0 || blue.Length + red.Length > BattleLimits.MaxActors)
                throw new InvalidDataException("Invalid map spawn capacity.");
            var ownCount = training ? 1 : playerCamp == 1 ? blue.Length : red.Length;
            var enemyCount = training ? 1 : playerCamp == 1 ? red.Length : blue.Length;
            var botProfile = catalogue.Create();
            var botHeroes = botProfile.Weapons.Keys.OrderBy(id => id).ToArray();
            var random = new Random(unchecked((int)seed));
            // The lobby needs a staged binding even when empty; match admission checks the saved formation.
            var playerHeroId = profile.Formation.Count == 0 ? botProfile.Formation[0] : profile.Formation[0];
            var members = new List<BattleMatchMember>(ownCount + enemyCount);
            members.Add(training ? trainingMode.Member(1, 1, playerCamp) : Member(1, 1, playerCamp, 1, playerHeroId,
                profile.Weapons[playerHeroId], catalogue));
            for (uint actorId = 2; actorId <= ownCount + enemyCount; ++actorId)
            {
                var camp = actorId <= enemyCount + 1 ? (byte)(3 - playerCamp) : playerCamp;
                var botHeroId = botHeroes[random.Next(botHeroes.Length)];
                members.Add(training ? trainingMode.Member(actorId, 2, camp) :
                    Member(actorId, checked((ushort)actorId), camp, 2, botHeroId,
                        botProfile.Weapons[botHeroId], catalogue));
            }
            return new BattleMatchConfiguration
            {
                Epoch = epoch, SourceId = sourceId, Seed = seed,
                StartedAtMs = 1000, DurationMs = 1200000, KillTarget = 60,
                Members = members
            };
        }

        [DataContract] private sealed class MapBundle
        {
            [DataMember(Name = "map")] public MapData Map { get; set; }
        }
        [DataContract] private sealed class MapData
        {
            [DataMember(Name = "resourceId")] public uint ResourceId { get; set; }
            [DataMember(Name = "spawns")] public Dictionary<string, object[]> Spawns { get; set; }
        }

        private static BattleMatchMember Member(uint actorId, ushort clientRid, byte camp,
            byte kind, int heroId, IReadOnlyList<PlatformWeaponSlot> slots, LocalProfileCatalogue catalogue)
        {
            var equipment = catalogue.MatchEquipment(heroId, slots, out var mainWeaponId);
            return new BattleMatchMember
            {
                ActorId = actorId, ClientRid = clientRid, Camp = camp, Kind = kind,
                HeroId = checked((uint)heroId), WeaponId = mainWeaponId, Equipment = equipment
            };
        }
    }
}
