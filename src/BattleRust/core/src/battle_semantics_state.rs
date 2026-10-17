//! Revisioned authoritative state: actor replacement events plus full checkpoints.
use super::{SEMANTIC_VERSION, SemanticError, read_f64, read_u16, read_u32, read_u64};
use std::collections::BTreeSet;

const HEADER_SIZE: usize = 40;
const ACTOR_SIZE: usize = 152;
const EVENT_SIZE: usize = 4 + ACTOR_SIZE;

/// Mode 1 is an ordered transaction of ActorStateChanged events; mode 2 is a full checkpoint.
#[derive(Debug, Clone, PartialEq)]
pub struct MatchState {
    pub mode: u8,
    pub epoch: u64,
    pub revision: u64,
    pub base_revision: u64,
    pub at_ms: u64,
    pub actors: Vec<StateActor>,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct StateActor {
    pub actor_id: u32,
    pub camp: u8,
    pub kind: u8,
    pub life: u8,
    pub intent: u8,
    pub phase: u8,
    /// Bit 0: action released.
    pub flags: u8,
    pub sequence: u16,
    pub duration_ms: u16,
    pub hero_id: u32,
    pub health: u32,
    pub guard: f64,
    pub score: [u32; 4],
    pub weapon_id: u32,
    pub offhand_id: u32,
    pub main_weapon_id: u32,
    pub shield_count: u8,
    /// Unused slots must be zero; active slots are sorted by weapon ID.
    pub shields: [(u32, u32); 4],
    pub action_changed_at_ms: u64,
    /// Death time for Dead/Selecting/Waiting; spawn time for Spawning; zero for Alive.
    pub life_at_ms: u64,
    /// Only Dead carries a respawn deadline.
    pub respawn_at_ms: u64,
    /// Only Spawning carries a spawn reference.
    pub spawn_position: [f64; 3],
    pub spawn_heading: f64,
}

impl StateActor {
    fn valid(self, at_ms: u64) -> bool {
        (1..=crate::MAX_BATTLE_ACTORS as u32).contains(&self.actor_id)
            && (1..=2).contains(&self.camp)
            && (1..=2).contains(&self.kind)
            && (1..=5).contains(&self.life)
            && self.intent <= 10
            && self.phase <= 9
            && self.flags & !1 == 0
            && self.sequence != 0
            && self.hero_id != 0
            && self.health <= i32::MAX as u32
            && self.guard.is_finite()
            && self.guard >= 0.
            && self.weapon_id != 0
            && self.main_weapon_id != 0
            && self.shield_count <= 4
            && self.shields[..usize::from(self.shield_count)]
                .iter()
                .enumerate()
                .all(|(i, (id, _))| *id != 0 && (i == 0 || self.shields[i - 1].0 < *id))
            && self.shields[usize::from(self.shield_count)..]
                .iter()
                .all(|pair| *pair == (0, 0))
            && self.action_changed_at_ms <= at_ms
            && self.life_at_ms <= at_ms
            && (self.life == 2) == (self.respawn_at_ms != 0)
            && (self.life != 2 || self.respawn_at_ms >= self.life_at_ms)
            && (self.life != 1 || self.life_at_ms == 0)
            && self
                .spawn_position
                .into_iter()
                .all(|v| v.is_finite() && v.abs() < 100000.)
            && self.spawn_heading.is_finite()
            && self.spawn_heading.abs() <= 180.
            && (self.life == 5 || self.spawn_position == [0.; 3] && self.spawn_heading == 0.)
    }

    fn encode(self, bytes: &mut Vec<u8>) {
        bytes.extend_from_slice(&self.actor_id.to_be_bytes());
        bytes.extend_from_slice(&[
            self.camp,
            self.kind,
            self.life,
            self.intent,
            self.phase,
            self.flags,
        ]);
        bytes.extend_from_slice(&self.sequence.to_be_bytes());
        bytes.extend_from_slice(&self.duration_ms.to_be_bytes());
        bytes.extend_from_slice(&0_u16.to_be_bytes());
        bytes.extend_from_slice(&self.hero_id.to_be_bytes());
        bytes.extend_from_slice(&self.health.to_be_bytes());
        bytes.extend_from_slice(&self.guard.to_bits().to_be_bytes());
        for value in self.score {
            bytes.extend_from_slice(&value.to_be_bytes());
        }
        bytes.extend_from_slice(&self.weapon_id.to_be_bytes());
        bytes.extend_from_slice(&self.offhand_id.to_be_bytes());
        for value in [
            self.action_changed_at_ms,
            self.life_at_ms,
            self.respawn_at_ms,
        ] {
            bytes.extend_from_slice(&value.to_be_bytes());
        }
        for value in self.spawn_position {
            bytes.extend_from_slice(&value.to_bits().to_be_bytes());
        }
        bytes.extend_from_slice(&self.spawn_heading.to_bits().to_be_bytes());
        bytes.extend_from_slice(&self.main_weapon_id.to_be_bytes());
        bytes.extend_from_slice(&[self.shield_count, 0, 0, 0]);
        for (weapon_id, health) in self.shields {
            bytes.extend_from_slice(&weapon_id.to_be_bytes());
            bytes.extend_from_slice(&health.to_be_bytes());
        }
    }

    fn decode(bytes: &[u8]) -> Result<Self, SemanticError> {
        if read_u16(bytes, 14) != 0 || bytes[117..120] != [0; 3] {
            return Err(SemanticError("invalid state actor reserved field"));
        }
        Ok(Self {
            actor_id: read_u32(bytes, 0),
            camp: bytes[4],
            kind: bytes[5],
            life: bytes[6],
            intent: bytes[7],
            phase: bytes[8],
            flags: bytes[9],
            sequence: read_u16(bytes, 10),
            duration_ms: read_u16(bytes, 12),
            hero_id: read_u32(bytes, 16),
            health: read_u32(bytes, 20),
            guard: read_f64(bytes, 24),
            score: [
                read_u32(bytes, 32),
                read_u32(bytes, 36),
                read_u32(bytes, 40),
                read_u32(bytes, 44),
            ],
            weapon_id: read_u32(bytes, 48),
            offhand_id: read_u32(bytes, 52),
            action_changed_at_ms: read_u64(bytes, 56),
            life_at_ms: read_u64(bytes, 64),
            respawn_at_ms: read_u64(bytes, 72),
            spawn_position: [
                read_f64(bytes, 80),
                read_f64(bytes, 88),
                read_f64(bytes, 96),
            ],
            spawn_heading: read_f64(bytes, 104),
            main_weapon_id: read_u32(bytes, 112),
            shield_count: bytes[116],
            shields: std::array::from_fn(|index| {
                let at = 120 + index * 8;
                (read_u32(bytes, at), read_u32(bytes, at + 4))
            }),
        })
    }
}

impl MatchState {
    pub fn encode(&self) -> Result<Vec<u8>, SemanticError> {
        if self.epoch == 0
            || self.revision == 0
            || self.actors.is_empty()
            || self.actors.len() > crate::MAX_BATTLE_ACTORS
            || match self.mode {
                1 => self.revision < 2 || self.base_revision != self.revision - 1,
                2 => self.base_revision != 0,
                _ => true,
            }
        {
            return Err(SemanticError("invalid match state header"));
        }
        let mut seen = BTreeSet::new();
        if self
            .actors
            .iter()
            .any(|actor| !actor.valid(self.at_ms) || !seen.insert(actor.actor_id))
        {
            return Err(SemanticError("invalid match state actor"));
        }
        let item_size = if self.mode == 1 {
            EVENT_SIZE
        } else {
            ACTOR_SIZE
        };
        let mut bytes = Vec::with_capacity(HEADER_SIZE + self.actors.len() * item_size);
        bytes.extend_from_slice(&SEMANTIC_VERSION.to_be_bytes());
        bytes.extend_from_slice(&[self.mode, 0]);
        for value in [self.epoch, self.revision, self.base_revision, self.at_ms] {
            bytes.extend_from_slice(&value.to_be_bytes());
        }
        bytes.extend_from_slice(&(self.actors.len() as u16).to_be_bytes());
        bytes.extend_from_slice(&0_u16.to_be_bytes());
        for actor in &self.actors {
            if self.mode == 1 {
                bytes.extend_from_slice(&[1, 0, 0, 0]);
            }
            actor.encode(&mut bytes);
        }
        Ok(bytes)
    }

    pub fn decode(bytes: &[u8]) -> Result<Self, SemanticError> {
        if bytes.len() < HEADER_SIZE
            || read_u16(bytes, 0) != SEMANTIC_VERSION
            || bytes[3] != 0
            || read_u16(bytes, 38) != 0
        {
            return Err(SemanticError(
                "invalid match state version or reserved field",
            ));
        }
        let mode = bytes[2];
        let item_size = match mode {
            1 => EVENT_SIZE,
            2 => ACTOR_SIZE,
            _ => return Err(SemanticError("invalid match state mode")),
        };
        let count = usize::from(read_u16(bytes, 36));
        if count == 0
            || count > crate::MAX_BATTLE_ACTORS
            || bytes.len() != HEADER_SIZE + count * item_size
        {
            return Err(SemanticError("invalid match state length"));
        }
        let mut actors = Vec::with_capacity(count);
        for item in bytes[HEADER_SIZE..].chunks_exact(item_size) {
            let body = if mode == 1 {
                if item[..4] != [1, 0, 0, 0] {
                    return Err(SemanticError("invalid state event type"));
                }
                &item[4..]
            } else {
                item
            };
            actors.push(StateActor::decode(body)?);
        }
        let state = Self {
            mode,
            epoch: read_u64(bytes, 4),
            revision: read_u64(bytes, 12),
            base_revision: read_u64(bytes, 20),
            at_ms: read_u64(bytes, 28),
            actors,
        };
        state.encode()?;
        Ok(state)
    }
}
