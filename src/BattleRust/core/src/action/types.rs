use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ActionError {
    InvalidActor,
    InvalidProfile,
    InvalidLoadout,
    InvalidCommand,
    TimeOverflow,
}

impl fmt::Display for ActionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::InvalidActor => "invalid action actor or sequence",
            Self::InvalidProfile => "invalid action weapon profile",
            Self::InvalidLoadout => "invalid action loadout",
            Self::InvalidCommand => "invalid battle action command",
            Self::TimeOverflow => "battle action time overflow",
        })
    }
}
impl std::error::Error for ActionError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Direction {
    Up,
    Right,
    Down,
    Left,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Intent {
    Attack(Direction),
    Guard(Direction),
    Shield,
    Dash,
}

impl Intent {
    pub fn is_attack(self) -> bool {
        matches!(self, Self::Attack(_) | Self::Dash)
    }
    pub fn is_guard(self) -> bool {
        matches!(self, Self::Guard(_) | Self::Shield)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Phase {
    Idle,
    Prepare,
    Hold,
    Strike,
    Recovery,
    WeaponChange,
    Parried,
    Terrain,
    ParrySuccess,
    Hurt,
}

impl Phase {
    pub fn is_reaction(self) -> bool {
        matches!(self, Self::Parried | Self::Terrain | Self::ParrySuccess)
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct AttackProfile {
    pub enabled: bool,
    pub prepare_ms: u16,
    pub strike_ms: u16,
    pub recovery_ms: u16,
    pub parried_ms: u16,
    pub rebound_ms: u16,
    pub contact_start_ms: u16,
    pub contact_end_ms: u16,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DashProfile {
    pub duration_ms: u16,
    pub move_ms: u16,
    pub contact_start_ms: u16,
    pub contact_end_ms: u16,
    pub rebound_ms: u16,
    pub speed_milli: u32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WeaponProfile {
    pub weapon_id: u32,
    pub hand: u8,
    pub shield: bool,
    pub change_ms: u16,
    pub attacks: [AttackProfile; 4],
    pub guard_prepare_ms: u16,
    pub guard_success_ms: u16,
    pub dash: Option<DashProfile>,
}

impl WeaponProfile {
    pub fn validate(&self) -> Result<(), ActionError> {
        if self.weapon_id == 0 || !(1..=3).contains(&self.hand) {
            return Err(ActionError::InvalidProfile);
        }
        if self.shield {
            return if self.hand == 1 && self.change_ms == 0 && self.dash.is_none() {
                Ok(())
            } else {
                Err(ActionError::InvalidProfile)
            };
        }
        if self.change_ms == 0
            || !self.attacks.iter().any(|attack| attack.enabled)
            || self.guard_prepare_ms == 0
            || self.guard_success_ms == 0
            || self.attacks.iter().any(|a| {
                a.prepare_ms == 0
                    || a.strike_ms == 0
                    || a.recovery_ms == 0
                    || a.parried_ms == 0
                    || a.rebound_ms == 0
                    || a.contact_start_ms >= a.contact_end_ms
                    || a.contact_end_ms > a.strike_ms
            })
            || self.dash.is_some_and(|d| {
                d.duration_ms == 0
                    || d.move_ms > d.duration_ms
                    || d.contact_start_ms > d.contact_end_ms
                    || d.contact_end_ms > d.duration_ms
                    || d.rebound_ms == 0
                    || d.speed_milli > i32::MAX as u32
            })
        {
            return Err(ActionError::InvalidProfile);
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct EquipmentItem {
    pub slot: u8,
    pub weapon_id: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TimerKind {
    Phase,
    Hurt,
    Movement,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HurtKind {
    Hit,
    Knockdown,
    GuardBreak,
    GuardBreakKnockdown,
    Exhaustion,
    ShieldBreak,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Command {
    Press {
        intent: Intent,
        defence_down: bool,
        dash_run: bool,
        dash_forward: bool,
    },
    ReleaseAttack,
    ReleaseGuard,
    Start {
        intent: Intent,
        released: bool,
    },
    Timer {
        kind: TimerKind,
        deadline_ms: u64,
    },
    Parried {
        target_id: u32,
        intent: Intent,
        weapon_id: u32,
    },
    ParrySuccess {
        target_id: u32,
        intent: Intent,
        minimum_ms: u16,
    },
    Terrain {
        intent: Intent,
        weapon_id: u32,
    },
    Hurt {
        source_id: u32,
        kind: HurtKind,
        duration_ms: u16,
        movement_ms: u16,
        contact_at_ms: f64,
    },
    ChangeSlot(i16),
    Cancel,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Context {
    pub now_ms: u64,
    pub enabled: bool,
    pub active: bool,
    pub instance_matches: bool,
    pub pose_ready: bool,
    pub contact_at_ms: Option<f64>,
}

impl Context {
    pub(super) fn cutoff(self) -> f64 {
        self.contact_at_ms.unwrap_or(self.now_ms as f64)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BufferedAction {
    pub intent: Intent,
    pub released: bool,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ActionSnapshot {
    pub actor_id: u32,
    pub intent: Option<Intent>,
    pub phase: Phase,
    pub sequence: u16,
    pub released: bool,
    pub held_at_ms: Option<u64>,
    pub held_ms: u64,
    pub phase_due_ms: Option<u64>,
    pub hurt_due_ms: Option<u64>,
    pub movement_due_ms: Option<u64>,
    pub buffered: Option<BufferedAction>,
    pub weapon_id: u32,
    pub main_weapon_id: u32,
    pub offhand_id: u32,
    pub defence_interrupted_at_ms: Option<f64>,
    pub changed_at_ms: u64,
    pub duration_ms: u16,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Animation {
    Idle,
    Action { intent: Intent, phase: Phase },
    WeaponChange,
    Hurt(HurtKind),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AnimationEvent {
    pub actor_id: u32,
    pub sequence: u16,
    pub at_ms: u64,
    pub duration_ms: u16,
    pub target_id: Option<u32>,
    pub animation: Animation,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WeaponChangeStatus {
    Success,
    InvalidSlot,
    Busy,
}

#[derive(Debug, Clone, PartialEq)]
pub enum ActionEvent {
    Animation(AnimationEvent),
    TimerChanged {
        actor_id: u32,
        kind: TimerKind,
        due_ms: Option<u64>,
    },
    CancelSwing {
        actor_id: u32,
        at_ms: f64,
        interrupted: bool,
    },
    SwingStarted {
        actor_id: u32,
        sequence: u16,
        intent: Intent,
        weapon_id: u32,
        began_at_ms: u64,
        held_ms: u64,
        strike_duration_ms: u16,
        contact_start_ms: u16,
        contact_end_ms: u16,
    },
    WeaponChangeResult {
        actor_id: u32,
        status: WeaponChangeStatus,
        weapon_id: u32,
        offhand_id: u32,
    },
    EquipmentChanged {
        actor_id: u32,
        weapon_id: u32,
        offhand_id: u32,
    },
    MovementLock {
        actor_id: u32,
        until_ms: Option<u64>,
    },
}
