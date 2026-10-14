//! Resolve reported native blade contacts against server-owned actions and lives.
use crate::{battle::Battle, client_view::ActorId, motion::Motion, protocol::ErrorCode};
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;

pub const MAX_HEALTH: u16 = 100;
pub const RESPAWN_MS: u64 = 8000;
const DAMAGE: u16 = 20;
const REPORT_GRACE_MS: u64 = 600;
const WEAPON_ID: i32 = 1030411;

#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ContactReport {
    pub action_seq: u16,
    pub state: u16,
    pub elapsed_ms: u16,
    // Zero is terrain; the sender's actor is always inferred from its connection.
    pub target_actor_id: u32,
    pub position: [f32; 3],
    pub normal: [f32; 3],
    pub direction: [f32; 3],
    pub material: i8,
    #[serde(default)]
    pub weapon_contact: bool,
}

impl ContactReport {
    fn valid(&self) -> bool {
        self.action_seq > 0
            && (-1..=7).contains(&self.material)
            && self
                .position
                .iter()
                .all(|v| v.is_finite() && v.abs() < 100_000.)
            && [&self.normal, &self.direction].iter().all(|v| {
                let length = v.iter().map(|n| n * n).sum::<f32>().sqrt();
                length.is_finite() && (0.9..=1.1).contains(&length)
            })
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Health {
    pub actor_id: ActorId,
    pub health: u16,
    pub kills: u32,
    pub deaths: u32,
    pub streak: u32,
}

#[derive(Clone, Debug, Serialize)]
#[serde(
    tag = "type",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub enum CombatEffect {
    Contact {
        attacker: ActorId,
        target: Option<ActorId>,
        action_seq: u16,
        weapon_id: i32,
        parried: bool,
        damage: u16,
        position: [f32; 3],
        normal: [f32; 3],
        direction: [f32; 3],
        material: i8,
        lives: Vec<Health>,
    },
    RespawnReady {
        actor_id: ActorId,
    },
    Respawned {
        actor_id: ActorId,
        lives: Vec<Health>,
    },
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CombatEvent {
    pub room_id: String,
    pub match_id: String,
    pub event_seq: u64,
    pub server_time_ms: u64,
    pub effect: CombatEffect,
}

#[derive(Debug)]
enum Life {
    Alive(u16),
    Dead { until: u64, ready: bool },
}
#[derive(Debug)]
struct Fighter {
    id: u32,
    camp: u8,
    life: Life,
    input_seq: u32,
    kills: u32,
    deaths: u32,
    streak: u32,
}
#[derive(Debug)]
struct SwingHits {
    actor: u32,
    seq: u16,
    targets: Vec<u32>,
    stopped: bool,
}

#[derive(Debug)]
pub(crate) struct Combat {
    room_id: String,
    match_id: String,
    event_seq: u64,
    fighters: Vec<Fighter>,
    hits: VecDeque<SwingHits>,
    pending: Vec<CombatEvent>,
}

impl Combat {
    pub(crate) fn new(
        room_id: String,
        match_id: String,
        actors: impl Iterator<Item = (u32, u8)>,
    ) -> Self {
        Self {
            room_id,
            match_id,
            event_seq: 0,
            fighters: actors
                .map(|(id, camp)| Fighter {
                    id,
                    camp,
                    life: Life::Alive(MAX_HEALTH),
                    input_seq: 0,
                    kills: 0,
                    deaths: 0,
                    streak: 0,
                })
                .collect(),
            hits: VecDeque::new(),
            pending: Vec::new(),
        }
    }
    pub(crate) fn alive(&self, id: u32) -> bool {
        self.fighters
            .iter()
            .any(|f| f.id == id && matches!(f.life, Life::Alive(_)))
    }
    fn health(&self) -> Vec<Health> {
        self.fighters
            .iter()
            .map(|f| Health {
                actor_id: ActorId(f.id),
                health: match f.life {
                    Life::Alive(hp) => hp,
                    Life::Dead { .. } => 0,
                },
                kills: f.kills,
                deaths: f.deaths,
                streak: f.streak,
            })
            .collect()
    }
    fn publish(&mut self, effect: CombatEffect, now: u64) {
        self.event_seq += 1;
        self.pending.push(CombatEvent {
            room_id: self.room_id.clone(),
            match_id: self.match_id.clone(),
            event_seq: self.event_seq,
            server_time_ms: now,
            effect,
        });
    }
    pub(crate) fn input(
        &mut self,
        actor: u32,
        input_seq: u32,
        report: ContactReport,
        now: u64,
        battle: &mut Battle,
        motion: &Motion,
    ) -> Result<bool, ErrorCode> {
        let sender = self
            .fighters
            .iter_mut()
            .find(|f| f.id == actor)
            .ok_or(ErrorCode::UnknownActor)?;
        if input_seq == 0 || input_seq <= sender.input_seq {
            return Err(ErrorCode::StaleInput);
        }
        sender.input_seq = input_seq;
        if !report.valid()
            || !self.alive(actor)
            || report.target_actor_id == actor
            || report.weapon_contact && report.target_actor_id == 0
        {
            return Ok(false);
        }
        if report.target_actor_id != 0
            && self
                .fighters
                .iter()
                .find(|f| f.id == actor)
                .zip(
                    self.fighters
                        .iter()
                        .find(|f| f.id == report.target_actor_id),
                )
                .is_some_and(|(source, target)| source.camp == target.camp)
        {
            return Ok(false);
        }
        let Some(action) = battle.sample(actor, report.action_seq) else {
            return Ok(false);
        };
        let Some(direction) = [238, 244, 250, 256]
            .iter()
            .position(|state| *state == action.state)
        else {
            return Ok(false);
        };
        let duration = if direction == 2 { 700 } else { 640 };
        if action.state != report.state
            || u64::from(report.elapsed_ms) > duration - 100
            || now < action.at
            || now - action.at > duration + REPORT_GRACE_MS
            || action
                .ended_at
                .is_some_and(|at| now.saturating_sub(at) > REPORT_GRACE_MS)
        {
            return Ok(false);
        }
        let Some(source) = motion.latest(actor, now) else {
            return Ok(false);
        };
        if distance(&source.position, &report.position) > 5.0 {
            return Ok(false);
        }
        let target_id = report.target_actor_id;
        let mut parried = false;
        let mut guard_direction = None;
        if target_id != 0 {
            if !self.alive(target_id) {
                return Ok(false);
            }
            let Some(target) = motion.latest(target_id, now) else {
                return Ok(false);
            };
            // The two endpoints may render a 100ms-old pose. These coarse bounds
            // supplement the native blade report; map geometry is not yet authoritative.
            if distance(&source.position, &target.position) > 4.0
                || (source.position[2] - target.position[2]).abs() > 2.0
                || distance(&target.position, &report.position) > 3.0
                || !facing(source.position, target.position, source.heading, 0.0)
            {
                return Ok(false);
            }
            if let Some(guard) = battle.current(target_id) {
                guard_direction = [223, 224, 225, 226]
                    .iter()
                    .position(|state| *state == guard.state);
                let matches = match direction {
                    0 => guard_direction == Some(0),
                    2 => guard_direction == Some(2),
                    _ => matches!(guard_direction, Some(1 | 3)),
                };
                parried = matches && facing(target.position, source.position, target.heading, 0.5);
            }
        }
        if report.weapon_contact && !parried {
            return Ok(false);
        }
        let index = if let Some(i) = self
            .hits
            .iter()
            .position(|s| s.actor == actor && s.seq == report.action_seq)
        {
            i
        } else {
            if self.hits.len() == crate::limits::MAX_SWING_HISTORY {
                self.hits.pop_front();
            }
            self.hits.push_back(SwingHits {
                actor,
                seq: report.action_seq,
                targets: Vec::new(),
                stopped: false,
            });
            self.hits.len() - 1
        };
        let hits = &mut self.hits[index];
        if hits.stopped || hits.targets.contains(&target_id) {
            return Ok(false);
        }
        hits.targets.push(target_id);
        let mut damage = 0;
        if target_id == 0 {
            // Ground ray normals may be reversed. The contact plane must also
            // pass near the source's feet; a ceiling is still an obstruction.
            if !ground(&report, &source.position) {
                hits.stopped = true;
                battle.react(
                    actor,
                    [466, 468, 467, 469][direction],
                    now,
                    400,
                    false,
                    Some(target_id),
                );
            }
        } else if parried {
            hits.stopped = true;
            battle.react(
                actor,
                [240, 246, 252, 258][direction],
                now,
                400,
                false,
                Some(target_id),
            );
            battle.react(
                target_id,
                [231, 232, 233, 234][guard_direction.expect("matched guard")],
                now,
                100,
                false,
                Some(actor),
            );
        } else {
            let target = self
                .fighters
                .iter_mut()
                .find(|f| f.id == target_id)
                .expect("validated target");
            let Life::Alive(hp) = &mut target.life else {
                unreachable!()
            };
            damage = DAMAGE.min(*hp);
            *hp -= damage;
            if *hp == 0 {
                target.life = Life::Dead {
                    until: now + RESPAWN_MS,
                    ready: false,
                };
                target.deaths = target.deaths.saturating_add(1).min(i32::MAX as u32);
                target.streak = 0;
                let sender = self
                    .fighters
                    .iter_mut()
                    .find(|f| f.id == actor)
                    .expect("validated attacker");
                sender.kills = sender.kills.saturating_add(1).min(i32::MAX as u32);
                sender.streak = sender.streak.saturating_add(1).min(i32::MAX as u32);
                battle.react(target_id, 42, now, 0, true, Some(actor));
            } else {
                battle.react(target_id, 35, now, 400, false, Some(actor));
            }
        }
        self.publish(
            CombatEffect::Contact {
                attacker: ActorId(actor),
                target: (target_id != 0).then_some(ActorId(target_id)),
                action_seq: report.action_seq,
                weapon_id: WEAPON_ID,
                parried,
                damage,
                position: report.position,
                normal: report.normal,
                direction: report.direction,
                material: report.material,
                lives: self.health(),
            },
            now,
        );
        Ok(true)
    }
    pub(crate) fn advance(&mut self, now: u64) {
        let mut ready = Vec::new();
        for fighter in &mut self.fighters {
            if let Life::Dead {
                until,
                ready: published,
            } = &mut fighter.life
                && now >= *until
                && !*published
            {
                *published = true;
                ready.push(fighter.id);
            }
        }
        for id in ready {
            self.publish(
                CombatEffect::RespawnReady {
                    actor_id: ActorId(id),
                },
                now,
            );
        }
    }
    pub(crate) fn respawn(
        &mut self,
        actor: u32,
        now: u64,
        battle: &mut Battle,
        motion: &mut Motion,
    ) -> Result<(), ErrorCode> {
        let fighter = self
            .fighters
            .iter_mut()
            .find(|f| f.id == actor)
            .ok_or(ErrorCode::UnknownActor)?;
        if !matches!(fighter.life, Life::Dead { until, ready: true } if now >= until) {
            return Err(ErrorCode::WrongPhase);
        }
        fighter.life = Life::Alive(MAX_HEALTH);
        self.hits.retain(|s| s.actor != actor);
        motion.reset(actor);
        battle.react(actor, 2, now, 0, false, None);
        self.publish(
            CombatEffect::Respawned {
                actor_id: ActorId(actor),
                lives: self.health(),
            },
            now,
        );
        Ok(())
    }
    pub(crate) fn take_events(&mut self) -> Vec<CombatEvent> {
        std::mem::take(&mut self.pending)
    }
}
fn distance(a: &[f32; 3], b: &[f32; 3]) -> f32 {
    a.iter()
        .zip(b)
        .map(|(a, b)| (a - b) * (a - b))
        .sum::<f32>()
        .sqrt()
}
fn ground(report: &ContactReport, source: &[f32; 3]) -> bool {
    report.normal[2].abs() >= 0.5
        && report
            .normal
            .iter()
            .zip(report.position.iter().zip(source))
            .map(|(n, (p, s))| n * (p - s))
            .sum::<f32>()
            / report.normal[2]
            <= 0.1
}
fn facing(source: [f32; 3], target: [f32; 3], heading: i16, limit: f32) -> bool {
    let dx = target[0] - source[0];
    let dy = target[1] - source[1];
    let len = dx.hypot(dy);
    let yaw = f32::from(heading).to_radians();
    len < 0.01 || (dx * yaw.cos() - dy * yaw.sin()) / len >= limit
}
