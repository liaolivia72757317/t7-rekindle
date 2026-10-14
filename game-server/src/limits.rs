/// Original client slots: owner 1, opponents 2..=11, teammates 12..=20.
pub const TEAM_CAPACITY: usize = 10;
pub const ROOM_CAPACITY: usize = TEAM_CAPACITY * 2;
pub const MAX_CONNECTIONS: usize = 128;
pub const TICKET_TTL_SECS: u64 = 120;
pub const MAX_AUTH_RECORDS: usize = 4096;
pub const COMMAND_QUEUE_SIZE: usize = 1024;
pub const OUTPUT_QUEUE_SIZE: usize = 128;
pub const LOGIN_WINDOW_SECS: u64 = 60;
pub const LOGIN_ATTEMPTS_PER_SOURCE: usize = 30;

#[derive(Clone, Copy, Debug)]
pub struct RoomRules {
    capacity: usize,
}
impl Default for RoomRules {
    fn default() -> Self {
        Self {
            capacity: ROOM_CAPACITY,
        }
    }
}
impl RoomRules {
    pub fn new(capacity: usize) -> Result<Self, &'static str> {
        if !(1..=ROOM_CAPACITY).contains(&capacity) {
            return Err("room capacity must be between 1 and 20 client slots");
        }
        Ok(Self { capacity })
    }
    pub fn capacity(self) -> usize {
        self.capacity
    }
    pub fn team_capacity(self) -> usize {
        self.capacity.div_ceil(2)
    }
}

pub const MAX_SWING_HISTORY: usize = ROOM_CAPACITY * 8;
pub const ENEMY_FIRST_RID: u32 = 2;
pub const FRIENDLY_FIRST_RID: u32 = ENEMY_FIRST_RID + TEAM_CAPACITY as u32;
