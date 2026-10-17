use crate::action::{Direction, Intent, Phase};
use crate::navigation::Point;
use std::fmt;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Profile {
    Aggressive,
    Balanced,
    Defensive,
    Berserker,
}

impl Profile {
    pub fn name(self) -> &'static str {
        match self {
            Self::Aggressive => "aggressive",
            Self::Balanced => "balanced",
            Self::Defensive => "defensive",
            Self::Berserker => "berserker",
        }
    }
    pub(crate) fn settings(self, tempo: u16) -> Settings {
        let (attack, observation, guard, counter) = match self {
            Self::Aggressive => (120, 240, 300, 120),
            Self::Balanced => (220, 150, 450, 150),
            Self::Defensive => (400, 90, 650, 90),
            Self::Berserker => (0, 240, 0, 0),
        };
        let scale = |value: u64| (value as f64 * (tempo as f64 / 100.0)).round_ties_even() as u64;
        Settings {
            attack_ms: scale(attack),
            observation_ms: scale(observation),
            guard_ms: scale(guard),
            counter_ms: scale(counter),
        }
    }
}

pub(crate) struct Settings {
    pub attack_ms: u64,
    pub observation_ms: u64,
    pub guard_ms: u64,
    pub counter_ms: u64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct BotConfig {
    pub actor_id: u32,
    pub camp: u8,
    pub profile: Profile,
    pub tempo: u16,
    pub lane: i8,
    pub patrol_anchor: Point,
    pub decision_seed: u32,
    pub rhythm_seed: u32,
    pub route_seed: u32,
}

impl BotConfig {
    pub fn from_spawns(
        actor_id: u32,
        ordinal: usize,
        camp: u8,
        seed: u32,
        own: &[Point],
        other: &[Point],
    ) -> Result<Self, AiError> {
        if !(2..=crate::MAX_BATTLE_ACTORS as u32).contains(&actor_id)
            || !(1..=2).contains(&camp)
            || own.is_empty()
            || other.is_empty()
            || own.iter().chain(other).any(|p| !valid_point(*p))
        {
            return Err(AiError("invalid bot spawn configuration"));
        }
        let center = |points: &[Point]| {
            std::array::from_fn::<_, 3, _>(|i| {
                super::math::sum(points.iter().map(|p| p[i])) / points.len() as f64
            })
        };
        let (a, b) = (center(own), center(other));
        let (dx, dy) = (b[0] - a[0], b[1] - a[1]);
        let distance = (dx * dx + dy * dy).sqrt().max(1.0);
        let lane = ((ordinal + ordinal / 4) % 3) as i8 - 1;
        let individuality = seed ^ actor_id.wrapping_mul(2654435761);
        let along = (((individuality >> 8) % 9) as f64 - 4.0) / distance;
        let side = (f64::from(lane) * 10.0 + ((individuality % 5) as f64 - 2.0)) / distance;
        Ok(Self {
            actor_id,
            camp,
            profile: [
                Profile::Aggressive,
                Profile::Balanced,
                Profile::Defensive,
                Profile::Berserker,
            ][(actor_id as usize - 2) % 4],
            tempo: 85 + (individuality % 31) as u16,
            lane,
            patrol_anchor: [
                (a[0] + b[0]) / 2.0 + dx * along - dy * side,
                (a[1] + b[1]) / 2.0 + dy * along + dx * side,
                (a[2] + b[2]) / 2.0,
            ],
            decision_seed: seed ^ u32::from(camp) ^ actor_id,
            rhythm_seed: individuality ^ 0xA341316C,
            route_seed: individuality,
        })
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ActionObservation {
    pub intent: Option<Intent>,
    pub phase: Phase,
    pub sequence: u16,
    pub released: bool,
    pub phase_due_ms: Option<u64>,
}

impl ActionObservation {
    pub fn idle() -> Self {
        Self {
            intent: None,
            phase: Phase::Idle,
            sequence: 0,
            released: false,
            phase_due_ms: None,
        }
    }
    pub(crate) fn index(self) -> i8 {
        match self.phase {
            Phase::Prepare => 0,
            Phase::Hold => 1,
            Phase::Strike => {
                if self.intent == Some(Intent::Dash) {
                    1
                } else {
                    2
                }
            }
            Phase::Recovery => 3,
            _ => -1,
        }
    }
    pub(crate) fn reaction(self) -> bool {
        matches!(
            self.phase,
            Phase::Parried | Phase::Terrain | Phase::ParrySuccess
        )
    }
    pub(crate) fn locked(self) -> bool {
        self.reaction()
            || self.index() >= 0
            || matches!(self.phase, Phase::WeaponChange | Phase::Hurt)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ContactState {
    Unavailable,
    Blocked,
    Clear,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ContactObservation {
    pub target_id: Option<u32>,
    pub state: ContactState,
}

impl ContactObservation {
    pub fn unavailable() -> Self {
        Self {
            target_id: None,
            state: ContactState::Unavailable,
        }
    }
    pub(crate) fn clear_for(self, target: u32) -> bool {
        self.target_id == Some(target) && self.state == ContactState::Clear
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct ActorSnapshot {
    pub id: u32,
    pub camp: u8,
    pub alive: bool,
    pub blocks_movement: bool,
    pub position: Point,
    pub heading: f64,
    pub health: u16,
    /// Conservative weapon-based standing distance, not a hit-test radius.
    pub preferred_distance: f64,
    pub shield_equipped: bool,
    pub action: ActionObservation,
    pub hurt: bool,
    pub contact: ContactObservation,
}

#[derive(Clone, Debug, PartialEq)]
pub struct WorldObservation {
    pub sampled_at_ms: u64,
    pub actors: Vec<ActorSnapshot>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct MotionAttempt {
    pub actor_id: u32,
    pub entity: u64,
    pub sampled_at_ms: u64,
    pub position: Point,
    pub heading: f64,
    pub attempted_forward: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct AttackTimings {
    pub windup_ms: [u64; 4],
    pub allowed: [bool; 4],
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ActionIntent {
    pub intent: Intent,
    pub released: bool,
    pub release_after_ms: Option<u64>,
}

#[derive(Clone, Debug, PartialEq)]
pub enum MovementIntent {
    Stop,
    Turn {
        target: Point,
    },
    Follow {
        points: Vec<Point>,
        look_at: Option<Point>,
    },
}

#[derive(Clone, Debug, PartialEq)]
pub struct BotOutput {
    pub actor_id: u32,
    pub target_id: Option<u32>,
    pub status: &'static str,
    pub action: Option<ActionIntent>,
    pub movement: MovementIntent,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Decision {
    pub kind: DecisionKind,
    pub reason: &'static str,
    pub intent: Option<Intent>,
    pub seed: u32,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DecisionKind {
    Wait,
    Attack,
    Parry,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct AiError(pub(crate) &'static str);
impl fmt::Display for AiError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.0)
    }
}
impl std::error::Error for AiError {}

pub(crate) const DIRECTIONS: [Direction; 4] = [
    Direction::Up,
    Direction::Right,
    Direction::Down,
    Direction::Left,
];
pub(crate) fn valid_point(p: Point) -> bool {
    p.iter().all(|v| v.is_finite() && v.abs() < 100000.0)
}
