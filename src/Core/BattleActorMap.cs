using System;
using System.Collections.Generic;
using System.Linq;

namespace T7R.Core
{
    public sealed class BattleActorIdentity
    {
        public int ActorId { get; }
        public ulong PlayerId { get; }
        public int Camp { get; }
        public bool IsBot => PlayerId == 0;

        public BattleActorIdentity(int actorId, ulong playerId, int camp)
        {
            if (actorId < 1 || actorId > BattleActorMap.MaxActors) throw new ArgumentOutOfRangeException(nameof(actorId));
            if (camp != 1 && camp != 2) throw new ArgumentOutOfRangeException(nameof(camp));
            ActorId = actorId; PlayerId = playerId; Camp = camp;
        }
    }

    // Each client sees its own human as RID 1, followed by opponents and teammates.
    public sealed class BattleActorMap
    {
        public const int MaxActors = 60;
        private readonly int[] _toLocal = new int[MaxActors + 1];
        private readonly int[] _toGlobal = new int[MaxActors + 1];
        public ulong MatchId { get; }
        public ulong PlayerId { get; }
        public int OwnActorId { get; }

        public BattleActorMap(ulong matchId, ulong playerId, IEnumerable<BattleActorIdentity> actors)
        {
            if (matchId == 0) throw new ArgumentOutOfRangeException(nameof(matchId));
            if (playerId == 0) throw new ArgumentOutOfRangeException(nameof(playerId));
            if (actors == null) throw new ArgumentNullException(nameof(actors));
            var roster = actors.Take(MaxActors + 1).ToArray();
            if (roster.Length == 0 || roster.Length > MaxActors || roster.Any(actor => actor == null)
                || roster.Select(actor => actor.ActorId).Distinct().Count() != roster.Length)
                throw new ArgumentException("Expected a unique roster within the actor capacity.", nameof(actors));
            var players = roster.Where(actor => !actor.IsBot).ToArray();
            if (players.Select(actor => actor.PlayerId).Distinct().Count() != players.Length)
                throw new ArgumentException("A player owns one human actor.", nameof(actors));
            var own = players.SingleOrDefault(actor => actor.PlayerId == playerId);
            if (own == null) throw new ArgumentException("The local player is not in this match.", nameof(playerId));
            MatchId = matchId; PlayerId = playerId; OwnActorId = own.ActorId;
            Add(own.ActorId, 1);
            int opponent = 2, teammate = 2 + roster.Count(actor => actor.Camp != own.Camp);
            foreach (var actor in roster.OrderBy(actor => actor.ActorId))
            {
                if (actor.ActorId == own.ActorId) continue;
                Add(actor.ActorId, actor.Camp == own.Camp ? teammate++ : opponent++);
            }
        }

        // Zero denotes scene geometry/no target, not an actor.
        public int ToLocal(int actorId) { return Lookup(_toLocal, actorId); }
        public int ToGlobal(int localRid) { return Lookup(_toGlobal, localRid); }

        private void Add(int globalId, int localId)
        {
            _toLocal[globalId] = localId;
            _toGlobal[localId] = globalId;
        }

        private static int Lookup(int[] map, int id)
        {
            if (id == 0) return 0;
            if (id < 1 || id >= map.Length || map[id] == 0)
                throw new ArgumentOutOfRangeException(nameof(id), "Actor is outside this match view.");
            return map[id];
        }
    }
}
