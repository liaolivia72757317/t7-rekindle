//! Versioned semantic payloads carried by BattleFrame, not original-client GameData.
use std::fmt;

pub const SEMANTIC_VERSION: u16 = 1;
pub const WORLD_SAMPLE_KIND: u8 = 6;
pub const PLAYER_INTENT_KIND: u8 = 11;
pub const MATCH_ABORTED_KIND: u8 = 12;
pub const MATCH_ENDED_KIND: u8 = 8;
pub const MOTION_ATTEMPT_KIND: u8 = 13;
pub const CONTACT_BATCH_KIND: u8 = 14;
#[path = "battle_semantics_evidence.rs"]
pub mod evidence;
#[path = "battle_semantics_state.rs"]
pub mod state;
const SAMPLE_HEADER_SIZE: usize = 40;
const ACTOR_SIZE: usize = 56;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SemanticError(&'static str);

impl fmt::Display for SemanticError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.0)
    }
}
impl std::error::Error for SemanticError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SourceStamp {
    pub epoch: u64,
    pub source_id: u64,
    pub batch_id: u64,
    pub at_ms: u64,
}

impl SourceStamp {
    fn valid(self) -> bool {
        self.epoch != 0 && self.source_id != 0 && self.batch_id != 0
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SampledActor {
    pub actor_id: u32,
    pub client_rid: u16,
    pub sequence: u16,
    pub entity: u64,
    pub position: [f64; 3],
    pub heading: f64,
    pub contact_target_id: u32,
    /// 0 unavailable, 1 blocked, 2 clear.
    pub contact_state: u8,
}

impl SampledActor {
    fn valid(self) -> bool {
        (1..=crate::MAX_BATTLE_ACTORS as u32).contains(&self.actor_id)
            && self.client_rid != 0
            && self.entity != 0
            && self
                .position
                .into_iter()
                .all(|v| v.is_finite() && v.abs() < 100000.)
            && self.heading.is_finite()
            && self.heading.abs() <= 180.
            && self.contact_target_id <= crate::MAX_BATTLE_ACTORS as u32
            && self.contact_state <= 2
            && (self.contact_state == 0 || self.contact_target_id != 0)
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct WorldSample {
    pub stamp: SourceStamp,
    pub actors: Vec<SampledActor>,
}

impl WorldSample {
    pub fn encode(&self) -> Result<Vec<u8>, SemanticError> {
        if !self.stamp.valid() || self.actors.len() > crate::MAX_BATTLE_ACTORS {
            return Err(SemanticError("invalid world sample header"));
        }
        let mut bytes = Vec::with_capacity(SAMPLE_HEADER_SIZE + self.actors.len() * ACTOR_SIZE);
        encode_stamp(self.stamp, &mut bytes);
        bytes.extend_from_slice(&(self.actors.len() as u16).to_be_bytes());
        bytes.extend_from_slice(&0_u16.to_be_bytes());
        let mut actor_ids = 0_u32;
        let mut rids = std::collections::BTreeSet::new();
        for actor in &self.actors {
            if !actor.valid()
                || actor_ids & (1 << actor.actor_id) != 0
                || !rids.insert(actor.client_rid)
            {
                return Err(SemanticError("invalid or duplicate sampled actor"));
            }
            actor_ids |= 1 << actor.actor_id;
            bytes.extend_from_slice(&actor.actor_id.to_be_bytes());
            bytes.extend_from_slice(&actor.client_rid.to_be_bytes());
            bytes.extend_from_slice(&actor.sequence.to_be_bytes());
            bytes.extend_from_slice(&actor.entity.to_be_bytes());
            for coordinate in actor.position {
                bytes.extend_from_slice(&coordinate.to_bits().to_be_bytes());
            }
            bytes.extend_from_slice(&actor.heading.to_bits().to_be_bytes());
            bytes.extend_from_slice(&actor.contact_target_id.to_be_bytes());
            bytes.push(actor.contact_state);
            bytes.extend_from_slice(&[0; 3]);
        }
        Ok(bytes)
    }

    pub fn decode(bytes: &[u8]) -> Result<Self, SemanticError> {
        if bytes.len() < SAMPLE_HEADER_SIZE || bytes[38..40] != [0; 2] {
            return Err(SemanticError(
                "invalid world sample length or reserved field",
            ));
        }
        let count = usize::from(read_u16(bytes, 36));
        if count > crate::MAX_BATTLE_ACTORS
            || bytes.len() != SAMPLE_HEADER_SIZE + count * ACTOR_SIZE
        {
            return Err(SemanticError("invalid world sample actor count"));
        }
        let stamp = decode_stamp(bytes)?;
        let mut actors = Vec::with_capacity(count);
        for item in bytes[SAMPLE_HEADER_SIZE..].chunks_exact(ACTOR_SIZE) {
            if item[53..56] != [0; 3] {
                return Err(SemanticError("invalid sampled actor reserved field"));
            }
            actors.push(SampledActor {
                actor_id: read_u32(item, 0),
                client_rid: read_u16(item, 4),
                sequence: read_u16(item, 6),
                entity: read_u64(item, 8),
                position: [read_f64(item, 16), read_f64(item, 24), read_f64(item, 32)],
                heading: read_f64(item, 40),
                contact_target_id: read_u32(item, 48),
                contact_state: item[52],
            });
        }
        let value = Self { stamp, actors };
        value.encode()?;
        Ok(value)
    }
}

/// Canonical player command: 1–10 press up/right/down/left attack,
/// up/right/down/left guard, shield, dash; 11 attack release; 12 guard release.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PlayerIntent {
    pub stamp: SourceStamp,
    pub actor_id: u32,
    pub client_rid: u16,
    pub command: u8,
    /// Bits 0 defence-down, 1 running, 2 moving-forward; zero for releases.
    pub flags: u8,
}

impl PlayerIntent {
    pub fn encode(self) -> Result<Vec<u8>, SemanticError> {
        if !self.stamp.valid()
            || !(1..=crate::MAX_BATTLE_ACTORS as u32).contains(&self.actor_id)
            || self.client_rid == 0
            || !(1..=12).contains(&self.command)
            || self.flags & !7 != 0
            || self.command >= 11 && self.flags != 0
        {
            return Err(SemanticError("invalid player intent"));
        }
        let mut bytes = Vec::with_capacity(44);
        encode_stamp(self.stamp, &mut bytes);
        bytes.extend_from_slice(&self.actor_id.to_be_bytes());
        bytes.extend_from_slice(&self.client_rid.to_be_bytes());
        bytes.extend_from_slice(&[self.command, self.flags]);
        Ok(bytes)
    }

    pub fn decode(bytes: &[u8]) -> Result<Self, SemanticError> {
        if bytes.len() != 44 {
            return Err(SemanticError("invalid player intent length"));
        }
        let value = Self {
            stamp: decode_stamp(bytes)?,
            actor_id: read_u32(bytes, 36),
            client_rid: read_u16(bytes, 40),
            command: bytes[42],
            flags: bytes[43],
        };
        value.encode()?;
        Ok(value)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MatchAborted {
    pub epoch: u64,
    pub at_ms: u64,
    /// 1 source disconnected, 2 input overflow, 3 output overflow, 4 internal failure.
    pub reason: u8,
}

impl MatchAborted {
    pub fn encode(self) -> Result<Vec<u8>, SemanticError> {
        if self.epoch == 0 || !(1..=4).contains(&self.reason) {
            return Err(SemanticError("invalid match abort"));
        }
        let mut bytes = Vec::with_capacity(20);
        bytes.extend_from_slice(&SEMANTIC_VERSION.to_be_bytes());
        bytes.extend_from_slice(&[self.reason, 0]);
        bytes.extend_from_slice(&self.epoch.to_be_bytes());
        bytes.extend_from_slice(&self.at_ms.to_be_bytes());
        Ok(bytes)
    }

    pub fn decode(bytes: &[u8]) -> Result<Self, SemanticError> {
        if bytes.len() != 20 || read_u16(bytes, 0) != SEMANTIC_VERSION || bytes[3] != 0 {
            return Err(SemanticError("invalid match abort version or length"));
        }
        let value = Self {
            reason: bytes[2],
            epoch: read_u64(bytes, 4),
            at_ms: read_u64(bytes, 12),
        };
        value.encode()?;
        Ok(value)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ResultMember {
    pub actor_id: u32,
    pub camp: u8,
    pub result: u8,
    pub rank: u32,
    pub kills: u32,
    pub deaths: u32,
    pub points: u32,
    pub streak: u32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MatchEnded {
    pub epoch: u64,
    pub started_at_ms: u64,
    pub ended_at_ms: u64,
    /// 1 quit, 2 timeout, 3 kill target.
    pub reason: u8,
    /// 0 quit, 1 camp one, 2 camp two, 3 tie.
    pub result: u8,
    pub camp_kills: [u32; 2],
    pub members: Vec<ResultMember>,
}

impl MatchEnded {
    pub fn encode(&self) -> Result<Vec<u8>, SemanticError> {
        if self.epoch == 0
            || self.ended_at_ms < self.started_at_ms
            || !(1..=3).contains(&self.reason)
            || self.result > 3
            || (self.reason == 1) != (self.result == 0)
            || self.members.is_empty()
            || self.members.len() > crate::MAX_BATTLE_ACTORS
        {
            return Err(SemanticError("invalid match result header"));
        }
        let mut bytes = Vec::with_capacity(40 + self.members.len() * 28);
        bytes.extend_from_slice(&SEMANTIC_VERSION.to_be_bytes());
        bytes.extend_from_slice(&[self.reason, self.result]);
        for value in [self.epoch, self.started_at_ms, self.ended_at_ms] {
            bytes.extend_from_slice(&value.to_be_bytes());
        }
        for kills in self.camp_kills {
            bytes.extend_from_slice(&kills.to_be_bytes());
        }
        bytes.extend_from_slice(&(self.members.len() as u16).to_be_bytes());
        bytes.extend_from_slice(&0_u16.to_be_bytes());
        let mut seen = 0_u32;
        for member in &self.members {
            if !(1..=crate::MAX_BATTLE_ACTORS as u32).contains(&member.actor_id)
                || !(1..=2).contains(&member.camp)
                || member.result > 3
                || member.rank == 0
                || seen & (1 << member.actor_id) != 0
            {
                return Err(SemanticError("invalid match result member"));
            }
            seen |= 1 << member.actor_id;
            bytes.extend_from_slice(&member.actor_id.to_be_bytes());
            bytes.extend_from_slice(&[member.camp, member.result, 0, 0]);
            for value in [
                member.rank,
                member.kills,
                member.deaths,
                member.points,
                member.streak,
            ] {
                bytes.extend_from_slice(&value.to_be_bytes());
            }
        }
        Ok(bytes)
    }

    pub fn decode(bytes: &[u8]) -> Result<Self, SemanticError> {
        if bytes.len() < 40 || read_u16(bytes, 0) != SEMANTIC_VERSION || read_u16(bytes, 38) != 0 {
            return Err(SemanticError("invalid match result version or length"));
        }
        let count = usize::from(read_u16(bytes, 36));
        if count == 0 || count > crate::MAX_BATTLE_ACTORS || bytes.len() != 40 + count * 28 {
            return Err(SemanticError("invalid match result member count"));
        }
        let mut members = Vec::with_capacity(count);
        for item in bytes[40..].chunks_exact(28) {
            if item[6..8] != [0; 2] {
                return Err(SemanticError("invalid result member reserved field"));
            }
            members.push(ResultMember {
                actor_id: read_u32(item, 0),
                camp: item[4],
                result: item[5],
                rank: read_u32(item, 8),
                kills: read_u32(item, 12),
                deaths: read_u32(item, 16),
                points: read_u32(item, 20),
                streak: read_u32(item, 24),
            });
        }
        let value = Self {
            epoch: read_u64(bytes, 4),
            started_at_ms: read_u64(bytes, 12),
            ended_at_ms: read_u64(bytes, 20),
            reason: bytes[2],
            result: bytes[3],
            camp_kills: [read_u32(bytes, 28), read_u32(bytes, 32)],
            members,
        };
        value.encode()?;
        Ok(value)
    }
}

fn encode_stamp(stamp: SourceStamp, bytes: &mut Vec<u8>) {
    bytes.extend_from_slice(&SEMANTIC_VERSION.to_be_bytes());
    bytes.extend_from_slice(&0_u16.to_be_bytes());
    for value in [stamp.epoch, stamp.source_id, stamp.batch_id, stamp.at_ms] {
        bytes.extend_from_slice(&value.to_be_bytes());
    }
}
fn decode_stamp(bytes: &[u8]) -> Result<SourceStamp, SemanticError> {
    if bytes.len() < 36 || read_u16(bytes, 0) != SEMANTIC_VERSION || read_u16(bytes, 2) != 0 {
        return Err(SemanticError("invalid semantic version or reserved field"));
    }
    let stamp = SourceStamp {
        epoch: read_u64(bytes, 4),
        source_id: read_u64(bytes, 12),
        batch_id: read_u64(bytes, 20),
        at_ms: read_u64(bytes, 28),
    };
    if !stamp.valid() {
        return Err(SemanticError("invalid source stamp"));
    }
    Ok(stamp)
}
fn read_u16(bytes: &[u8], at: usize) -> u16 {
    u16::from_be_bytes(bytes[at..at + 2].try_into().expect("bounded"))
}
fn read_u32(bytes: &[u8], at: usize) -> u32 {
    u32::from_be_bytes(bytes[at..at + 4].try_into().expect("bounded"))
}
fn read_u64(bytes: &[u8], at: usize) -> u64 {
    u64::from_be_bytes(bytes[at..at + 8].try_into().expect("bounded"))
}
fn read_f64(bytes: &[u8], at: usize) -> f64 {
    f64::from_bits(read_u64(bytes, at))
}
