using System;
using System.Linq;

namespace T7R.Core
{
    public static class MatchMapSelection
    {
        // The open maps shared by native small-battlefield and team-competition pools.
        public static uint[] All => new uint[] { 1003, 1030, 1021, 1001, 6003, 1034 };

        public static bool IsValid(uint[] ids) => ids != null && ids.Length > 0
            && ids.Length <= All.Length && ids.Distinct().Count() == ids.Length && ids.All(All.Contains);

        public static uint[] Normalize(uint[] ids)
        {
            if (!IsValid(ids)) throw new ArgumentException("至少选择一张可用的团队作战地图。", nameof(ids));
            return All.Where(ids.Contains).ToArray();
        }
    }
}
