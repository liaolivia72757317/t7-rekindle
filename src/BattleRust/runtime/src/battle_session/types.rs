use super::profiles::ActorResources;
use std::collections::BTreeMap;
use std::sync::Arc;
use t7r_battle_core::{
    action, ai, damage,
    navigation::{Point, SpawnReference},
};

#[derive(Clone, Debug)]
pub struct Member {
    pub actor_id: u32,
    pub kind: MemberKind,
    pub sample_source_id: u64,
    pub camp: u8,
    pub hero_id: u32,
    pub weapon_id: u32,
    pub equipment: Vec<action::EquipmentItem>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MemberKind {
    Human,
    Bot,
}

#[derive(Clone, Debug)]
pub struct Config {
    pub epoch: u64,
    pub started_at_ms: u64,
    pub duration_ms: u64,
    pub kill_target: u32,
    pub seed: u32,
    pub members: Vec<Member>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Pose {
    pub entity: u64,
    pub position: Point,
    pub heading: f64,
    pub sequence: u16,
    pub contact: ai::ContactObservation,
}

#[derive(Clone, Debug, PartialEq)]
pub struct World {
    pub epoch: u64,
    pub sampled_at_ms: u64,
    pub actors: BTreeMap<u32, Pose>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct SourceWorld {
    pub epoch: u64,
    pub source_id: u64,
    pub batch_id: u64,
    pub sampled_at_ms: u64,
    pub actors: BTreeMap<u32, Pose>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Life {
    Alive,
    Dead {
        died_at_ms: u64,
        respawn_at_ms: u64,
    },
    Selecting {
        died_at_ms: u64,
    },
    Waiting {
        died_at_ms: u64,
    },
    Spawning {
        spawned_at_ms: u64,
        spawn: SpawnReference,
    },
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Score {
    pub kills: u32,
    pub deaths: u32,
    pub points: u32,
    pub streak: u32,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ActorSnapshot {
    pub actor_id: u32,
    pub camp: u8,
    pub hero_id: u32,
    pub health: u32,
    pub guard: f64,
    pub life: Life,
    pub score: Score,
    pub action: action::ActionSnapshot,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Buff {
    pub since_ms: f64,
    pub until_ms: f64,
    pub multiplier: f64,
}

#[derive(Clone, Debug)]
pub(crate) struct Swing {
    pub sequence: u16,
    pub intent: action::Intent,
    pub weapon_id: u32,
    pub began_at_ms: u64,
    pub held_ms: u64,
    pub start_ms: u16,
    pub end_ms: u16,
    pub closed_at_ms: Option<u64>,
    pub cutoff_ms: Option<f64>,
    pub hits: Vec<u32>,
    pub allies: u32,
    pub enemies: usize,
    pub stopped: bool,
    pub ground_played: bool,
    pub buffs: BTreeMap<&'static str, Buff>,
}

#[derive(Clone)]
pub(crate) struct Actor {
    pub member: Member,
    pub resources: Arc<ActorResources>,
    pub action: action::ActionState,
    pub health: u32,
    pub life: Life,
    pub contact_life_at_ms: u64,
    pub last_attacked_at_ms: Option<u64>,
    pub score: Score,
    pub shields: BTreeMap<u32, u32>,
    pub buffs: BTreeMap<&'static str, Buff>,
    pub swings: Vec<Swing>,
    pub release_at_ms: Option<u64>,
}

impl Actor {
    pub fn snapshot(&self, guard: f64) -> ActorSnapshot {
        ActorSnapshot {
            actor_id: self.member.actor_id,
            camp: self.member.camp,
            hero_id: self.member.hero_id,
            health: self.health,
            guard,
            life: self.life,
            score: self.score,
            action: self.action.snapshot(),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EndReason {
    Quit,
    Timeout,
    KillTarget,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RankedScore {
    pub actor_id: u32,
    pub camp: u8,
    pub rank: u32,
    pub result: u8,
    pub score: Score,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Settlement {
    pub epoch: u64,
    pub started_at_ms: u64,
    pub ended_at_ms: u64,
    pub reason: EndReason,
    pub result: u8,
    pub camp_kills: [u32; 2],
    pub members: Vec<RankedScore>,
}

#[derive(Clone, Debug, PartialEq)]
pub enum Output {
    Training(super::training::Snapshot),
    Squad {
        actor_id: u32,
        command: super::SquadCommand,
        result: i32,
        previous: Arc<super::SquadRoster>,
        roster: Arc<super::SquadRoster>,
    },
    SpawnRejected {
        actor_id: u32,
        result: i32,
    },
    State(t7r_battle_core::battle_semantics::state::MatchState),
    Action(action::ActionEvent),
    Bot(ai::BotOutput),
    Damage {
        attacker: u32,
        target: u32,
        sequence: u16,
        health: u32,
        previous_health: u32,
        weapon_id: u32,
        evidence: Box<super::contacts::ContactEvent>,
        guard_break: bool,
        result: damage::DamageResult,
    },
    Guard {
        actor_id: u32,
        value: f64,
    },
    Shield {
        actor_id: u32,
        weapon_id: u32,
        health: u32,
    },
    Parry {
        attacker: u32,
        target: u32,
        weapon_id: u32,
        defence_weapon_id: u32,
        evidence: Box<super::contacts::ContactEvent>,
    },
    Contact {
        attacker: u32,
        target: u32,
        ground: bool,
        occurred_at_ms: f64,
    },
    SurfaceHit {
        attacker: u32,
        weapon_id: u32,
        ground: bool,
        evidence: Box<super::contacts::ContactEvent>,
    },
    Kill {
        attacker: u32,
        target: u32,
        first_blood: bool,
        butcher_ended: bool,
    },
    Life {
        actor_id: u32,
        state: Life,
    },
    Score {
        actor_id: u32,
        score: Score,
    },
    ContactOverflow {
        dropped: u32,
    },
    Ended(Settlement),
}

#[derive(Clone, Debug)]
pub enum Input {
    Training {
        source_id: u64,
        actor_id: u32,
        command: super::training::Command,
    },
    SourceSquad {
        source_id: u64,
        actor_id: u32,
        command: super::SquadCommand,
    },
    SourceSquadSpawn {
        source_id: u64,
        actor_id: u32,
        target_id: u32,
        position: Point,
    },
    World(Arc<World>),
    SourceWorld(Arc<SourceWorld>),
    MotionAttempt(ai::MotionAttempt),
    SourceMotionAttempt {
        source_id: u64,
        attempt: ai::MotionAttempt,
    },
    Action {
        actor_id: u32,
        command: action::Command,
    },
    SourceAction {
        source_id: u64,
        actor_id: u32,
        command: action::Command,
    },
    PlayerAction {
        stamp: t7r_battle_core::battle_semantics::SourceStamp,
        actor_id: u32,
        command: action::Command,
    },
    Contacts(super::contacts::ContactBatch),
    SourceContacts {
        source_id: u64,
        batch: super::contacts::ContactBatch,
    },
    Timers,
    Tick,
    RequestPlayerSpawn,
    RequestHumanSpawn {
        actor_id: u32,
    },
    SourceSpawn {
        source_id: u64,
        actor_id: u32,
    },
    Quit,
}

pub(crate) fn invalid(error: impl std::fmt::Display) -> std::io::Error {
    std::io::Error::new(std::io::ErrorKind::InvalidData, error.to_string())
}
pub(crate) fn deadline(at: u64, delay: u64) -> std::io::Result<u64> {
    at.checked_add(delay)
        .ok_or_else(|| invalid("battle time overflow"))
}
pub(crate) fn valid_point(p: Point) -> bool {
    p.into_iter().all(|v| v.is_finite() && v.abs() < 100000.)
}
