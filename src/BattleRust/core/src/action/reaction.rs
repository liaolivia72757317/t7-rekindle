use super::*;

impl ActionState {
    pub(super) fn react(
        &mut self,
        command: Command,
        context: Context,
        loadout: &Loadout,
        events: &mut Vec<ActionEvent>,
    ) -> Result<(), ActionError> {
        match command {
            Command::Hurt {
                source_id,
                kind,
                duration_ms,
                movement_ms,
                contact_at_ms,
            } => {
                if source_id == 0 || !contact_at_ms.is_finite() || contact_at_ms < 0.0 {
                    return Err(ActionError::InvalidCommand);
                }
                let remaining = self
                    .hurt_until_ms
                    .unwrap_or(0)
                    .saturating_sub(context.now_ms);
                let duration = u16::try_from(remaining.max(u64::from(duration_ms)))
                    .map_err(|_| ActionError::TimeOverflow)?;
                self.reset(true, contact_at_ms, events);
                self.snapshot.defence_interrupted_at_ms = Some(contact_at_ms);
                self.snapshot.phase = Phase::Hurt;
                self.later(TimerKind::Hurt, context.now_ms, u64::from(duration), events)?;
                self.hurt_until_ms = self.snapshot.hurt_due_ms;
                self.animate(
                    Animation::Hurt(kind),
                    context.now_ms,
                    duration,
                    Some(source_id),
                    events,
                )?;
                if movement_ms > 0 {
                    let until = context
                        .now_ms
                        .checked_add(u64::from(movement_ms))
                        .ok_or(ActionError::TimeOverflow)?
                        .max(self.snapshot.movement_due_ms.unwrap_or(0));
                    self.set_timer(TimerKind::Movement, Some(until), events);
                    events.push(ActionEvent::MovementLock {
                        actor_id: self.snapshot.actor_id,
                        until_ms: Some(until),
                    });
                }
                Ok(())
            }
            Command::ParrySuccess {
                target_id,
                intent,
                minimum_ms,
            } => {
                if target_id == 0 || !intent.is_guard() {
                    return Err(ActionError::InvalidCommand);
                }
                let remaining = if self.snapshot.phase == Phase::ParrySuccess {
                    self.snapshot
                        .phase_due_ms
                        .unwrap_or(context.now_ms)
                        .saturating_sub(context.now_ms)
                } else {
                    0
                };
                self.reset(false, context.cutoff(), events);
                self.snapshot.intent = Some(intent);
                self.snapshot.phase = Phase::ParrySuccess;
                self.active_profile = Some(loadout.snapshot(self.snapshot.weapon_id)?);
                let duration = u16::try_from(
                    remaining
                        .max(u64::from(minimum_ms))
                        .max(u64::from(self.duration()?)),
                )
                .map_err(|_| ActionError::TimeOverflow)?;
                self.animate(
                    Animation::Action {
                        intent,
                        phase: Phase::ParrySuccess,
                    },
                    context.now_ms,
                    duration,
                    Some(target_id),
                    events,
                )?;
                self.later(
                    TimerKind::Phase,
                    context.now_ms,
                    u64::from(duration),
                    events,
                )
            }
            Command::Parried {
                intent, weapon_id, ..
            }
            | Command::Terrain { intent, weapon_id } => {
                let terrain = matches!(command, Command::Terrain { .. });
                let target_id = match command {
                    Command::Parried { target_id, .. } => target_id,
                    _ => 0,
                };
                if !intent.is_attack() || (!terrain && target_id == 0) {
                    return Err(ActionError::InvalidCommand);
                }
                let profile = loadout.snapshot(weapon_id)?;
                if profile.shield || intent == Intent::Dash && profile.dash.is_none() {
                    return Err(ActionError::InvalidProfile);
                }
                self.reset(true, context.cutoff(), events);
                self.snapshot.intent = Some(intent);
                self.snapshot.phase = if terrain {
                    Phase::Terrain
                } else {
                    Phase::Parried
                };
                self.snapshot.released = true;
                self.active_profile = Some(profile);
                let duration = self.duration()?;
                self.animate(
                    Animation::Action {
                        intent,
                        phase: self.snapshot.phase,
                    },
                    context.now_ms,
                    duration,
                    Some(target_id),
                    events,
                )?;
                self.later(
                    TimerKind::Phase,
                    context.now_ms,
                    u64::from(duration),
                    events,
                )
            }
            _ => Err(ActionError::InvalidCommand),
        }
    }

    pub(super) fn finish_reaction(
        &mut self,
        context: Context,
        loadout: &Loadout,
        events: &mut Vec<ActionEvent>,
    ) -> Result<(), ActionError> {
        if !context.instance_matches {
            self.reset(false, context.cutoff(), events);
            return Ok(());
        }
        if self.snapshot.phase == Phase::ParrySuccess && !self.snapshot.released {
            self.snapshot.phase = Phase::Hold;
            self.animate(
                Animation::Action {
                    intent: self.snapshot.intent.ok_or(ActionError::InvalidCommand)?,
                    phase: Phase::Hold,
                },
                context.now_ms,
                0,
                None,
                events,
            )?;
        } else {
            self.snapshot.phase = Phase::Idle;
            if !self.start_buffered(context, loadout, events)? {
                self.animate(Animation::Idle, context.now_ms, 0, None, events)?;
                self.reset(false, context.cutoff(), events);
            }
        }
        Ok(())
    }

    pub(super) fn finish_lock(
        &mut self,
        kind: TimerKind,
        context: Context,
        loadout: &Loadout,
        events: &mut Vec<ActionEvent>,
    ) -> Result<(), ActionError> {
        if kind == TimerKind::Movement {
            self.set_timer(kind, None, events);
            if context.enabled && context.active {
                events.push(ActionEvent::MovementLock {
                    actor_id: self.snapshot.actor_id,
                    until_ms: None,
                });
            }
            return Ok(());
        }
        if !context.active || !context.enabled || !context.instance_matches {
            self.set_timer(kind, None, events);
            self.hurt_until_ms = None;
            self.reset(false, context.cutoff(), events);
            return Ok(());
        }
        if !context.pose_ready {
            return self.later(kind, context.now_ms, 50, events);
        }
        self.set_timer(kind, None, events);
        self.hurt_until_ms = None;
        self.snapshot.phase = Phase::Idle;
        if !self.start_buffered(context, loadout, events)? {
            self.reset(false, context.cutoff(), events);
            self.animate(Animation::Idle, context.now_ms, 0, None, events)?;
        }
        Ok(())
    }
}
