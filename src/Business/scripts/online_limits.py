"""Original client object capacity; keep in sync with game-server/src/limits.rs."""
TEAM_CAPACITY = 10
SERVER_PING_INTERVAL_MS = 1000
ROOM_CAPACITY = TEAM_CAPACITY * 2
ENEMY_FIRST_RID = 2
FRIENDLY_FIRST_RID = ENEMY_FIRST_RID + TEAM_CAPACITY
ALL_RIDS = frozenset(str(i) for i in range(1, ROOM_CAPACITY + 1))
