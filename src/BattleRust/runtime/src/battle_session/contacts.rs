use super::*;
#[cfg(test)]
#[path = "buff_tests.rs"]
mod buff_tests;
use t7r_battle_core::navigation::Point;
use t7r_battle_core::{
    action::{Command, Direction, HurtKind, Intent, Phase},
    damage, guard,
};

const MAX_HISTORY: usize = 32;

enum DamageMode {
    Regular,
    GuardBreak(f64),
}

#[derive(Clone, Debug)]
pub struct ContactBatch {
    pub epoch: u64,
    pub batch_id: u64,
    pub through_at_ms: f64,
    pub dropped: u32,
    pub events: Vec<ContactEvent>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ContactEvent {
    pub guard_volume: bool,
    pub actor_id: u32,
    pub target_id: u32,
    pub source_entity: u64,
    pub target_entity: u64,
    pub sequence: u16,
    pub phase_at_ms: u64,
    pub target_intent: Option<Intent>,
    pub target_phase: Phase,
    pub target_phase_at_ms: f64,
    /// Read-completion upper bound in match time; None retains v1 semantics.
    pub target_observed_at_ms: Option<u64>,
    /// Source read-completion bound; absent in legacy inputs.
    pub source_observed_at_ms: Option<u64>,
    pub occurred_at_ms: f64,
    pub along: f64,
    pub defence_weapon_id: u32,
    pub region: damage::Region,
    pub body_index: i8,
    pub source_position: Point,
    pub target_position: Point,
    pub source_heading: f64,
    pub target_heading: f64,
    pub source_velocity: Point,
    pub target_velocity: Point,
    pub weapon_velocity: Point,
    pub velocity_ready: bool,
    pub airborne: bool,
    pub target_airborne: bool,
    pub position: Point,
    pub normal: Point,
    pub direction: Point,
    pub material: i8,
}

impl ContactEvent {
    fn target_attacking(&self) -> bool {
        self.target_phase == Phase::Strike && self.target_phase_at_ms <= self.occurred_at_ms
    }

    pub(super) fn validate(&self, through: f64, now: u64) -> io::Result<()> {
        if !(1..=t7r_battle_core::MAX_BATTLE_ACTORS as u32).contains(&self.actor_id)
            || self.target_id > t7r_battle_core::MAX_BATTLE_ACTORS as u32
            || self.actor_id == self.target_id
            || self.source_entity == 0
            || (self.target_id != 0 && self.target_entity == 0)
            || self.sequence == 0
            || self.target_id == 0 && self.defence_weapon_id != 0
            || self.guard_volume
                && (self.target_id == 0
                    || self.defence_weapon_id == 0
                    || self.region != damage::Region::None
                    || self.body_index != -1
                    || self.target_intent == Some(Intent::Shield))
            || !self
                .region
                .valid_for_contact(self.target_id, self.defence_weapon_id)
            || !(-1..=7).contains(&self.material)
            || !(-1..=127).contains(&self.body_index)
            || !self.occurred_at_ms.is_finite()
            || self.occurred_at_ms < 0.
            || self.occurred_at_ms > through
            || self.source_observed_at_ms.is_some_and(|at| {
                at > now || self.phase_at_ms > at || self.occurred_at_ms > at as f64
            })
            || !self.target_phase_at_ms.is_finite()
            || self.target_phase_at_ms < 0.
                && (self.target_observed_at_ms.is_none() || self.source_observed_at_ms.is_none())
            || self.target_observed_at_ms.map_or(
                self.target_phase_at_ms > self.occurred_at_ms,
                |at| {
                    at > now
                        || self.occurred_at_ms > at as f64
                        || self.target_phase_at_ms > at as f64
                },
            )
            || !self.along.is_finite()
            || !(0. ..=1.).contains(&self.along)
            || !self.source_heading.is_finite()
            || self.source_heading.abs() > 180.
            || !self.target_heading.is_finite()
            || self.target_heading.abs() > 180.
            || [
                self.source_position,
                self.target_position,
                self.source_velocity,
                self.target_velocity,
                self.weapon_velocity,
                self.position,
                self.normal,
                self.direction,
            ]
            .into_iter()
            .any(|p| !valid_point(p))
            || self.normal.into_iter().map(|v| v * v).sum::<f64>().sqrt() < 0.9
            || self.normal.into_iter().map(|v| v * v).sum::<f64>().sqrt() > 1.1
            || self
                .direction
                .into_iter()
                .map(|v| v * v)
                .sum::<f64>()
                .sqrt()
                < 0.9
            || self
                .direction
                .into_iter()
                .map(|v| v * v)
                .sum::<f64>()
                .sqrt()
                > 1.1
        {
            return Err(invalid("invalid native contact evidence"));
        }
        Ok(())
    }
}

fn reject_guard(
    e: &ContactEvent,
    swing: &Swing,
    reason: GuardRejectReason,
    outputs: &mut Vec<Output>,
) -> bool {
    if e.guard_volume {
        outputs.push(Output::GuardRejected {
            reason,
            strike_duration_ms: swing.strike_duration_ms,
            evidence: Box::new(e.clone()),
        });
    }
    false
}

fn intent_id(intent: Intent) -> u32 {
    match intent {
        Intent::Attack(Direction::Up) => 300560,
        Intent::Attack(Direction::Right) => 300550,
        Intent::Attack(Direction::Down) => 300570,
        Intent::Attack(Direction::Left) => 300540,
        Intent::Dash => 700250,
        _ => 0,
    }
}
fn guard_intent(intent: Intent) -> guard::AttackIntent {
    match intent {
        Intent::Attack(Direction::Up) => guard::AttackIntent::Up,
        Intent::Attack(Direction::Right) => guard::AttackIntent::Right,
        Intent::Attack(Direction::Down) => guard::AttackIntent::Down,
        Intent::Attack(Direction::Left) => guard::AttackIntent::Left,
        _ => guard::AttackIntent::Other,
    }
}

fn ordinary_guard_direction(defence: Direction, attack: Intent) -> bool {
    use Direction::*;
    match attack {
        Intent::Attack(direction) => matches!(
            (defence, direction),
            (Up, Up) | (Down, Down) | (Left | Right, Left | Right)
        ),
        Intent::Dash => true,
        _ => false,
    }
}

impl State {
    pub(super) fn settle_contact_events(
        &mut self,
        through: f64,
        mut contacts: Vec<ContactEvent>,
        config: &Config,
        resources: &BattleResources,
        events: &mut Vec<Output>,
    ) -> io::Result<()> {
        if through < self.contact_through {
            return Err(invalid("regressing contact time"));
        }
        self.contact_through = through;
        contacts.sort_by(|a, b| {
            a.occurred_at_ms
                .total_cmp(&b.occurred_at_ms)
                .then(a.along.total_cmp(&b.along))
                .then(a.actor_id.cmp(&b.actor_id))
                .then(a.target_id.cmp(&b.target_id))
                .then(b.defence_weapon_id.cmp(&a.defence_weapon_id))
        });
        for contact in &contacts {
            if self.settlement.is_some() {
                break;
            }
            if self
                .training
                .as_ref()
                .is_some_and(|training| !training.accepts_contact(contact.occurred_at_ms))
            {
                continue;
            }
            self.settle_one(contact, config, resources, events)?;
        }
        for (&id, entry) in &mut self.actors {
            let pose = self.world.as_ref().and_then(|w| w.actors.get(&id));
            let keep = |swing: &Swing| {
                swing.closed_at_ms.is_none_or(|closed| {
                    through < closed as f64 || pose.is_some_and(|p| p.sequence == swing.sequence)
                })
            };
            if entry.swings.iter().any(|swing| !keep(swing)) {
                Arc::make_mut(entry).swings.retain(keep);
            }
            if entry
                .swings
                .iter()
                .filter(|s| s.closed_at_ms.is_some())
                .count()
                > MAX_HISTORY
            {
                return Err(invalid(
                    "native contact settlement stalled: swing history exhausted",
                ));
            }
            let meter = self.guard_meters[&id]
                .advance(through, resources.guard_rules())
                .map_err(invalid)?;
            self.guard_meters.insert(id, meter);
        }
        Ok(())
    }

    fn settle_one(
        &mut self,
        e: &ContactEvent,
        config: &Config,
        resources: &BattleResources,
        outputs: &mut Vec<Output>,
    ) -> io::Result<()> {
        let Some(source) = self.actors.get(&e.actor_id) else {
            return Ok(());
        };
        if source.life != Life::Alive || e.occurred_at_ms < source.contact_life_at_ms as f64 {
            return Ok(());
        }
        let Some(index) = source.swings.iter().position(|s| s.sequence == e.sequence) else {
            return Ok(());
        };
        let swing = &source.swings[index];
        // Sequence binds the authoritative swing; the native phase locates its
        // geometry window, not the server's animation dispatch timestamp.
        if swing.stopped
            || e.occurred_at_ms < (e.phase_at_ms as f64 + f64::from(swing.start_ms))
            || e.occurred_at_ms > (e.phase_at_ms as f64 + f64::from(swing.end_ms))
            || swing.cutoff_ms.is_some_and(|at| e.occurred_at_ms > at)
            || self
                .world
                .as_ref()
                .and_then(|w| w.actors.get(&e.actor_id))
                .is_none_or(|p| p.entity != e.source_entity)
        {
            return Ok(());
        }
        let swing = swing.clone();
        if e.target_id == 0 {
            if e.normal[2].abs() >= 0.5 && ground_height(e) <= 0.1 {
                let actor = self.actor_mut(e.actor_id)?;
                if !actor.swings[index].ground_played {
                    actor.swings[index].ground_played = true;
                    outputs.push(Output::Contact {
                        attacker: e.actor_id,
                        target: 0,
                        ground: true,
                        occurred_at_ms: e.occurred_at_ms,
                    });
                    outputs.push(Output::SurfaceHit {
                        attacker: e.actor_id,
                        weapon_id: swing.weapon_id,
                        ground: true,
                        evidence: Box::new(e.clone()),
                    });
                }
            } else {
                self.actor_mut(e.actor_id)?.swings[index].stopped = true;
                self.action(
                    e.actor_id,
                    Command::Terrain {
                        intent: swing.intent,
                        weapon_id: swing.weapon_id,
                    },
                    Some(e.occurred_at_ms),
                    outputs,
                )?;
                outputs.push(Output::Contact {
                    attacker: e.actor_id,
                    target: 0,
                    ground: false,
                    occurred_at_ms: e.occurred_at_ms,
                });
                outputs.push(Output::SurfaceHit {
                    attacker: e.actor_id,
                    weapon_id: swing.weapon_id,
                    ground: false,
                    evidence: Box::new(e.clone()),
                });
            }
            return Ok(());
        }
        let Some(defender) = self.actors.get(&e.target_id) else {
            return Ok(());
        };
        if defender.life != Life::Alive
            || e.occurred_at_ms < defender.contact_life_at_ms as f64
            || swing.hits.contains(&e.target_id)
            || self
                .world
                .as_ref()
                .and_then(|w| w.actors.get(&e.target_id))
                .is_none_or(|p| p.entity != e.target_entity)
        {
            return Ok(());
        }
        if defender.member.camp == source.member.camp {
            if e.defence_weapon_id == 0 {
                let source = self.actor_mut(e.actor_id)?;
                source.swings[index].hits.push(e.target_id);
                source.swings[index].allies += 1;
            }
            return Ok(());
        }
        let weapon = source
            .resources
            .damage
            .weapons
            .get(&swing.weapon_id)
            .ok_or_else(|| invalid("unknown swing weapon"))?;
        if swing.enemies >= resources.damage_rules().cleave_scale[weapon.cleave as usize].len() {
            return Ok(());
        }
        let pending = swing.pending_breaks.get(&e.target_id).filter(|pending| {
            pending.entity == e.target_entity && pending.life_at_ms == defender.contact_life_at_ms
        });
        if e.defence_weapon_id != 0 && pending.is_some() {
            return Ok(());
        }
        if e.defence_weapon_id != 0 && self.parry(e, &swing, index, config, resources, outputs)? {
            return Ok(());
        }
        if e.defence_weapon_id != 0 {
            return Ok(());
        }
        self.actor_mut(e.actor_id)?.swings[index]
            .hits
            .push(e.target_id);
        let mode = pending.map_or(DamageMode::Regular, |pending| {
            DamageMode::GuardBreak(pending.fraction)
        });
        self.actor_mut(e.actor_id)?.swings[index]
            .pending_breaks
            .remove(&e.target_id);
        self.apply_damage(e, &swing, config, resources, mode, outputs)
    }

    fn parry(
        &mut self,
        e: &ContactEvent,
        swing: &Swing,
        index: usize,
        config: &Config,
        resources: &BattleResources,
        outputs: &mut Vec<Output>,
    ) -> io::Result<bool> {
        // A later observed phase must not grant defence before it began.
        if e.target_phase_at_ms > e.occurred_at_ms {
            return Ok(reject_guard(
                e,
                swing,
                GuardRejectReason::FuturePhase,
                outputs,
            ));
        }
        let defender = &self.actors[&e.target_id];
        let target_action = defender.action.snapshot();
        if target_action
            .defence_interrupted_at_ms
            .is_some_and(|at| e.target_phase_at_ms < at && at <= e.occurred_at_ms)
        {
            return Ok(reject_guard(
                e,
                swing,
                GuardRejectReason::Interrupted,
                outputs,
            ));
        }
        let chamber =
            e.target_phase == Phase::Prepare && e.target_intent.is_some_and(Intent::is_attack);
        let holding = matches!(e.target_phase, Phase::Hold | Phase::ParrySuccess)
            && e.target_intent.is_some_and(Intent::is_guard);
        if !chamber && !holding {
            return Ok(reject_guard(
                e,
                swing,
                GuardRejectReason::NotDefending,
                outputs,
            ));
        }
        let Some(intent) = e.target_intent else {
            return Ok(reject_guard(
                e,
                swing,
                GuardRejectReason::NotDefending,
                outputs,
            ));
        };
        if let Intent::Guard(direction) = intent
            && !ordinary_guard_direction(direction, swing.intent)
        {
            return Ok(reject_guard(
                e,
                swing,
                GuardRejectReason::Direction,
                outputs,
            ));
        }
        if chamber
            && !guard::chamber_direction_matches(guard_intent(intent), guard_intent(swing.intent))
        {
            return Ok(reject_guard(
                e,
                swing,
                GuardRejectReason::Direction,
                outputs,
            ));
        }
        if chamber
            && (e.occurred_at_ms < e.phase_at_ms as f64
                || 3. * (e.occurred_at_ms - e.phase_at_ms as f64)
                    > f64::from(swing.strike_duration_ms))
        {
            return Ok(reject_guard(
                e,
                swing,
                GuardRejectReason::ChamberWindow,
                outputs,
            ));
        }
        let Some(defence) = defender.resources.damage.weapons.get(&e.defence_weapon_id) else {
            return Ok(reject_guard(
                e,
                swing,
                GuardRejectReason::MissingWeapon,
                outputs,
            ));
        };
        if (defence.shield_max > 0) != (intent == Intent::Shield)
            || defence.shield_max > 0
                && defender
                    .shields
                    .get(&e.defence_weapon_id)
                    .copied()
                    .unwrap_or(0)
                    == 0
        {
            return Ok(reject_guard(
                e,
                swing,
                GuardRejectReason::ShieldState,
                outputs,
            ));
        }
        if e.defence_weapon_id
            != if intent == Intent::Shield {
                target_action.offhand_id
            } else {
                target_action.weapon_id
            }
        {
            return Ok(reject_guard(
                e,
                swing,
                GuardRejectReason::EquipmentChanged,
                outputs,
            ));
        }
        let attack = &self.actors[&e.actor_id].resources.damage.weapons[&swing.weapon_id];
        let context = guard::AttackContext {
            attack_grade: attack.attack_grade,
            held_ms: swing.held_ms as f64,
            traits: attack.traits,
            long_pole: attack.long_pole,
        };
        let performance = guard::attack_performance(
            &context,
            guard_intent(swing.intent),
            e.target_attacking(),
            swing.allies,
            resources.guard_rules(),
        )
        .map_err(invalid)?;
        let grade = if chamber {
            defence.attack_grade
        } else {
            defence.defence_grade
        };
        let gap = i32::from(performance.value()) - i32::from(grade.value());
        let (attack_weight, defence_weight, shield_max) =
            (attack.weight, defence.weight, defence.shield_max);
        let fraction =
            guard::damage_fraction(gap, chamber, resources.guard_rules()).map_err(invalid)?;
        self.mark_attacked(e.target_id, e.occurred_at_ms)?;
        outputs.push(Output::Parry {
            outcome: if fraction > 0. {
                ParryOutcome::Broken
            } else if chamber {
                ParryOutcome::Chambered
            } else {
                ParryOutcome::Blocked
            },
            attacker: e.actor_id,
            target: e.target_id,
            weapon_id: swing.weapon_id,
            defence_weapon_id: e.defence_weapon_id,
            evidence: Box::new(e.clone()),
        });
        if fraction > 0. && e.guard_volume {
            let life_at_ms = self.actors[&e.target_id].contact_life_at_ms;
            self.actor_mut(e.actor_id)?.swings[index]
                .pending_breaks
                .insert(
                    e.target_id,
                    PendingGuardBreak {
                        entity: e.target_entity,
                        life_at_ms,
                        fraction,
                    },
                );
            return Ok(false);
        }
        if fraction > 0. {
            self.actor_mut(e.actor_id)?.swings[index]
                .hits
                .push(e.target_id);
            self.apply_damage(
                e,
                swing,
                config,
                resources,
                DamageMode::GuardBreak(fraction),
                outputs,
            )?;
            return Ok(false);
        }
        self.actor_mut(e.actor_id)?.swings[index].stopped = true;
        self.action(
            e.actor_id,
            Command::Parried {
                target_id: e.target_id,
                intent: swing.intent,
                weapon_id: swing.weapon_id,
            },
            Some(e.occurred_at_ms),
            outputs,
        )?;
        if !chamber && target_action.intent == Some(intent) && !target_action.released {
            self.action(
                e.target_id,
                Command::ParrySuccess {
                    target_id: e.actor_id,
                    intent,
                    minimum_ms: if gap == 1 {
                        u16::try_from(resources.guard_rules().guard_stun_ms).map_err(invalid)?
                    } else {
                        0
                    },
                },
                Some(e.occurred_at_ms),
                outputs,
            )?;
        }
        let cost = guard::consumption(gap, attack_weight, defence_weight, resources.guard_rules())
            .map_err(invalid)?;
        let consumed = self.guard_meters[&e.target_id]
            .consume(cost, e.occurred_at_ms, resources.guard_rules())
            .map_err(invalid)?;
        self.guard_meters.insert(e.target_id, consumed.meter);
        outputs.push(Output::Guard {
            actor_id: e.target_id,
            value: consumed.meter.value(),
        });
        let restored = self.guard_meters[&e.actor_id]
            .restore(e.occurred_at_ms, resources.guard_rules())
            .map_err(invalid)?;
        self.guard_meters.insert(e.actor_id, restored);
        outputs.push(Output::Guard {
            actor_id: e.actor_id,
            value: restored.value(),
        });
        if consumed.exhausted {
            let duration = u16::try_from(resources.guard_rules().exhaustion_ms).map_err(invalid)?;
            self.action(
                e.target_id,
                Command::Hurt {
                    source_id: e.actor_id,
                    kind: HurtKind::Exhaustion,
                    duration_ms: duration,
                    movement_ms: duration,
                    contact_at_ms: e.occurred_at_ms,
                },
                Some(e.occurred_at_ms),
                outputs,
            )?;
        }
        self.grant_buff(e.target_id, "riposte", e.occurred_at_ms, resources)?;
        if shield_max > 0 {
            let result = self.calculate_damage(
                e,
                swing,
                resources,
                1.,
                Some(self.actors[&e.target_id].shields[&e.defence_weapon_id]),
            )?;
            let health = {
                let actor = self.actor_mut(e.target_id)?;
                let value = actor
                    .shields
                    .get_mut(&e.defence_weapon_id)
                    .expect("validated shield");
                *value = value.saturating_sub(result.shield_damage);
                let health = *value;
                if health == 0 {
                    actor.action = actor
                        .action
                        .break_shield(e.defence_weapon_id, &actor.resources.loadout)
                        .map_err(invalid)?;
                }
                health
            };
            outputs.push(Output::Shield {
                actor_id: e.target_id,
                weapon_id: e.defence_weapon_id,
                health,
            });
            if health == 0 {
                self.action(
                    e.target_id,
                    Command::Hurt {
                        source_id: e.actor_id,
                        kind: HurtKind::ShieldBreak,
                        duration_ms: 1000,
                        movement_ms: 1000,
                        contact_at_ms: e.occurred_at_ms,
                    },
                    Some(e.occurred_at_ms),
                    outputs,
                )?;
            }
        }
        outputs.push(Output::Contact {
            attacker: e.actor_id,
            target: e.target_id,
            ground: false,
            occurred_at_ms: e.occurred_at_ms,
        });
        Ok(true)
    }

    fn calculate_damage(
        &self,
        e: &ContactEvent,
        swing: &Swing,
        resources: &BattleResources,
        scale: f64,
        shield: Option<u32>,
    ) -> io::Result<damage::DamageResult> {
        let source = &self.actors[&e.actor_id];
        let target = &self.actors[&e.target_id];
        let weapon = &source.resources.damage.weapons[&swing.weapon_id];
        let attack = weapon
            .attacks
            .get(&intent_id(swing.intent))
            .ok_or_else(|| invalid("missing attack damage"))?;
        let dx = e.source_position[0] - e.target_position[0];
        let dy = e.source_position[1] - e.target_position[1];
        let distance = dx.hypot(dy);
        let yaw = e.target_heading.to_radians();
        let facing = if distance < 0.01 {
            1.
        } else {
            (dx * yaw.cos() - dy * yaw.sin()) / distance
        };
        let buffs = swing
            .buffs
            .values()
            .filter(|b| b.since_ms < swing.began_at_ms as f64 && e.occurred_at_ms < b.until_ms)
            .map(|b| b.multiplier)
            .product::<f64>();
        let hit = damage::Hit {
            base: attack.base,
            damage_type: attack.damage_type,
            health: target.health,
            armor: target.resources.damage.armor,
            region: e.region,
            held_ms: swing.held_ms as f64,
            peak_ms: weapon.peak_ms,
            phase: ((e.occurred_at_ms - e.phase_at_ms as f64 - f64::from(swing.start_ms))
                / f64::from(swing.end_ms - swing.start_ms))
            .clamp(0., 1.),
            source_velocity: e.source_velocity,
            target_velocity: e.target_velocity,
            weapon_velocity: e.weapon_velocity,
            continuous: e.velocity_ready,
            traits: weapon.traits,
            buff_multiplier: buffs,
            health_ratio: source.health as f64 / f64::from(source.resources.damage.max_health),
            airborne: e.airborne,
            target_attacking: e.target_attacking(),
            long_pole: weapon.long_pole,
            down_attack: swing.intent == Intent::Attack(Direction::Down),
            rear: facing <= -0.5,
            friendly: false,
            allies: swing.allies,
            enemy_index: swing.enemies,
            cleave: weapon.cleave,
            shield_health: shield,
            damage_scale: scale,
        };
        let mut result = damage::calculate(&hit, resources.damage_rules()).map_err(invalid)?;
        if shield.is_none() {
            let performance = guard::attack_performance(
                &guard::AttackContext {
                    attack_grade: weapon.attack_grade,
                    held_ms: swing.held_ms as f64,
                    traits: weapon.traits,
                    long_pole: weapon.long_pole,
                },
                guard_intent(swing.intent),
                e.target_attacking(),
                swing.allies,
                resources.guard_rules(),
            )
            .map_err(invalid)?;
            let hero = &target.resources.damage;
            let reaction = guard::body_reaction(
                performance,
                hero.armor_type,
                e.target_airborne,
                hero.siege_immunity,
                hero.fortified,
                resources.guard_rules(),
            )
            .map_err(invalid)?;
            result.interrupt = reaction.interrupt;
            result.movement_ms = reaction.movement_ms;
            result.knockdown = reaction.knockdown;
        }
        Ok(result)
    }

    fn apply_damage(
        &mut self,
        e: &ContactEvent,
        swing: &Swing,
        config: &Config,
        resources: &BattleResources,
        mode: DamageMode,
        outputs: &mut Vec<Output>,
    ) -> io::Result<()> {
        let (scale, guard_break) = match mode {
            DamageMode::Regular => (1., false),
            DamageMode::GuardBreak(scale) => (scale, true),
        };
        let result = self.calculate_damage(e, swing, resources, scale, None)?;
        if result.hp_damage == 0 {
            return Ok(());
        }
        let index = self.actors[&e.actor_id]
            .swings
            .iter()
            .position(|item| item.sequence == swing.sequence)
            .ok_or_else(|| invalid("missing settled swing"))?;
        self.actor_mut(e.actor_id)?.swings[index].enemies += 1;
        self.mark_attacked(e.target_id, e.occurred_at_ms)?;
        let previous_health = self.actors[&e.target_id].health;
        let health = {
            let target = self.actor_mut(e.target_id)?;
            target.health = target.health.saturating_sub(result.hp_damage);
            target.health
        };
        outputs.push(Output::Damage {
            attacker: e.actor_id,
            target: e.target_id,
            sequence: swing.sequence,
            health,
            previous_health,
            weapon_id: swing.weapon_id,
            evidence: Box::new(e.clone()),
            guard_break,
            result: result.clone(),
        });
        let meter = self.guard_meters[&e.actor_id]
            .restore(e.occurred_at_ms, resources.guard_rules())
            .map_err(invalid)?;
        self.guard_meters.insert(e.actor_id, meter);
        outputs.push(Output::Guard {
            actor_id: e.actor_id,
            value: meter.value(),
        });
        self.grant_buff(e.actor_id, "combo", e.occurred_at_ms, resources)?;
        if health == 0 {
            self.grant_buff(e.actor_id, "rage", e.occurred_at_ms, resources)?;
            self.kill(e.actor_id, e.target_id, config, outputs)?;
        } else if result.interrupt || guard_break {
            let movement_ms = u16::try_from(result.movement_ms).map_err(invalid)?;
            let duration = self.actors[&e.target_id].resources.damage.weapons
                [&self.actors[&e.target_id].action.snapshot().weapon_id]
                .hurt_ms;
            self.action(
                e.target_id,
                Command::Hurt {
                    source_id: e.actor_id,
                    kind: match (guard_break, result.knockdown) {
                        (false, false) => HurtKind::Hit,
                        (false, true) => HurtKind::Knockdown,
                        (true, false) => HurtKind::GuardBreak,
                        (true, true) => HurtKind::GuardBreakKnockdown,
                    },
                    duration_ms: duration.max(movement_ms),
                    movement_ms,
                    contact_at_ms: e.occurred_at_ms,
                },
                Some(e.occurred_at_ms),
                outputs,
            )?;
        }
        if self.actors[&e.target_id].member.kind == MemberKind::Bot {
            let world = self.ai_world(false);
            self.ai
                .notice_damage(
                    e.target_id,
                    e.actor_id,
                    result.hp_damage as f64,
                    self.now_ms,
                    &world,
                )
                .map_err(invalid)?;
        }
        Ok(())
    }

    fn grant_buff(
        &mut self,
        id: u32,
        name: &'static str,
        at_ms: f64,
        resources: &BattleResources,
    ) -> io::Result<()> {
        let hero = &self.actors[&id].resources.damage;
        let rules = resources.damage_buff_rules();
        let (enabled, rule) = match name {
            "riposte" => (hero.riposte, rules.riposte),
            "combo" => (hero.combo, rules.combo),
            "rage" => (hero.rage, rules.rage),
            _ => return Err(invalid("unknown combat buff")),
        };
        if !enabled {
            return Ok(());
        }
        let buff = Buff {
            since_ms: at_ms,
            until_ms: at_ms + f64::from(rule.duration_ms),
            multiplier: rule.multiplier,
        };
        let actor = self.actor_mut(id)?;
        actor.buffs.insert(name, buff);
        for swing in &mut actor.swings {
            if at_ms < swing.began_at_ms as f64 && (swing.began_at_ms as f64) < buff.until_ms {
                swing.buffs.insert(name, buff);
            }
        }
        Ok(())
    }
}

fn ground_height(e: &ContactEvent) -> f64 {
    e.normal
        .into_iter()
        .zip(e.position)
        .zip(e.source_position)
        .map(|((n, p), s)| n * (p - s))
        .sum::<f64>()
        / e.normal[2]
}
