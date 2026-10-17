use super::*;
use t7r_battle_core::action::{ActionEvent, Command, Context, Phase, TimerKind};

#[cfg(test)]
#[path = "ai_equipment_tests.rs"]
mod tests;

impl State {
    pub(super) fn action(
        &mut self,
        id: u32,
        command: Command,
        contact_at: Option<f64>,
        events: &mut Vec<Output>,
    ) -> io::Result<()> {
        let actor = self
            .actors
            .get(&id)
            .ok_or_else(|| invalid("unknown action actor"))?;
        let context = Context {
            now_ms: self.now_ms,
            enabled: self.settlement.is_none(),
            active: actor.life == Life::Alive,
            instance_matches: true,
            pose_ready: self.fresh_pose(id).is_some(),
            contact_at_ms: contact_at,
        };
        let transition = actor
            .action
            .apply(command, context, &actor.resources.loadout)
            .map_err(invalid)?;
        let now = self.now_ms;
        let actor = self.actor_mut(id)?;
        actor.action = transition.state;
        for event in &transition.events {
            match *event {
                ActionEvent::CancelSwing {
                    at_ms, interrupted, ..
                } => {
                    if interrupted {
                        actor.release_at_ms = None;
                    }
                    for swing in &mut actor.swings {
                        if interrupted {
                            swing.cutoff_ms = Some(swing.cutoff_ms.unwrap_or(at_ms).min(at_ms));
                        }
                        if swing.closed_at_ms.is_none() {
                            swing.closed_at_ms = Some(now);
                        }
                    }
                }
                ActionEvent::SwingStarted {
                    sequence,
                    intent,
                    weapon_id,
                    began_at_ms,
                    held_ms,
                    contact_start_ms,
                    contact_end_ms,
                    ..
                } => {
                    if actor.swings.len() >= 33 {
                        return Err(invalid(
                            "native contact settlement stalled: swing history exhausted",
                        ));
                    }
                    actor.swings.push(Swing {
                        sequence,
                        intent,
                        weapon_id,
                        began_at_ms,
                        held_ms,
                        start_ms: contact_start_ms,
                        end_ms: contact_end_ms,
                        closed_at_ms: None,
                        cutoff_ms: None,
                        hits: Vec::new(),
                        allies: 0,
                        enemies: 0,
                        stopped: false,
                        ground_played: false,
                        buffs: actor.buffs.clone(),
                    });
                }
                _ => {}
            }
        }
        events.extend(transition.events.into_iter().map(Output::Action));
        Ok(())
    }

    pub(super) fn timers(&mut self, events: &mut Vec<Output>) -> io::Result<()> {
        let mut pending = Vec::new();
        for (&id, actor) in &self.actors {
            let snapshot = actor.action.snapshot();
            for (name, kind, due) in [
                ("phase", TimerKind::Phase, snapshot.phase_due_ms),
                ("hurt", TimerKind::Hurt, snapshot.hurt_due_ms),
                ("movement", TimerKind::Movement, snapshot.movement_due_ms),
            ] {
                if let Some(due) = due.filter(|&due| due <= self.now_ms) {
                    pending.push((
                        format!("{name}-{id}"),
                        id,
                        Command::Timer {
                            kind,
                            deadline_ms: due,
                        },
                    ));
                }
            }
            if actor.release_at_ms.is_some_and(|due| due <= self.now_ms) {
                pending.push((
                    format!("release-{id}"),
                    id,
                    if snapshot.intent.is_some_and(|i| i.is_attack()) {
                        Command::ReleaseAttack
                    } else {
                        Command::ReleaseGuard
                    },
                ));
            }
        }
        pending.sort_by(|a, b| a.0.cmp(&b.0));
        for (_, id, command) in pending {
            if matches!(command, Command::ReleaseAttack | Command::ReleaseGuard) {
                self.actor_mut(id)?.release_at_ms = None;
            }
            self.action(id, command, None, events)?;
        }
        Ok(())
    }

    pub(super) fn ai_world(&self, fresh_only: bool) -> ai::WorldObservation {
        let actors = self
            .world
            .as_ref()
            .map(|world| {
                world
                    .actors
                    .iter()
                    .filter(|(id, _)| !fresh_only || self.fresh_pose(**id).is_some())
                    .map(|(&id, pose)| {
                        let actor = &self.actors[&id];
                        let action = actor.action.snapshot();
                        ai::ActorSnapshot {
                            id,
                            camp: actor.member.camp,
                            alive: actor.life == Life::Alive,
                            blocks_movement: matches!(
                                actor.life,
                                Life::Alive | Life::Spawning { .. }
                            ),
                            position: pose.position,
                            heading: pose.heading,
                            // AI thresholds are percentages, not absolute hit points.
                            health: (u64::from(actor.health) * 100)
                                .div_ceil(u64::from(actor.resources.damage.max_health))
                                as u16,
                            hurt: action.hurt_due_ms.is_some(),
                            shield_equipped: action.offhand_id != 0,
                            preferred_distance: actor
                                .resources
                                .preferred_distance(action.weapon_id),
                            contact: pose.contact,
                            action: ai::ActionObservation {
                                intent: action.intent,
                                phase: action.phase,
                                sequence: action.sequence,
                                released: action.released,
                                phase_due_ms: action.phase_due_ms,
                            },
                        }
                    })
                    .collect()
            })
            .unwrap_or_default();
        ai::WorldObservation {
            sampled_at_ms: self.world.as_ref().map_or(0, |world| {
                if fresh_only {
                    self.actor_sampled_at_ms
                        .iter()
                        .filter(|(id, _)| self.fresh_pose(**id).is_some())
                        .map(|(_, &at)| at)
                        .max()
                        .unwrap_or(0)
                } else {
                    world.sampled_at_ms
                }
            }),
            actors,
        }
    }

    pub(super) fn decide(
        &mut self,
        resources: &BattleResources,
        events: &mut Vec<Output>,
    ) -> io::Result<()> {
        let world = self.ai_world(true);
        let timings = self
            .actors
            .iter()
            .map(|(&id, actor)| {
                let profile = actor
                    .resources
                    .loadout
                    .profile(actor.action.snapshot().weapon_id)
                    .map_err(invalid)?;
                Ok((
                    id,
                    ai::AttackTimings {
                        windup_ms: profile.attacks.map(|attack| u64::from(attack.prepare_ms)),
                        allowed: profile.attacks.map(|attack| attack.enabled),
                    },
                ))
            })
            .collect::<io::Result<BTreeMap<_, _>>>()?;
        let outputs = self
            .ai
            .tick(self.now_ms, &world, resources.navigation(), &timings)
            .map_err(invalid)?;
        for mut output in outputs {
            if let Some(intent) = output.action {
                let before = self.actors[&output.actor_id].action.snapshot();
                self.action(
                    output.actor_id,
                    Command::Start {
                        intent: intent.intent,
                        released: intent.released,
                    },
                    None,
                    events,
                )?;
                if self.actors[&output.actor_id].action.snapshot().sequence != before.sequence {
                    let due = intent
                        .release_after_ms
                        .map(|delay| deadline(self.now_ms, delay))
                        .transpose()?;
                    self.actor_mut(output.actor_id)?.release_at_ms = due;
                } else {
                    output.action = None;
                }
            }
            if self.actors[&output.actor_id].action.snapshot().phase != Phase::Hurt {
                events.push(Output::Bot(output));
            }
        }
        Ok(())
    }
}
