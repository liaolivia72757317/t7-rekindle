using System;
using System.Threading;
using System.Threading.Tasks;
using T7R.Battle;
using T7R.Core;

namespace T7R.BattleHarness
{
    internal static class PlatformBackendTests
    {
        internal static async Task Run()
        {
            var backend = LocalProfileTests.CreateBackend(" 测试玩家 ");
            var identity = await backend.ReadIdentityAsync(CancellationToken.None);
            Program.Check(identity.DisplayName == "测试玩家" && identity.Level == 100 && identity.Portrait == 7, "local platform identity");
            Program.Check(ReferenceEquals(identity, await backend.ReadIdentityAsync(CancellationToken.None)), "immutable identity snapshot");
            Program.Throws<OperationCanceledException>(() => backend.ReadIdentityAsync(new CancellationToken(true)).GetAwaiter().GetResult());
            Program.Throws<ArgumentException>(() => LocalProfileTests.CreateBackend("😀"));
            Program.Throws<ArgumentException>(() => LocalProfileTests.CreateBackend(new string('中', 16)));
            Program.Throws<ArgumentOutOfRangeException>(() => new PlatformIdentity("玩家", -1, 100));
            Program.Throws<ArgumentOutOfRangeException>(() => new PlatformIdentity("玩家", 7, 0));
        }
    }
}
