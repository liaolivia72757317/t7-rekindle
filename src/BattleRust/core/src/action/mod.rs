//! Transactional actor action phases; adapters own original packet/state identifiers.
mod equipment;
mod input;
mod reaction;
mod types;

pub use equipment::Loadout;
use std::collections::BTreeSet;
use std::sync::Arc;
pub use types::*;

#[derive(Debug, Clone, PartialEq)]
pub struct ActionState {
    snapshot: ActionSnapshot,
    active_profile: Option<Arc<WeaponProfile>>,
    broken_shields: BTreeSet<u32>,
    hurt_until_ms: Option<u64>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Transition {
    pub state: ActionState,
    pub events: Vec<ActionEvent>,
}

impl ActionState {
    pub fn new(
        actor_id: u32,
        loadout: &Loadout,
        weapon_id: u32,
        sequence: u16,
    ) -> Result<Self, ActionError> {
        if actor_id == 0 || sequence == 0 {
            return Err(ActionError::InvalidActor);
        }
        if loadout.profile(weapon_id)?.shield {
            return Err(ActionError::InvalidLoadout);
        }
        Ok(Self {
            snapshot: ActionSnapshot {
                actor_id,
                intent: None,
                phase: Phase::Idle,
                sequence,
                released: false,
                held_at_ms: None,
                held_ms: 0,
                phase_due_ms: None,
                hurt_due_ms: None,
                movement_due_ms: None,
                buffered: None,
                weapon_id,
                main_weapon_id: weapon_id,
                offhand_id: loadout.offhand(weapon_id, &BTreeSet::new())?,
                defence_interrupted_at_ms: None,
                changed_at_ms: 0,
                duration_ms: 0,
            },
            active_profile: None,
            broken_shields: BTreeSet::new(),
            hurt_until_ms: None,
        })
    }

    pub fn snapshot(&self) -> ActionSnapshot {
        self.snapshot
    }
    pub fn active_profile(&self) -> Option<&WeaponProfile> {
        self.active_profile.as_deref()
    }

    /// Broken shields are authoritative durability output; never a client inventory edit.
    pub fn break_shield(&self, weapon_id: u32, loadout: &Loadout) -> Result<Self, ActionError> {
        if !loadout.profile(weapon_id)?.shield {
            return Err(ActionError::InvalidLoadout);
        }
        let mut next = self.clone();
        next.broken_shields.insert(weapon_id);
        next.snapshot.offhand_id =
            loadout.offhand(next.snapshot.weapon_id, &next.broken_shields)?;
        Ok(next)
    }

    pub fn apply(
        &self,
        command: Command,
        context: Context,
        loadout: &Loadout,
    ) -> Result<Transition, ActionError> {
        if context
            .contact_at_ms
            .is_some_and(|at| !at.is_finite() || at < 0.0)
        {
            return Err(ActionError::InvalidCommand);
        }
        loadout.profile(self.snapshot.weapon_id)?;
        let mut next = self.clone();
        let mut events = Vec::new();
        next.dispatch(command, context, loadout, &mut events)?;
        Ok(Transition {
            state: next,
            events,
        })
    }

    fn dispatch(
        &mut self,
        command: Command,
        context: Context,
        loadout: &Loadout,
        events: &mut Vec<ActionEvent>,
    ) -> Result<(), ActionError> {
        match command {
            Command::Press { .. } | Command::ReleaseAttack | Command::ReleaseGuard => {
                self.input(command, context, loadout, events)
            }
            Command::Start { intent, released } => {
                if self.snapshot.phase == Phase::Idle {
                    self.start(intent, released, context, loadout, events)
                } else {
                    Ok(())
                }
            }
            Command::Timer { kind, deadline_ms } => {
                self.timer(kind, deadline_ms, context, loadout, events)
            }
            Command::ChangeSlot(slot) => self.change_weapon(slot, context, loadout, events),
            Command::Parried { .. }
            | Command::ParrySuccess { .. }
            | Command::Terrain { .. }
            | Command::Hurt { .. } => self.react(command, context, loadout, events),
            Command::Cancel => {
                self.reset(false, context.cutoff(), events);
                self.set_timer(TimerKind::Hurt, None, events);
                self.hurt_until_ms = None;
                self.set_timer(TimerKind::Movement, None, events);
                Ok(())
            }
        }
    }

    fn reset(&mut self, preserve_buffered: bool, at: f64, events: &mut Vec<ActionEvent>) {
        events.push(ActionEvent::CancelSwing {
            actor_id: self.snapshot.actor_id,
            at_ms: at,
            interrupted: true,
        });
        self.set_timer(TimerKind::Phase, None, events);
        self.snapshot.intent = None;
        self.snapshot.phase = Phase::Idle;
        self.snapshot.released = false;
        self.snapshot.held_at_ms = None;
        self.snapshot.held_ms = 0;
        self.active_profile = None;
        if !preserve_buffered || self.snapshot.buffered.is_some_and(|value| value.released) {
            self.snapshot.buffered = None;
        }
    }

    fn set_timer(&mut self, kind: TimerKind, due_ms: Option<u64>, events: &mut Vec<ActionEvent>) {
        let slot = match kind {
            TimerKind::Phase => &mut self.snapshot.phase_due_ms,
            TimerKind::Hurt => &mut self.snapshot.hurt_due_ms,
            TimerKind::Movement => &mut self.snapshot.movement_due_ms,
        };
        if *slot != due_ms {
            *slot = due_ms;
            events.push(ActionEvent::TimerChanged {
                actor_id: self.snapshot.actor_id,
                kind,
                due_ms,
            });
        }
    }

    fn later(
        &mut self,
        kind: TimerKind,
        at: u64,
        delay: u64,
        events: &mut Vec<ActionEvent>,
    ) -> Result<(), ActionError> {
        self.set_timer(
            kind,
            Some(at.checked_add(delay).ok_or(ActionError::TimeOverflow)?),
            events,
        );
        Ok(())
    }

    fn animate(
        &mut self,
        animation: Animation,
        at: u64,
        duration_ms: u16,
        target_id: Option<u32>,
        events: &mut Vec<ActionEvent>,
    ) -> Result<(), ActionError> {
        self.snapshot.sequence = self.snapshot.sequence % u16::MAX + 1;
        self.snapshot.changed_at_ms = at;
        self.snapshot.duration_ms = duration_ms;
        events.push(ActionEvent::Animation(AnimationEvent {
            actor_id: self.snapshot.actor_id,
            sequence: self.snapshot.sequence,
            at_ms: at,
            duration_ms,
            target_id,
            animation,
        }));
        events.push(ActionEvent::CancelSwing {
            actor_id: self.snapshot.actor_id,
            at_ms: at as f64,
            interrupted: false,
        });
        if matches!(
            animation,
            Animation::Action {
                phase: Phase::Strike,
                ..
            }
        ) {
            let intent = self.snapshot.intent.ok_or(ActionError::InvalidCommand)?;
            let profile = self
                .active_profile
                .as_ref()
                .ok_or(ActionError::InvalidProfile)?;
            let (start, end) = match intent {
                Intent::Attack(direction) => {
                    let p = profile.attacks[direction as usize];
                    (p.contact_start_ms, p.contact_end_ms)
                }
                Intent::Dash => {
                    let p = profile.dash.ok_or(ActionError::InvalidProfile)?;
                    (p.contact_start_ms, p.contact_end_ms)
                }
                _ => return Err(ActionError::InvalidCommand),
            };
            at.checked_add(u64::from(end))
                .ok_or(ActionError::TimeOverflow)?;
            events.push(ActionEvent::SwingStarted {
                actor_id: self.snapshot.actor_id,
                sequence: self.snapshot.sequence,
                intent,
                weapon_id: profile.weapon_id,
                began_at_ms: at,
                held_ms: self.snapshot.held_ms,
                strike_duration_ms: duration_ms,
                contact_start_ms: start,
                contact_end_ms: end,
            });
        }
        Ok(())
    }

    fn duration(&self) -> Result<u16, ActionError> {
        let profile = self
            .active_profile
            .as_ref()
            .ok_or(ActionError::InvalidProfile)?;
        Ok(match (self.snapshot.intent, self.snapshot.phase) {
            (Some(Intent::Attack(direction)), phase) => {
                let p = profile.attacks[direction as usize];
                match phase {
                    Phase::Prepare => p.prepare_ms,
                    Phase::Hold => 0,
                    Phase::Strike => p.strike_ms,
                    Phase::Recovery => p.recovery_ms,
                    Phase::Parried => p.parried_ms,
                    Phase::Terrain => p.rebound_ms,
                    _ => return Err(ActionError::InvalidCommand),
                }
            }
            (Some(Intent::Dash), phase) => {
                let p = profile.dash.ok_or(ActionError::InvalidProfile)?;
                match phase {
                    Phase::Strike => p.duration_ms,
                    Phase::Parried | Phase::Terrain => p.rebound_ms,
                    _ => return Err(ActionError::InvalidCommand),
                }
            }
            (Some(Intent::Shield), Phase::Prepare) => profile.guard_prepare_ms,
            (Some(Intent::Shield), Phase::ParrySuccess) => profile.guard_success_ms,
            (Some(Intent::Guard(_) | Intent::Shield), _) => 0,
            _ => return Err(ActionError::InvalidCommand),
        })
    }

    fn start(
        &mut self,
        intent: Intent,
        released: bool,
        context: Context,
        loadout: &Loadout,
        events: &mut Vec<ActionEvent>,
    ) -> Result<(), ActionError> {
        if !context.enabled
            || !context.active
            || self.snapshot.hurt_due_ms.is_some()
            || self.snapshot.phase.is_reaction()
            || self.snapshot.phase == Phase::WeaponChange
        {
            return Ok(());
        }
        if intent == Intent::Shield && self.snapshot.offhand_id == 0 {
            return Ok(());
        }
        let profile = loadout.snapshot(self.snapshot.weapon_id)?;
        if let Intent::Attack(direction) = intent
            && !profile.attacks[direction as usize].enabled
        {
            return Ok(());
        }
        if intent == Intent::Dash
            && (self.snapshot.weapon_id == loadout.fist_id || profile.dash.is_none())
        {
            return Ok(());
        }
        self.active_profile = Some(profile);
        self.snapshot.intent = Some(intent);
        self.snapshot.phase = if intent == Intent::Dash {
            Phase::Strike
        } else {
            Phase::Prepare
        };
        self.snapshot.released = released || intent == Intent::Dash;
        self.snapshot.held_at_ms = None;
        self.snapshot.held_ms = 0;
        self.snapshot.buffered = None;
        let duration = self.duration()?;
        self.animate(
            Animation::Action {
                intent,
                phase: self.snapshot.phase,
            },
            context.now_ms,
            duration,
            None,
            events,
        )?;
        self.later(
            TimerKind::Phase,
            context.now_ms,
            u64::from(duration),
            events,
        )
    }

    fn start_buffered(
        &mut self,
        context: Context,
        loadout: &Loadout,
        events: &mut Vec<ActionEvent>,
    ) -> Result<bool, ActionError> {
        if let Some(buffered) = self.snapshot.buffered.filter(|value| !value.released) {
            self.start(buffered.intent, false, context, loadout, events)?;
            return Ok(true);
        }
        Ok(false)
    }

    fn timer(
        &mut self,
        kind: TimerKind,
        deadline_ms: u64,
        context: Context,
        loadout: &Loadout,
        events: &mut Vec<ActionEvent>,
    ) -> Result<(), ActionError> {
        let due = match kind {
            TimerKind::Phase => self.snapshot.phase_due_ms,
            TimerKind::Hurt => self.snapshot.hurt_due_ms,
            TimerKind::Movement => self.snapshot.movement_due_ms,
        };
        if due != Some(deadline_ms) || context.now_ms < deadline_ms {
            return Ok(());
        }
        if kind != TimerKind::Phase {
            return self.finish_lock(kind, context, loadout, events);
        }
        self.set_timer(kind, None, events);
        if !context.active {
            return Ok(());
        }
        if !context.enabled || !context.instance_matches {
            self.reset(false, context.cutoff(), events);
            return Ok(());
        }
        if self.snapshot.phase == Phase::WeaponChange {
            self.animate(Animation::Idle, context.now_ms, 0, None, events)?;
            self.reset(false, context.cutoff(), events);
            return Ok(());
        }
        if self.snapshot.phase.is_reaction() {
            return self.finish_reaction(context, loadout, events);
        }
        let Some(intent) = self.snapshot.intent else {
            self.reset(false, context.cutoff(), events);
            return Ok(());
        };
        let next = match self.snapshot.phase {
            Phase::Prepare if intent.is_attack() && self.snapshot.released => Phase::Strike,
            Phase::Prepare => Phase::Hold,
            Phase::Hold if !self.snapshot.released => return Ok(()),
            Phase::Hold => Phase::Strike,
            Phase::Strike if intent == Intent::Dash => Phase::Idle,
            Phase::Strike => Phase::Recovery,
            _ => Phase::Idle,
        };
        if matches!(next, Phase::Recovery | Phase::Idle)
            && self.start_buffered(context, loadout, events)?
        {
            return Ok(());
        }
        if next == Phase::Idle {
            self.animate(Animation::Idle, context.now_ms, 0, None, events)?;
            self.reset(false, context.cutoff(), events);
            return Ok(());
        }
        self.snapshot.phase = next;
        if next == Phase::Hold && intent.is_attack() {
            self.snapshot.held_at_ms = Some(context.now_ms);
        }
        let duration = self.duration()?;
        self.animate(
            Animation::Action {
                intent,
                phase: next,
            },
            context.now_ms,
            duration,
            None,
            events,
        )?;
        if self.snapshot.released || next == Phase::Prepare {
            self.later(kind, context.now_ms, u64::from(duration), events)?;
        }
        Ok(())
    }
}
