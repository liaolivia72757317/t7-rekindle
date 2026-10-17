use super::*;
use t7r_battle_core::{action::Command, navigation::SpawnSelection};

const MAX_SCORE: u32 = i32::MAX as u32;

impl State {
    pub(super) fn kill(
        &mut self,
        source: u32,
        target: u32,
        config: &Config,
        events: &mut Vec<Output>,
    ) -> io::Result<()> {
        let butcher_ended = self.actors[&target].score.streak >= 10;
        let first_blood = !self.first_blood;
        self.first_blood = true;
        let killer = self.actor_mut(source)?;
        killer.score.kills = (killer.score.kills + 1).min(MAX_SCORE);
        killer.score.points = killer.score.points.saturating_add(100).min(MAX_SCORE);
        killer.score.streak = (killer.score.streak + 1).min(MAX_SCORE);
        events.push(Output::Score {
            actor_id: source,
            score: killer.score,
        });
        self.action(target, Command::Cancel, None, events)?;
        let now = self.now_ms;
        let victim = self.actor_mut(target)?;
        victim.score.deaths = (victim.score.deaths + 1).min(MAX_SCORE);
        victim.score.streak = 0;
        victim.health = 0;
        victim.swings.clear();
        victim.release_at_ms = None;
        let respawn_delay = if victim.member.kind == MemberKind::Human {
            8000
        } else {
            5000
        };
        victim.life = Life::Dead {
            died_at_ms: now,
            respawn_at_ms: deadline(now, respawn_delay)?,
        };
        events.push(Output::Life {
            actor_id: target,
            state: victim.life,
        });
        events.push(Output::Score {
            actor_id: target,
            score: victim.score,
        });
        events.push(Output::Kill {
            attacker: source,
            target,
            first_blood,
            butcher_ended,
        });
        if self.training.is_none()
            && self
                .camp_kills()
                .iter()
                .any(|&kills| kills >= config.kill_target)
        {
            self.end(config, EndReason::KillTarget, events);
        }
        Ok(())
    }

    fn camp_kills(&self) -> [u32; 2] {
        let mut result = [0u32; 2];
        for actor in self.actors.values() {
            let value = &mut result[usize::from(actor.member.camp - 1)];
            *value = value.saturating_add(actor.score.kills).min(MAX_SCORE);
        }
        result
    }

    pub(super) fn end(&mut self, config: &Config, reason: EndReason, events: &mut Vec<Output>) {
        let camps = self.camp_kills();
        let result = if reason == EndReason::Quit {
            0
        } else if camps[0] == camps[1] {
            3
        } else if camps[0] > camps[1] {
            1
        } else {
            2
        };
        let mut members = Vec::new();
        for camp in 1..=2 {
            let mut team: Vec<_> = self
                .actors
                .values()
                .filter(|a| a.member.camp == camp)
                .collect();
            team.sort_by_key(|a| {
                (
                    std::cmp::Reverse(a.score.points),
                    std::cmp::Reverse(a.score.kills),
                    a.score.deaths,
                    a.member.actor_id,
                )
            });
            members.extend(team.into_iter().enumerate().map(|(rank, a)| RankedScore {
                actor_id: a.member.actor_id,
                camp,
                rank: rank as u32 + 1,
                result: if result == 0 || result == 3 {
                    result
                } else if result == camp {
                    1
                } else {
                    2
                },
                score: a.score,
            }));
        }
        let settlement = Settlement {
            epoch: config.epoch,
            started_at_ms: config.started_at_ms,
            ended_at_ms: self.now_ms,
            reason,
            result,
            camp_kills: camps,
            members,
        };
        self.settlement = Some(settlement.clone());
        events.push(Output::Ended(settlement));
    }

    pub(super) fn life_tick(
        &mut self,
        resources: &BattleResources,
        events: &mut Vec<Output>,
    ) -> io::Result<()> {
        let now = self.now_ms;
        let ids: Vec<_> = self.actors.keys().copied().collect();
        for id in ids {
            let life = self.actors[&id].life;
            let died_at = match life {
                Life::Dead {
                    died_at_ms,
                    respawn_at_ms,
                } if now >= respawn_at_ms => {
                    if self.actors[&id].member.kind == MemberKind::Human {
                        if let Some(training) = &self.training
                            && training.snapshot.map_id == 6006
                        {
                            // The fixed training hero returns to the arena, not hero selection.
                            if training.snapshot.state == 3 {
                                let camp = usize::from(self.actors[&id].member.camp - 1);
                                let spawn = resources.camps()[camp].respawns()[0];
                                self.spawn_actor(id, spawn, resources, events)?;
                            }
                            continue;
                        }
                        let life = Life::Selecting { died_at_ms };
                        self.actor_mut(id)?.life = life;
                        events.push(Output::Life {
                            actor_id: id,
                            state: life,
                        });
                        continue;
                    }
                    died_at_ms
                }
                Life::Waiting { died_at_ms } => died_at_ms,
                _ => continue,
            };
            if self.fresh_world().is_none() {
                continue;
            }
            let neighbors: Vec<_> = self
                .actors
                .iter()
                .filter(|(rid, a)| {
                    **rid != id && matches!(a.life, Life::Alive | Life::Spawning { .. })
                })
                .filter_map(|(rid, a)| {
                    if let Life::Spawning { spawn, .. } = a.life {
                        Some(spawn.position)
                    } else {
                        self.fresh_pose(*rid).map(|p| p.position)
                    }
                })
                .collect();
            let camp = &resources.camps()[usize::from(self.actors[&id].member.camp - 1)];
            let spawn = if self.actors[&id].member.kind == MemberKind::Human {
                let points = camp.respawns();
                if points.is_empty() {
                    None
                } else {
                    let offset = (died_at / 8000 % points.len() as u64) as usize;
                    (offset..points.len())
                        .chain(0..offset)
                        .map(|i| points[i])
                        .find(|p| {
                            neighbors.iter().all(|n| {
                                (p.position[2] - n[2]).abs() >= 1.8
                                    || (p.position[0] - n[0]).hypot(p.position[1] - n[1]) >= 0.701
                            })
                        })
                }
            } else {
                match camp
                    .candidates()
                    .choose(u64::from(id), died_at, &neighbors)
                    .map_err(invalid)?
                {
                    SpawnSelection::Ready { spawn, .. } => Some(spawn),
                    _ => None,
                }
            };
            let Some(spawn) = spawn else {
                continue;
            };
            self.spawn_actor(id, spawn, resources, events)?;
        }
        Ok(())
    }

    pub(super) fn spawn_actor(
        &mut self,
        id: u32,
        spawn: t7r_battle_core::navigation::SpawnReference,
        resources: &BattleResources,
        events: &mut Vec<Output>,
    ) -> io::Result<()> {
        let now = self.now_ms;
        self.guard_meters.insert(
            id,
            GuardMeter::new(resources.guard_rules()).map_err(invalid)?,
        );
        let actor = self.actor_mut(id)?;
        actor.health = actor.resources.damage.max_health;
        actor.contact_life_at_ms = now;
        actor.last_attacked_at_ms = None;
        actor.swings.clear();
        actor.buffs.clear();
        actor.shields = actor
            .resources
            .damage
            .weapons
            .iter()
            .filter(|(_, w)| w.shield_max > 0)
            .map(|(&id, w)| (id, w.shield_max))
            .collect();
        actor.action = action::ActionState::new(
            id,
            &actor.resources.loadout,
            actor.member.weapon_id,
            // The client death animation consumed one sequence after the
            // last live action. Resume from it, without a wire-only offset.
            actor.action.snapshot().sequence % u16::MAX + 1,
        )
        .map_err(invalid)?;
        actor.life = Life::Spawning {
            spawned_at_ms: now,
            spawn,
        };
        actor.release_at_ms = None;
        events.push(Output::Life {
            actor_id: id,
            state: actor.life,
        });
        Ok(())
    }

    pub(super) fn confirm_spawns(&mut self, events: &mut Vec<Output>) -> io::Result<()> {
        if self.fresh_world().is_none() {
            return Ok(());
        }
        let ready: Vec<_> = self
            .actors
            .iter()
            .filter_map(|(&id, actor)| {
                let Life::Spawning {
                    spawned_at_ms,
                    spawn,
                } = actor.life
                else {
                    return None;
                };
                let pose = self.fresh_pose(id)?;
                (self.actor_sampled_at_ms[&id] > spawned_at_ms
                    && (pose.position[0] - spawn.position[0])
                        .hypot(pose.position[1] - spawn.position[1])
                        <= 1.
                    && (pose.position[2] - spawn.position[2]).abs()
                        <= if actor.member.kind == MemberKind::Human {
                            12.
                        } else {
                            1.
                        })
                .then_some(id)
            })
            .collect();
        for id in ready {
            Arc::make_mut(self.actors.get_mut(&id).expect("ready actor")).life = Life::Alive;
            if self.actors[&id].member.kind == MemberKind::Bot {
                self.ai.reset_actor(id).map_err(invalid)?;
            }
            events.push(Output::Life {
                actor_id: id,
                state: Life::Alive,
            });
        }
        Ok(())
    }

    pub(super) fn request_human_spawn(
        &mut self,
        id: u32,
        resources: &BattleResources,
        events: &mut Vec<Output>,
    ) -> io::Result<()> {
        let actor = self.actor_mut(id)?;
        if actor.member.kind != MemberKind::Human {
            return Err(invalid("spawn request requires a human actor"));
        }
        if let Life::Selecting { died_at_ms } = actor.life {
            actor.life = Life::Waiting { died_at_ms };
        }
        self.life_tick(resources, events)
    }
}
