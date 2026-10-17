namespace T7R.Battle
{
    internal static class BattleLimits
    {
        internal const int MaxActors = T7R.Core.BattleActorMap.MaxActors;
        internal const uint ConfigAbiVersion = 2;
        internal const int NativeConfigSize = 56 + MaxActors * 52;
    }
}
