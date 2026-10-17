using System;
using System.Linq;
using T7R.Core;

namespace T7R.BattleHarness
{
    internal static class ActorMapTests
    {
        internal static void Run()
        {
            var roster = Enumerable.Range(1, 20).Select(id =>
                new BattleActorIdentity(id, (ulong)(100 + id), id <= 10 ? 1 : 2)).ToArray();
            foreach (var own in roster)
            {
                var view = new BattleActorMap(7, own.PlayerId, roster);
                Program.Check(view.MatchId == 7 && view.OwnActorId == own.ActorId && view.ToGlobal(1) == own.ActorId, "local identity");
                Program.Check(view.ToLocal(0) == 0 && view.ToGlobal(0) == 0, "terrain target");
                foreach (var actor in roster)
                {
                    int local = view.ToLocal(actor.ActorId);
                    Program.Check(view.ToGlobal(local) == actor.ActorId, "bijective actor mapping");
                    Program.Check(actor.ActorId == own.ActorId ? local == 1
                        : actor.Camp == own.Camp ? local >= 12 && local <= 20 : local >= 2 && local <= 11, "relative camp slots");
                }
                var reordered = new BattleActorMap(7, own.PlayerId, roster.Reverse());
                Program.Check(roster.All(actor => view.ToLocal(actor.ActorId) == reordered.ToLocal(actor.ActorId)), "roster ordering independence");
            }
            var first = new BattleActorMap(7, 101, roster);
            var second = new BattleActorMap(7, 102, roster);
            Program.Check(first.ToLocal(second.ToGlobal(1)) == 12 && second.ToLocal(first.ToGlobal(1)) == 12, "two humans keep distinct global identity");
            Program.Throws<ArgumentException>(() => new BattleActorMap(7, 101, roster.Concat(roster.Take(1))));
            Program.Throws<ArgumentException>(() => new BattleActorMap(7, 101, new[] { roster[0], roster[0] }));
            Program.Throws<ArgumentException>(() => new BattleActorMap(7, 101,
                new[] { roster[0], new BattleActorIdentity(2, 101, 2) }));
            Program.Throws<ArgumentException>(() => new BattleActorMap(7, 500, roster));
            Program.Throws<ArgumentOutOfRangeException>(() => new BattleActorMap(0, 101, roster));
            Program.Throws<ArgumentOutOfRangeException>(() => new BattleActorIdentity(61, 0, 1));
            Program.Throws<ArgumentOutOfRangeException>(() => new BattleActorIdentity(1, 0, 3));
            var partial = new BattleActorMap(7, 101, new[] { roster[0], new BattleActorIdentity(20, 0, 2) });
            Program.Check(partial.ToLocal(20) == 2, "sparse roster and bot mapping");
            Program.Throws<ArgumentOutOfRangeException>(() => partial.ToGlobal(12));
            Program.Throws<ArgumentOutOfRangeException>(() => partial.ToLocal(2));
            foreach (var counts in new[] { (5, 5), (19, 18), (14, 18), (30, 30) })
            {
                var native = Enumerable.Range(1, counts.Item1 + counts.Item2).Select(id =>
                    new BattleActorIdentity(id, (ulong)(100 + id), id <= counts.Item1 ? 1 : 2)).ToArray();
                foreach (var own in native)
                {
                    var view = new BattleActorMap(8, own.PlayerId, native);
                    var enemies = native.Count(actor => actor.Camp != own.Camp);
                    Program.Check(native.Select(actor => view.ToLocal(actor.ActorId)).OrderBy(id => id)
                        .SequenceEqual(Enumerable.Range(1, native.Length)), "native roster uses every slot exactly once");
                    foreach (var actor in native)
                    {
                        var local = view.ToLocal(actor.ActorId);
                        Program.Check(view.ToGlobal(local) == actor.ActorId, "native roster mapping is bijective");
                        Program.Check(actor.ActorId == own.ActorId ? local == 1
                            : actor.Camp == own.Camp ? local >= enemies + 2 : local >= 2 && local <= enemies + 1,
                            "unequal native camps retain their full capacity");
                    }
                }
            }
        }
    }
}
