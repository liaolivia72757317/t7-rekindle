use super::*;

impl ActionState {
    pub(super) fn input(
        &mut self,
        command: Command,
        context: Context,
        loadout: &Loadout,
        events: &mut Vec<ActionEvent>,
    ) -> Result<(), ActionError> {
        let press = if let Command::Press { intent, .. } = command {
            Some(intent)
        } else {
            None
        };
        if press == Some(Intent::Shield) && self.snapshot.offhand_id == 0 {
            return Ok(());
        }
        if !context.enabled || !context.active {
            self.reset(false, context.cutoff(), events);
            return Ok(());
        }
        if let Some(Intent::Attack(direction)) = press
            && !loadout.profile(self.snapshot.weapon_id)?.attacks[direction as usize].enabled
        {
            return Ok(());
        }
        if matches!(
            command,
            Command::Press {
                intent: Intent::Attack(_),
                defence_down: true,
                ..
            }
        ) {
            return Ok(());
        }
        if self.snapshot.phase == Phase::WeaponChange {
            return Ok(());
        }
        if self.snapshot.hurt_due_ms.is_some() {
            self.buffer(command, true);
            return Ok(());
        }
        if let Command::Press {
            intent: Intent::Dash,
            dash_run,
            dash_forward,
            ..
        } = command
        {
            if dash_run && dash_forward && self.snapshot.phase == Phase::Idle {
                self.start(Intent::Dash, false, context, loadout, events)?;
            }
            return Ok(());
        }
        if self.snapshot.phase.is_reaction() {
            match self.snapshot.phase {
                Phase::Parried | Phase::Terrain => {
                    self.buffer(command, true);
                }
                Phase::ParrySuccess => {
                    if matches!(press, Some(Intent::Attack(_))) {
                        self.snapshot.released = true;
                        self.snapshot.defence_interrupted_at_ms = Some(context.now_ms as f64);
                        self.buffer(command, true);
                    } else if self.snapshot.released || self.snapshot.buffered.is_some() {
                        self.buffer(command, true);
                    }
                }
                _ => {}
            }
            return self.release(command, context, loadout, events);
        }
        if let (Some(next), Some(current)) = (press, self.snapshot.intent) {
            let prepared = matches!(self.snapshot.phase, Phase::Prepare | Phase::Hold);
            let interrupts_attack =
                next.is_guard() && matches!(current, Intent::Attack(_)) && prepared;
            let interrupts_guard = matches!(next, Intent::Attack(_))
                && current.is_guard()
                && prepared
                && !self.snapshot.released;
            let interrupts_recovery =
                matches!(current, Intent::Attack(_)) && self.snapshot.phase == Phase::Recovery;
            if interrupts_attack || interrupts_guard || interrupts_recovery {
                if interrupts_guard {
                    self.snapshot.defence_interrupted_at_ms = Some(context.now_ms as f64);
                }
                self.reset(false, context.cutoff(), events);
                return self.start(next, false, context, loadout, events);
            }
        }
        if self.buffer(command, false) {
            return Ok(());
        }
        if let Some(intent) = press {
            if self.snapshot.phase == Phase::Idle {
                self.start(intent, false, context, loadout, events)?;
            }
            return Ok(());
        }
        self.release(command, context, loadout, events)
    }

    fn buffer(&mut self, command: Command, locked: bool) -> bool {
        if !locked
            && (self.snapshot.intent.is_none_or(Intent::is_guard)
                || matches!(self.snapshot.phase, Phase::Idle | Phase::Hurt))
        {
            return false;
        }
        if let Command::Press { intent, .. } = command
            && intent != Intent::Dash
        {
            self.snapshot.buffered = Some(BufferedAction {
                intent,
                released: false,
            });
            return true;
        }
        if let Some(buffered) = self.snapshot.buffered.as_mut()
            && (matches!(command, Command::ReleaseAttack)
                && matches!(buffered.intent, Intent::Attack(_))
                || matches!(command, Command::ReleaseGuard) && buffered.intent.is_guard())
        {
            buffered.released = true;
        }
        false
    }

    fn release(
        &mut self,
        command: Command,
        context: Context,
        loadout: &Loadout,
        events: &mut Vec<ActionEvent>,
    ) -> Result<(), ActionError> {
        if self.snapshot.phase.is_reaction() {
            if self.snapshot.phase == Phase::ParrySuccess && command == Command::ReleaseGuard {
                self.snapshot.released = true;
                self.snapshot.defence_interrupted_at_ms = Some(context.now_ms as f64);
            }
            return Ok(());
        }
        let Some(intent) = self.snapshot.intent else {
            return Ok(());
        };
        let expected = if matches!(intent, Intent::Attack(_)) {
            Command::ReleaseAttack
        } else {
            Command::ReleaseGuard
        };
        if matches!(self.snapshot.phase, Phase::Idle | Phase::Hurt)
            || self.snapshot.released
            || command != expected
        {
            return Ok(());
        }
        if command == Command::ReleaseAttack && self.snapshot.phase == Phase::Prepare {
            self.snapshot.released = true;
            return Ok(());
        }
        self.set_timer(TimerKind::Phase, None, events);
        if command == Command::ReleaseGuard {
            self.snapshot.defence_interrupted_at_ms = Some(context.now_ms as f64);
            if !self.start_buffered(context, loadout, events)? {
                self.animate(Animation::Idle, context.now_ms, 0, None, events)?;
                self.reset(false, context.cutoff(), events);
            }
            return Ok(());
        }
        self.snapshot.phase = Phase::Strike;
        self.snapshot.released = true;
        self.snapshot.held_ms = context
            .now_ms
            .saturating_sub(self.snapshot.held_at_ms.unwrap_or(context.now_ms));
        let duration = self.duration()?;
        self.animate(
            Animation::Action {
                intent,
                phase: Phase::Strike,
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

    pub(super) fn change_weapon(
        &mut self,
        slot: i16,
        context: Context,
        loadout: &Loadout,
        events: &mut Vec<ActionEvent>,
    ) -> Result<(), ActionError> {
        if !context.enabled || !context.active {
            return Ok(());
        }
        let current = self.snapshot.weapon_id;
        let target = loadout.select(slot, current, self.snapshot.main_weapon_id)?;
        if target.is_none()
            && loadout
                .items()
                .iter()
                .any(|item| i16::from(item.slot) == slot)
        {
            return Ok(());
        }
        let status = if target.is_none() {
            WeaponChangeStatus::InvalidSlot
        } else if self.snapshot.hurt_due_ms.is_some() || self.snapshot.phase != Phase::Idle {
            WeaponChangeStatus::Busy
        } else {
            WeaponChangeStatus::Success
        };
        let target = if status == WeaponChangeStatus::Success {
            target.unwrap()
        } else {
            current
        };
        let offhand = if status == WeaponChangeStatus::Success {
            loadout.offhand(target, &self.broken_shields)?
        } else {
            self.snapshot.offhand_id
        };
        events.push(ActionEvent::WeaponChangeResult {
            actor_id: self.snapshot.actor_id,
            status,
            weapon_id: target,
            offhand_id: offhand,
        });
        if status != WeaponChangeStatus::Success || target == current {
            return Ok(());
        }
        self.reset(false, context.cutoff(), events);
        self.snapshot.weapon_id = target;
        self.snapshot.offhand_id = offhand;
        if target != loadout.fist_id {
            self.snapshot.main_weapon_id = target;
        }
        self.snapshot.phase = Phase::WeaponChange;
        events.push(ActionEvent::EquipmentChanged {
            actor_id: self.snapshot.actor_id,
            weapon_id: target,
            offhand_id: offhand,
        });
        let duration = loadout.profile(target)?.change_ms;
        self.animate(
            Animation::WeaponChange,
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
}
