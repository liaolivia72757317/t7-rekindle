//! Portable evidence layout. Native ingress and public frames use the same match-clock units.
use super::{
    SemanticError, SourceStamp, decode_stamp, encode_stamp, read_f64, read_u16, read_u32, read_u64,
};

const MOTION_SIZE: usize = 100;
const CONTACT_HEADER_SIZE: usize = 52;
const CONTACT_SIZE: usize = 280;
const MAX_CONTACTS: usize = 8192;

fn point_valid(point: [f64; 3]) -> bool {
    point
        .into_iter()
        .all(|value| value.is_finite() && value.abs() < 100000.)
}

fn unit_valid(point: [f64; 3]) -> bool {
    point_valid(point)
        && (0.9..=1.1).contains(&point.into_iter().map(|v| v * v).sum::<f64>().sqrt())
}

fn put_f64(bytes: &mut Vec<u8>, value: f64) {
    bytes.extend_from_slice(&value.to_bits().to_be_bytes());
}

fn put_point(bytes: &mut Vec<u8>, point: [f64; 3]) {
    for value in point {
        put_f64(bytes, value);
    }
}

fn read_point(bytes: &[u8], at: usize) -> [f64; 3] {
    [
        read_f64(bytes, at),
        read_f64(bytes, at + 8),
        read_f64(bytes, at + 16),
    ]
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct MotionAttempt {
    pub stamp: SourceStamp,
    pub world_batch_id: u64,
    pub actor_id: u32,
    pub client_rid: u16,
    pub entity: u64,
    pub position: [f64; 3],
    pub heading: f64,
    /// Zero is an explicit false attempt, not an omitted sample.
    pub attempted_forward_at_ms: u64,
}

impl MotionAttempt {
    pub fn encode(self) -> Result<Vec<u8>, SemanticError> {
        if !self.stamp.valid()
            || self.world_batch_id == 0
            || !(1..=crate::MAX_BATTLE_ACTORS as u32).contains(&self.actor_id)
            || self.client_rid == 0
            || self.entity == 0
            || !point_valid(self.position)
            || !self.heading.is_finite()
            || self.heading.abs() > 180.
            || self.attempted_forward_at_ms > self.stamp.at_ms
            || self.attempted_forward_at_ms != 0
                && self.stamp.at_ms - self.attempted_forward_at_ms > 250
        {
            return Err(SemanticError("invalid motion attempt"));
        }
        let mut bytes = Vec::with_capacity(MOTION_SIZE);
        encode_stamp(self.stamp, &mut bytes);
        bytes.extend_from_slice(&self.world_batch_id.to_be_bytes());
        bytes.extend_from_slice(&self.actor_id.to_be_bytes());
        bytes.extend_from_slice(&self.client_rid.to_be_bytes());
        bytes.extend_from_slice(&0_u16.to_be_bytes());
        bytes.extend_from_slice(&self.entity.to_be_bytes());
        put_point(&mut bytes, self.position);
        put_f64(&mut bytes, self.heading);
        bytes.extend_from_slice(&self.attempted_forward_at_ms.to_be_bytes());
        Ok(bytes)
    }

    pub fn decode(bytes: &[u8]) -> Result<Self, SemanticError> {
        if bytes.len() != MOTION_SIZE || read_u16(bytes, 50) != 0 {
            return Err(SemanticError(
                "invalid motion attempt length or reserved field",
            ));
        }
        let value = Self {
            stamp: decode_stamp(bytes)?,
            world_batch_id: read_u64(bytes, 36),
            actor_id: read_u32(bytes, 44),
            client_rid: read_u16(bytes, 48),
            entity: read_u64(bytes, 52),
            position: read_point(bytes, 60),
            heading: read_f64(bytes, 84),
            attempted_forward_at_ms: read_u64(bytes, 92),
        };
        value.encode()?;
        Ok(value)
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SampledContact {
    pub actor_id: u32,
    pub target_id: u32,
    pub client_rid: u16,
    pub target_client_rid: u16,
    pub source_entity: u64,
    pub target_entity: u64,
    pub sequence: u16,
    pub target_intent: u8,
    pub target_phase: u8,
    pub phase_at_ms: u64,
    pub target_phase_at_ms: f64,
    /// Upper bound when the target state was read; absent in the v1 layout.
    pub target_observed_at_ms: Option<u64>,
    /// Upper bound when the source state was read; present from v3 onward.
    pub source_observed_at_ms: Option<u64>,
    pub occurred_at_ms: f64,
    pub along: f64,
    pub defence_weapon_id: u32,
    pub region: u8,
    pub body_index: i8,
    pub material: i8,
    /// Bits: velocity ready, source airborne, target airborne, v5 guard volume.
    pub flags: u8,
    pub source_position: [f64; 3],
    pub target_position: [f64; 3],
    pub source_velocity: [f64; 3],
    pub target_velocity: [f64; 3],
    pub weapon_velocity: [f64; 3],
    pub position: [f64; 3],
    pub normal: [f64; 3],
    pub direction: [f64; 3],
    pub source_heading: f64,
    pub target_heading: f64,
}

impl SampledContact {
    fn valid(self, through: u64, version: u16) -> bool {
        (1..=crate::MAX_BATTLE_ACTORS as u32).contains(&self.actor_id)
            && self.target_id <= crate::MAX_BATTLE_ACTORS as u32
            && self.actor_id != self.target_id
            && self.client_rid != 0
            && (self.target_id == 0 && self.target_client_rid == 0
                || self.target_id != 0 && self.target_client_rid != 0)
            && self.source_entity != 0
            && (self.target_id == 0 || self.target_entity != 0)
            && self.sequence != 0
            && self.target_intent <= 10
            && self.target_phase <= 9
            && self.flags & !(if version >= 5 { 15 } else { 7 }) == 0
            && (self.flags & 8 == 0
                || (self.target_id != 0
                    && self.defence_weapon_id != 0
                    && self.region == 0
                    && self.body_index == -1
                    && self.target_intent != 9))
            && crate::damage::Region::from_contact_code(
                self.region,
                self.target_id,
                self.defence_weapon_id,
            )
            .is_ok()
            && (-1..=127).contains(&self.body_index)
            && (-1..=7).contains(&self.material)
            && (self.target_id != 0 || self.defence_weapon_id == 0)
            && self.occurred_at_ms.is_finite()
            && self.occurred_at_ms >= 0.
            && self.occurred_at_ms <= through as f64
            && self
                .source_observed_at_ms
                .map_or(self.phase_at_ms as f64 <= self.occurred_at_ms, |at| {
                    self.phase_at_ms <= at && self.occurred_at_ms <= at as f64
                })
            && self.target_phase_at_ms.is_finite()
            && (version >= 4 || self.target_phase_at_ms >= 0.)
            && self
                .target_observed_at_ms
                .map_or(self.target_phase_at_ms <= self.occurred_at_ms, |at| {
                    self.occurred_at_ms <= at as f64 && self.target_phase_at_ms <= at as f64
                })
            && self.along.is_finite()
            && (0. ..=1.).contains(&self.along)
            && self.source_heading.is_finite()
            && self.source_heading.abs() <= 180.
            && self.target_heading.is_finite()
            && self.target_heading.abs() <= 180.
            && [
                self.source_position,
                self.target_position,
                self.source_velocity,
                self.target_velocity,
                self.weapon_velocity,
                self.position,
            ]
            .into_iter()
            .all(point_valid)
            && unit_valid(self.normal)
            && unit_valid(self.direction)
    }

    fn encode(self, bytes: &mut Vec<u8>) {
        bytes.extend_from_slice(&self.actor_id.to_be_bytes());
        bytes.extend_from_slice(&self.target_id.to_be_bytes());
        bytes.extend_from_slice(&self.client_rid.to_be_bytes());
        bytes.extend_from_slice(&self.target_client_rid.to_be_bytes());
        bytes.extend_from_slice(&self.source_entity.to_be_bytes());
        bytes.extend_from_slice(&self.target_entity.to_be_bytes());
        bytes.extend_from_slice(&self.sequence.to_be_bytes());
        bytes.extend_from_slice(&[self.target_intent, self.target_phase]);
        bytes.extend_from_slice(&self.phase_at_ms.to_be_bytes());
        for value in [self.target_phase_at_ms, self.occurred_at_ms, self.along] {
            put_f64(bytes, value);
        }
        bytes.extend_from_slice(&self.defence_weapon_id.to_be_bytes());
        bytes.extend_from_slice(&[
            self.region,
            self.body_index as u8,
            self.material as u8,
            self.flags,
        ]);
        for point in [
            self.source_position,
            self.target_position,
            self.source_velocity,
            self.target_velocity,
            self.weapon_velocity,
            self.position,
            self.normal,
            self.direction,
        ] {
            put_point(bytes, point);
        }
        put_f64(bytes, self.source_heading);
        put_f64(bytes, self.target_heading);
        if let Some(at) = self.target_observed_at_ms {
            bytes.extend_from_slice(&at.to_be_bytes());
        }
        if let Some(at) = self.source_observed_at_ms {
            bytes.extend_from_slice(&at.to_be_bytes());
        }
    }

    fn decode(bytes: &[u8]) -> Self {
        Self {
            actor_id: read_u32(bytes, 0),
            target_id: read_u32(bytes, 4),
            client_rid: read_u16(bytes, 8),
            target_client_rid: read_u16(bytes, 10),
            source_entity: read_u64(bytes, 12),
            target_entity: read_u64(bytes, 20),
            sequence: read_u16(bytes, 28),
            target_intent: bytes[30],
            target_phase: bytes[31],
            phase_at_ms: read_u64(bytes, 32),
            target_phase_at_ms: read_f64(bytes, 40),
            target_observed_at_ms: (bytes.len() >= CONTACT_SIZE + 8)
                .then(|| read_u64(bytes, CONTACT_SIZE)),
            source_observed_at_ms: (bytes.len() == CONTACT_SIZE + 16)
                .then(|| read_u64(bytes, CONTACT_SIZE + 8)),
            occurred_at_ms: read_f64(bytes, 48),
            along: read_f64(bytes, 56),
            defence_weapon_id: read_u32(bytes, 64),
            region: bytes[68],
            body_index: bytes[69] as i8,
            material: bytes[70] as i8,
            flags: bytes[71],
            source_position: read_point(bytes, 72),
            target_position: read_point(bytes, 96),
            source_velocity: read_point(bytes, 120),
            target_velocity: read_point(bytes, 144),
            weapon_velocity: read_point(bytes, 168),
            position: read_point(bytes, 192),
            normal: read_point(bytes, 216),
            direction: read_point(bytes, 240),
            source_heading: read_f64(bytes, 264),
            target_heading: read_f64(bytes, 272),
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct ContactBatch {
    pub version: u16,
    pub stamp: SourceStamp,
    pub world_batch_id: u64,
    pub dropped: u32,
    pub events: Vec<SampledContact>,
}

impl ContactBatch {
    pub fn encode(&self) -> Result<Vec<u8>, SemanticError> {
        if !matches!(self.version, 1..=5)
            || !self.stamp.valid()
            || self.world_batch_id == 0
            || self.dropped != 0
            || self.events.len() > MAX_CONTACTS
        {
            return Err(SemanticError("invalid or incomplete contact batch"));
        }
        if self.events.iter().any(|e| {
            crate::damage::Region::from_contact_code(e.region, e.target_id, e.defence_weapon_id)
                .is_err()
        }) {
            return Err(SemanticError(
                "invalid contact region for target and defence weapon",
            ));
        }
        if self.events.iter().any(|e| {
            !e.valid(self.stamp.at_ms, self.version)
                || e.target_observed_at_ms.is_some() != (self.version >= 2)
                || e.source_observed_at_ms.is_some() != (self.version >= 3)
        }) {
            return Err(SemanticError("invalid or incomplete contact batch"));
        }
        let size = CONTACT_SIZE + usize::from(self.version.min(3) - 1) * 8;
        let mut bytes = Vec::with_capacity(CONTACT_HEADER_SIZE + self.events.len() * size);
        encode_stamp(self.stamp, &mut bytes);
        bytes[..2].copy_from_slice(&self.version.to_be_bytes());
        bytes.extend_from_slice(&self.world_batch_id.to_be_bytes());
        bytes.extend_from_slice(&self.dropped.to_be_bytes());
        bytes.extend_from_slice(&(self.events.len() as u16).to_be_bytes());
        bytes.extend_from_slice(&0_u16.to_be_bytes());
        for event in &self.events {
            event.encode(&mut bytes);
        }
        Ok(bytes)
    }

    pub fn decode(bytes: &[u8]) -> Result<Self, SemanticError> {
        if bytes.len() < CONTACT_HEADER_SIZE || read_u16(bytes, 50) != 0 {
            return Err(SemanticError("invalid contact batch header"));
        }
        let count = usize::from(read_u16(bytes, 48));
        let version = read_u16(bytes, 0);
        if !matches!(version, 1..=5) || read_u16(bytes, 2) != 0 {
            return Err(SemanticError("invalid contact version or reserved field"));
        }
        let size = CONTACT_SIZE + usize::from(version.min(3) - 1) * 8;
        if count > MAX_CONTACTS || bytes.len() != CONTACT_HEADER_SIZE + count * size {
            return Err(SemanticError("invalid contact batch length"));
        }
        let value = Self {
            version,
            stamp: SourceStamp {
                epoch: read_u64(bytes, 4),
                source_id: read_u64(bytes, 12),
                batch_id: read_u64(bytes, 20),
                at_ms: read_u64(bytes, 28),
            },
            world_batch_id: read_u64(bytes, 36),
            dropped: read_u32(bytes, 44),
            events: bytes[CONTACT_HEADER_SIZE..]
                .chunks_exact(size)
                .map(SampledContact::decode)
                .collect(),
        };
        value.encode()?;
        Ok(value)
    }
}
