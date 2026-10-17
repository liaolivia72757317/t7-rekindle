//! Private, additive match ingress on an existing process-local gateway handle.
use crate::gateway::{self, BUSY, EMPTY, INTERNAL_ERROR, INVALID_ARGUMENT, INVALID_STATE};
use std::collections::{BTreeMap, BTreeSet};
use t7r_battle_runtime::MAX_BATTLE_ACTORS;
use t7r_battle_runtime::{
    battle_session::{
        Config, Direction, Intent, Member, MemberKind, Phase, Region, contacts::ContactEvent,
    },
    semantic_adapter::{BoundContact, ContactSample, MotionSample, SourceBinding, SourceStamp},
};

const MAX_SEMANTIC_BYTES: usize = 4096;

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct BattleEquipment {
    pub weapon_id: u32,
    pub slot: u8,
    pub reserved: [u8; 3],
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct BattleMember {
    pub actor_id: u32,
    pub client_rid: u16,
    pub camp: u8,
    pub kind: u8,
    pub hero_id: u32,
    pub weapon_id: u32,
    pub equipment_count: u32,
    pub equipment: [BattleEquipment; 4],
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct BattleConfig {
    pub abi_version: u32,
    pub struct_size: u32,
    pub epoch: u64,
    pub started_at_ms: u64,
    pub duration_ms: u64,
    pub kill_target: u32,
    pub seed: u32,
    pub source_id: u64,
    pub member_count: u32,
    pub reserved: u32,
    pub members: [BattleMember; MAX_BATTLE_ACTORS],
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct BattleMotion {
    pub abi_version: u32,
    pub struct_size: u32,
    pub epoch: u64,
    pub source_id: u64,
    pub batch_id: u64,
    pub world_batch_id: u64,
    pub sampled_at_ms: u64,
    pub actor_id: u32,
    pub client_rid: u16,
    pub reserved: [u8; 2],
    pub entity: u64,
    pub position: [f64; 3],
    pub heading: f64,
    pub attempted_forward_at_ms: u64,
}

impl BattleMotion {
    fn parse(self) -> Result<MotionSample, i32> {
        if self.abi_version != 1
            || self.struct_size != size_of::<Self>() as u32
            || self.reserved != [0; 2]
        {
            return Err(INVALID_ARGUMENT);
        }
        Ok(MotionSample {
            stamp: SourceStamp {
                epoch: self.epoch,
                source_id: self.source_id,
                batch_id: self.batch_id,
                at_ms: self.sampled_at_ms,
            },
            world_batch_id: self.world_batch_id,
            actor_id: self.actor_id,
            client_rid: self.client_rid,
            entity: self.entity,
            position: self.position,
            heading: self.heading,
            attempted_forward_at_ms: self.attempted_forward_at_ms,
        })
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct BattleContact {
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
    pub occurred_at_ms: f64,
    pub along: f64,
    pub defence_weapon_id: u32,
    pub region: u8,
    pub body_index: i8,
    pub material: i8,
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

impl BattleContact {
    fn parse(self) -> Result<BoundContact, i32> {
        if self.flags & !7 != 0 {
            return Err(INVALID_ARGUMENT);
        }
        let direction = |index| match index {
            0 => Direction::Up,
            1 => Direction::Right,
            2 => Direction::Down,
            _ => Direction::Left,
        };
        let target_intent = match self.target_intent {
            0 => None,
            1..=4 => Some(Intent::Attack(direction(self.target_intent - 1))),
            5..=8 => Some(Intent::Guard(direction(self.target_intent - 5))),
            9 => Some(Intent::Shield),
            10 => Some(Intent::Dash),
            _ => return Err(INVALID_ARGUMENT),
        };
        let target_phase = match self.target_phase {
            0 => Phase::Idle,
            1 => Phase::Prepare,
            2 => Phase::Hold,
            3 => Phase::Strike,
            4 => Phase::Recovery,
            5 => Phase::WeaponChange,
            6 => Phase::Parried,
            7 => Phase::Terrain,
            8 => Phase::ParrySuccess,
            9 => Phase::Hurt,
            _ => return Err(INVALID_ARGUMENT),
        };
        let region = Region::from_contact_code(self.region, self.target_id, self.defence_weapon_id)
            .map_err(|_| INVALID_ARGUMENT)?;
        Ok(BoundContact {
            client_rid: self.client_rid,
            target_client_rid: self.target_client_rid,
            event: ContactEvent {
                guard_volume: false,
                actor_id: self.actor_id,
                target_id: self.target_id,
                source_entity: self.source_entity,
                target_entity: self.target_entity,
                sequence: self.sequence,
                phase_at_ms: self.phase_at_ms,
                target_intent,
                target_phase,
                target_phase_at_ms: self.target_phase_at_ms,
                target_observed_at_ms: None,
                source_observed_at_ms: None,
                occurred_at_ms: self.occurred_at_ms,
                along: self.along,
                defence_weapon_id: self.defence_weapon_id,
                region,
                body_index: self.body_index,
                source_position: self.source_position,
                target_position: self.target_position,
                source_heading: self.source_heading,
                target_heading: self.target_heading,
                source_velocity: self.source_velocity,
                target_velocity: self.target_velocity,
                weapon_velocity: self.weapon_velocity,
                velocity_ready: self.flags & 1 != 0,
                airborne: self.flags & 2 != 0,
                target_airborne: self.flags & 4 != 0,
                position: self.position,
                normal: self.normal,
                direction: self.direction,
                material: self.material,
            },
        })
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct BattleContacts {
    pub abi_version: u32,
    pub struct_size: u32,
    pub epoch: u64,
    pub source_id: u64,
    pub batch_id: u64,
    pub world_batch_id: u64,
    pub through_at_ms: u64,
    pub dropped: u32,
    pub count: u32,
    pub events: *const BattleContact,
}

impl BattleContacts {
    /// # Safety
    /// `events` points to `count` readable BattleContact values when count is nonzero.
    unsafe fn parse(self) -> Result<ContactSample, i32> {
        if self.abi_version != 1
            || self.struct_size != size_of::<Self>() as u32
            || self.count > 8192
            || self.count != 0 && self.events.is_null()
        {
            return Err(INVALID_ARGUMENT);
        }
        let events = if self.count == 0 {
            Vec::new()
        } else {
            unsafe { std::slice::from_raw_parts(self.events, self.count as usize) }
                .iter()
                .copied()
                .map(BattleContact::parse)
                .collect::<Result<Vec<_>, _>>()?
        };
        Ok(ContactSample {
            stamp: SourceStamp {
                epoch: self.epoch,
                source_id: self.source_id,
                batch_id: self.batch_id,
                at_ms: self.through_at_ms,
            },
            world_batch_id: self.world_batch_id,
            dropped: self.dropped,
            events,
        })
    }
}

impl Default for BattleConfig {
    fn default() -> Self {
        Self {
            abi_version: 0,
            struct_size: 0,
            epoch: 0,
            started_at_ms: 0,
            duration_ms: 0,
            kill_target: 0,
            seed: 0,
            source_id: 0,
            member_count: 0,
            reserved: 0,
            members: [BattleMember::default(); MAX_BATTLE_ACTORS],
        }
    }
}

impl BattleConfig {
    fn parse(&self) -> Result<(Config, SourceBinding), i32> {
        if self.abi_version != 2
            || self.struct_size != size_of::<Self>() as u32
            || self.epoch == 0
            || self.source_id == 0
            || self.reserved != 0
            || !(1..=MAX_BATTLE_ACTORS as u32).contains(&self.member_count)
        {
            return Err(INVALID_ARGUMENT);
        }
        let mut members = Vec::with_capacity(self.member_count as usize);
        let mut actor_rids = BTreeMap::new();
        let mut used_rids = BTreeSet::new();
        for item in &self.members[..self.member_count as usize] {
            if item.client_rid == 0
                || item.equipment_count > 4
                || !used_rids.insert(item.client_rid)
                || actor_rids.insert(item.actor_id, item.client_rid).is_some()
            {
                return Err(INVALID_ARGUMENT);
            }
            let kind = match item.kind {
                1 => MemberKind::Human,
                2 => MemberKind::Bot,
                _ => return Err(INVALID_ARGUMENT),
            };
            let mut equipment = Vec::with_capacity(item.equipment_count as usize);
            for selected in &item.equipment[..item.equipment_count as usize] {
                if selected.reserved != [0; 3] {
                    return Err(INVALID_ARGUMENT);
                }
                equipment.push(t7r_battle_runtime::battle_session::EquipmentItem {
                    slot: selected.slot,
                    weapon_id: selected.weapon_id,
                });
            }
            members.push(Member {
                actor_id: item.actor_id,
                kind,
                sample_source_id: self.source_id,
                camp: item.camp,
                hero_id: item.hero_id,
                weapon_id: item.weapon_id,
                equipment,
            });
        }
        Ok((
            Config {
                epoch: self.epoch,
                started_at_ms: self.started_at_ms,
                duration_ms: self.duration_ms,
                kill_target: self.kill_target,
                seed: self.seed,
                members,
            },
            SourceBinding {
                epoch: self.epoch,
                source_id: self.source_id,
                actor_rids,
            },
        ))
    }
}

fn status(error: std::io::Error) -> i32 {
    match error.kind() {
        std::io::ErrorKind::WouldBlock => BUSY,
        std::io::ErrorKind::InvalidInput => INVALID_STATE,
        std::io::ErrorKind::InvalidData => INVALID_ARGUMENT,
        _ => INTERNAL_ERROR,
    }
}

/// # Safety
/// `config` points to a readable, aligned BattleConfig. The value is copied
/// during this call and no borrowed pointer is retained.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn t7r_gateway_battle_start(handle: u64, config: *const BattleConfig) -> i32 {
    gateway::guarded(|| {
        if config.is_null() {
            return Err(INVALID_ARGUMENT);
        }
        // Inspect the fixed header before referencing the enlarged ABI 2 array.
        let header = config.cast::<u32>();
        if unsafe { header.read() } != 2
            || unsafe { header.add(1).read() } != size_of::<BattleConfig>() as u32
        {
            return Err(INVALID_ARGUMENT);
        }
        let (config, binding) = unsafe { &*config }.parse()?;
        gateway::with_gateway(handle, |gateway| {
            gateway.start_battle(config, binding).map_err(status)
        })
    })
}

/// # Safety
/// `config` points to a readable, aligned BattleConfig. The match clock is
/// activated by the bound instance only when the client enters GAME.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn t7r_gateway_battle_start_staged(
    handle: u64,
    config: *const BattleConfig,
) -> i32 {
    gateway::guarded(|| {
        if config.is_null() {
            return Err(INVALID_ARGUMENT);
        }
        // Inspect the fixed header before referencing the enlarged ABI 2 array.
        let header = config.cast::<u32>();
        if unsafe { header.read() } != 2
            || unsafe { header.add(1).read() } != size_of::<BattleConfig>() as u32
        {
            return Err(INVALID_ARGUMENT);
        }
        let (config, binding) = unsafe { &*config }.parse()?;
        gateway::with_gateway(handle, |gateway| {
            gateway.start_battle_staged(config, binding).map_err(status)
        })
    })
}

/// # Safety
/// `clock_ms` points to a writable, aligned u64. The battle clock is valid
/// only while this match owner is running.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn t7r_gateway_battle_clock(handle: u64, clock_ms: *mut u64) -> i32 {
    gateway::guarded(|| {
        if clock_ms.is_null() {
            return Err(INVALID_ARGUMENT);
        }
        unsafe {
            *clock_ms = 0;
        }
        gateway::with_gateway(handle, |gateway| {
            let value = gateway.battle_clock_ms().map_err(|error| {
                if error.kind() == std::io::ErrorKind::WouldBlock {
                    EMPTY
                } else {
                    status(error)
                }
            })?;
            unsafe {
                *clock_ms = value;
            }
            Ok(())
        })
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn t7r_gateway_battle_bind_client_clock(
    handle: u64,
    epoch: u64,
    offset_ms: i64,
) -> i32 {
    gateway::guarded(|| {
        gateway::with_gateway(handle, |gateway| {
            gateway
                .bind_battle_client_clock(epoch, offset_ms)
                .map_err(status)
        })
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn t7r_gateway_battle_bind_server_clock(
    handle: u64,
    epoch: u64,
    offset_ms: i64,
) -> i32 {
    gateway::guarded(|| {
        gateway::with_gateway(handle, |gateway| {
            gateway
                .bind_battle_server_clock(epoch, offset_ms)
                .map_err(status)
        })
    })
}

/// # Safety
/// `payload` is readable for `length` bytes. Kind 6 is WorldSample; kind 11
/// is PlayerIntent; kind 14 is ContactBatch (v1/v2/v3). The buffer is borrowed only for this call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn t7r_gateway_battle_submit(
    handle: u64,
    kind: u32,
    payload: *const u8,
    length: u32,
) -> i32 {
    gateway::guarded(|| {
        let limit = if kind == 14 {
            52 + 8192 * 296
        } else {
            MAX_SEMANTIC_BYTES
        };
        if payload.is_null() || length == 0 || length as usize > limit {
            return Err(INVALID_ARGUMENT);
        }
        let bytes = unsafe { std::slice::from_raw_parts(payload, length as usize) };
        gateway::with_gateway(handle, |gateway| match kind {
            6 => gateway.submit_battle_world(bytes).map_err(status),
            11 => gateway.submit_battle_player(bytes).map_err(status),
            14 => gateway.submit_battle_contact_payload(bytes).map_err(status),
            _ => Err(INVALID_ARGUMENT),
        })
    })
}

/// # Safety
/// `sample` points to a readable, aligned BattleMotion copied during this call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn t7r_gateway_battle_submit_motion(
    handle: u64,
    sample: *const BattleMotion,
) -> i32 {
    gateway::guarded(|| {
        if sample.is_null() {
            return Err(INVALID_ARGUMENT);
        }
        let sample = unsafe { *sample }.parse()?;
        gateway::with_gateway(handle, |gateway| {
            gateway.submit_battle_motion(sample).map_err(status)
        })
    })
}

/// # Safety
/// `sample` points to a readable, aligned BattleContacts, and its `events`
/// points to `count` readable BattleContact values when count is nonzero.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn t7r_gateway_battle_submit_contacts(
    handle: u64,
    sample: *const BattleContacts,
) -> i32 {
    gateway::guarded(|| {
        if sample.is_null() {
            return Err(INVALID_ARGUMENT);
        }
        let sample = unsafe { (*sample).parse() }?;
        gateway::with_gateway(handle, |gateway| {
            gateway.submit_battle_contacts(sample).map_err(status)
        })
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn t7r_gateway_battle_dispatch_next(handle: u64) -> i32 {
    gateway::guarded(|| {
        gateway::with_gateway(handle, |gateway| {
            match gateway.dispatch_next_battle_output().map_err(status)? {
                Some(_) => Ok(()),
                None => Err(EMPTY),
            }
        })
    })
}

/// # Safety
/// `returned` points to a writable, aligned u32 for the duration of this call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn t7r_gateway_battle_returned_to_room(
    handle: u64,
    returned: *mut u32,
) -> i32 {
    gateway::guarded(|| {
        if returned.is_null() {
            return Err(INVALID_ARGUMENT);
        }
        unsafe {
            *returned = 0;
        }
        gateway::with_gateway(handle, |gateway| {
            let value = gateway.battle_returned_to_room().map_err(status)?;
            unsafe {
                *returned = u32::from(value);
            }
            Ok(())
        })
    })
}

/// # Safety
/// `open` points to a writable, aligned u32 for the duration of this call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn t7r_gateway_battle_preparation_open(handle: u64, open: *mut u32) -> i32 {
    gateway::guarded(|| {
        if open.is_null() {
            return Err(INVALID_ARGUMENT);
        }
        unsafe {
            *open = 0;
        }
        gateway::with_gateway(handle, |gateway| {
            let value = gateway.battle_preparation_open().map_err(status)?;
            unsafe {
                *open = u32::from(value);
            }
            Ok(())
        })
    })
}

/// # Safety
/// `reserved` points to a writable, aligned u32 for the duration of this call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn t7r_gateway_battle_reserve_rearm(handle: u64, reserved: *mut u32) -> i32 {
    gateway::guarded(|| {
        if reserved.is_null() {
            return Err(INVALID_ARGUMENT);
        }
        unsafe {
            *reserved = 0;
        }
        gateway::with_gateway(handle, |gateway| {
            let value = gateway.reserve_battle_rearm().map_err(status)?;
            unsafe {
                *reserved = u32::from(value);
            }
            Ok(())
        })
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn t7r_gateway_battle_stop(handle: u64) -> i32 {
    gateway::guarded(|| {
        gateway::with_gateway(handle, |gateway| gateway.stop_battle().map_err(status))
    })
}

#[cfg(test)]
mod backpressure_tests {
    use super::*;
    #[test]
    fn fixed_contact_abi_rejects_v5_guard_volume_flag() {
        // Every field is numeric; the fixed ABI has no versioned extension slot.
        let mut event: BattleContact = unsafe { std::mem::zeroed() };
        event.flags = 8;
        assert!(matches!(event.parse(), Err(INVALID_ARGUMENT)));
    }
    #[test]
    fn only_uncommitted_would_block_is_retryable() {
        use std::io::{Error, ErrorKind};
        assert_eq!(status(Error::from(ErrorKind::WouldBlock)), BUSY);
        assert_eq!(status(Error::from(ErrorKind::TimedOut)), INTERNAL_ERROR);
        assert_eq!(status(Error::from(ErrorKind::BrokenPipe)), INTERNAL_ERROR);
        assert_eq!(
            status(Error::from(ErrorKind::InvalidData)),
            INVALID_ARGUMENT
        );
        assert_eq!(status(Error::from(ErrorKind::InvalidInput)), INVALID_STATE);
    }
}
