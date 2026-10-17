//! Pure boundary conversion between public semantic payloads and the match owner.
use crate::battle_session::{
    EndReason, Input, Phase, Pose, Region, Settlement, SourceWorld,
    contacts::{ContactBatch, ContactEvent},
};
use std::{collections::BTreeMap, io, sync::Arc};
pub use t7r_battle_core::battle_semantics::SourceStamp;
use t7r_battle_core::{
    action::{Command, Direction, Intent},
    ai::{ContactObservation, ContactState, MotionAttempt},
    battle_semantics::{MatchEnded, PlayerIntent, ResultMember, WorldSample},
};

#[derive(Clone)]
pub struct SourceBinding {
    pub epoch: u64,
    pub source_id: u64,
    /// Match actor ID to the original-client RID assigned on this connection.
    pub actor_rids: BTreeMap<u32, u16>,
}

pub struct BoundInput {
    pub stamp: SourceStamp,
    pub input: Input,
}

#[derive(Clone)]
pub struct MotionSample {
    pub stamp: SourceStamp,
    pub world_batch_id: u64,
    pub actor_id: u32,
    pub client_rid: u16,
    pub entity: u64,
    pub position: [f64; 3],
    pub heading: f64,
    pub attempted_forward_at_ms: u64,
}

#[derive(Clone)]
pub struct BoundContact {
    pub client_rid: u16,
    pub target_client_rid: u16,
    pub event: ContactEvent,
}

#[derive(Clone)]
pub struct ContactSample {
    pub stamp: SourceStamp,
    pub world_batch_id: u64,
    pub dropped: u32,
    pub events: Vec<BoundContact>,
}

impl SourceBinding {
    fn check(&self, stamp: SourceStamp, last_batch: u64) -> io::Result<()> {
        if stamp.epoch != self.epoch
            || stamp.source_id != self.source_id
            || stamp.batch_id <= last_batch
        {
            return Err(invalid("stale or unbound semantic input"));
        }
        Ok(())
    }
    fn check_actor(&self, actor_id: u32, client_rid: u16) -> io::Result<()> {
        if self.actor_rids.get(&actor_id) != Some(&client_rid) {
            return Err(invalid("unbound actor or client RID"));
        }
        Ok(())
    }
}

pub fn world_input(
    payload: &[u8],
    binding: &SourceBinding,
    last_batch: u64,
) -> io::Result<BoundInput> {
    let sample = WorldSample::decode(payload).map_err(invalid)?;
    binding.check(sample.stamp, last_batch)?;
    let mut actors = BTreeMap::new();
    for actor in sample.actors {
        binding.check_actor(actor.actor_id, actor.client_rid)?;
        let state = match actor.contact_state {
            0 => ContactState::Unavailable,
            1 => ContactState::Blocked,
            2 => ContactState::Clear,
            _ => unreachable!("semantic codec validates contact state"),
        };
        actors.insert(
            actor.actor_id,
            Pose {
                entity: actor.entity,
                position: actor.position,
                heading: actor.heading,
                sequence: actor.sequence,
                contact: ContactObservation {
                    target_id: (actor.contact_target_id != 0).then_some(actor.contact_target_id),
                    state,
                },
            },
        );
    }
    Ok(BoundInput {
        stamp: sample.stamp,
        input: Input::SourceWorld(Arc::new(SourceWorld {
            epoch: sample.stamp.epoch,
            source_id: sample.stamp.source_id,
            batch_id: sample.stamp.batch_id,
            sampled_at_ms: sample.stamp.at_ms,
            actors,
        })),
    })
}

pub fn player_input(
    payload: &[u8],
    binding: &SourceBinding,
    last_batch: u64,
) -> io::Result<BoundInput> {
    let value = PlayerIntent::decode(payload).map_err(invalid)?;
    binding.check(value.stamp, last_batch)?;
    binding.check_actor(value.actor_id, value.client_rid)?;
    let direction = |index| match index {
        0 => Direction::Up,
        1 => Direction::Right,
        2 => Direction::Down,
        _ => Direction::Left,
    };
    let command = match value.command {
        1..=4 => Command::Press {
            intent: Intent::Attack(direction(value.command - 1)),
            defence_down: value.flags & 1 != 0,
            dash_run: value.flags & 2 != 0,
            dash_forward: value.flags & 4 != 0,
        },
        5..=8 => Command::Press {
            intent: Intent::Guard(direction(value.command - 5)),
            defence_down: value.flags & 1 != 0,
            dash_run: value.flags & 2 != 0,
            dash_forward: value.flags & 4 != 0,
        },
        9 | 10 => Command::Press {
            intent: if value.command == 9 {
                Intent::Shield
            } else {
                Intent::Dash
            },
            defence_down: value.flags & 1 != 0,
            dash_run: value.flags & 2 != 0,
            dash_forward: value.flags & 4 != 0,
        },
        11 => Command::ReleaseAttack,
        12 => Command::ReleaseGuard,
        _ => unreachable!("semantic codec validates command"),
    };
    Ok(BoundInput {
        stamp: value.stamp,
        input: Input::PlayerAction {
            stamp: value.stamp,
            actor_id: value.actor_id,
            command,
        },
    })
}

pub fn motion_input(
    sample: MotionSample,
    binding: &SourceBinding,
    last_batch: u64,
    world_batch: u64,
) -> io::Result<BoundInput> {
    binding.check(sample.stamp, last_batch)?;
    binding.check_actor(sample.actor_id, sample.client_rid)?;
    if sample.world_batch_id == 0
        || sample.world_batch_id != world_batch
        || sample.attempted_forward_at_ms > sample.stamp.at_ms
        || sample.attempted_forward_at_ms != 0
            && sample.stamp.at_ms - sample.attempted_forward_at_ms > 250
    {
        return Err(invalid(
            "motion attempt is not bound to a fresh world batch",
        ));
    }
    Ok(BoundInput {
        stamp: sample.stamp,
        input: Input::SourceMotionAttempt {
            source_id: sample.stamp.source_id,
            attempt: MotionAttempt {
                actor_id: sample.actor_id,
                entity: sample.entity,
                sampled_at_ms: sample.stamp.at_ms,
                position: sample.position,
                heading: sample.heading,
                attempted_forward: sample.attempted_forward_at_ms != 0,
            },
        },
    })
}

pub fn contacts_input(
    sample: ContactSample,
    binding: &SourceBinding,
    last_batch: u64,
    world_batch: u64,
) -> io::Result<BoundInput> {
    binding.check(sample.stamp, last_batch)?;
    if sample.world_batch_id == 0
        || sample.world_batch_id != world_batch
        || sample.dropped != 0
        || sample.events.len() > 8192
    {
        return Err(invalid("invalid or incomplete contact batch"));
    }
    let mut events = Vec::with_capacity(sample.events.len());
    for contact in sample.events {
        binding.check_actor(contact.event.actor_id, contact.client_rid)?;
        if contact.event.target_id == 0 {
            if contact.target_client_rid != 0 {
                return Err(invalid("ground contact has a target RID"));
            }
        } else {
            binding.check_actor(contact.event.target_id, contact.target_client_rid)?;
        }
        events.push(contact.event);
    }
    Ok(BoundInput {
        stamp: sample.stamp,
        input: Input::SourceContacts {
            source_id: sample.stamp.source_id,
            batch: ContactBatch {
                epoch: sample.stamp.epoch,
                batch_id: sample.stamp.batch_id,
                through_at_ms: sample.stamp.at_ms as f64,
                dropped: 0,
                events,
            },
        },
    })
}

pub fn ended_payload(settlement: &Settlement) -> io::Result<Vec<u8>> {
    MatchEnded {
        epoch: settlement.epoch,
        started_at_ms: settlement.started_at_ms,
        ended_at_ms: settlement.ended_at_ms,
        reason: match settlement.reason {
            EndReason::Quit => 1,
            EndReason::Timeout => 2,
            EndReason::KillTarget => 3,
        },
        result: settlement.result,
        camp_kills: settlement.camp_kills,
        members: settlement
            .members
            .iter()
            .map(|member| ResultMember {
                actor_id: member.actor_id,
                camp: member.camp,
                result: member.result,
                rank: member.rank,
                kills: member.score.kills,
                deaths: member.score.deaths,
                points: member.score.points,
                streak: member.score.streak,
            })
            .collect(),
    }
    .encode()
    .map_err(invalid)
}

fn invalid(error: impl std::fmt::Display) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, error.to_string())
}

impl ContactSample {
    pub fn decode(payload: &[u8]) -> io::Result<Self> {
        let batch = t7r_battle_core::battle_semantics::evidence::ContactBatch::decode(payload)
            .map_err(|error| {
                if payload.len() >= 52 {
                    let batch_id =
                        u64::from_be_bytes(payload[20..28].try_into().expect("checked header"));
                    invalid(format!("contact batchId={batch_id}: {error}"))
                } else {
                    invalid(error)
                }
            })?;
        Ok(Self {
            stamp: batch.stamp,
            world_batch_id: batch.world_batch_id,
            dropped: batch.dropped,
            events: batch
                .events
                .into_iter()
                .map(bound_contact)
                .collect::<io::Result<_>>()?,
        })
    }
}

fn bound_contact(
    value: t7r_battle_core::battle_semantics::evidence::SampledContact,
) -> io::Result<BoundContact> {
    let direction = |index| match index {
        0 => Direction::Up,
        1 => Direction::Right,
        2 => Direction::Down,
        _ => Direction::Left,
    };
    let target_intent = match value.target_intent {
        0 => None,
        1..=4 => Some(Intent::Attack(direction(value.target_intent - 1))),
        5..=8 => Some(Intent::Guard(direction(value.target_intent - 5))),
        9 => Some(Intent::Shield),
        10 => Some(Intent::Dash),
        _ => return Err(invalid("invalid contact enum")),
    };
    let target_phase = match value.target_phase {
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
        _ => return Err(invalid("invalid contact enum")),
    };
    let region = Region::from_contact_code(value.region, value.target_id, value.defence_weapon_id)
        .map_err(invalid)?;
    Ok(BoundContact {
        client_rid: value.client_rid,
        target_client_rid: value.target_client_rid,
        event: ContactEvent {
            guard_volume: value.flags & 8 != 0,
            actor_id: value.actor_id,
            target_id: value.target_id,
            source_entity: value.source_entity,
            target_entity: value.target_entity,
            sequence: value.sequence,
            phase_at_ms: value.phase_at_ms,
            target_intent,
            target_phase,
            target_phase_at_ms: value.target_phase_at_ms,
            target_observed_at_ms: value.target_observed_at_ms,
            source_observed_at_ms: value.source_observed_at_ms,
            occurred_at_ms: value.occurred_at_ms,
            along: value.along,
            defence_weapon_id: value.defence_weapon_id,
            region,
            body_index: value.body_index,
            source_position: value.source_position,
            target_position: value.target_position,
            source_heading: value.source_heading,
            target_heading: value.target_heading,
            source_velocity: value.source_velocity,
            target_velocity: value.target_velocity,
            weapon_velocity: value.weapon_velocity,
            velocity_ready: value.flags & 1 != 0,
            airborne: value.flags & 2 != 0,
            target_airborne: value.flags & 4 != 0,
            position: value.position,
            normal: value.normal,
            direction: value.direction,
            material: value.material,
        },
    })
}
