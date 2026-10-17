use super::*;

#[derive(Clone)]
pub(crate) struct PreparedHero {
    pub member: Member,
    resources: Arc<profiles::ActorResources>,
}

impl PreparedHero {
    pub(crate) fn max_health(&self) -> u32 {
        self.resources.damage.max_health
    }

    pub(crate) fn offhand_id(&self) -> u32 {
        action::ActionState::new(
            self.member.actor_id,
            &self.resources.loadout,
            self.member.weapon_id,
            1,
        )
        .expect("prepared loadout")
        .snapshot()
        .offhand_id
    }

    pub(crate) fn shields(&self) -> Vec<(u32, u32)> {
        self.resources
            .damage
            .weapons
            .iter()
            .filter(|(_, weapon)| weapon.shield_max > 0)
            .map(|(&id, weapon)| (id, weapon.shield_max))
            .collect()
    }

    pub(crate) fn new(bundle: &ResourceBundle, member: Member) -> io::Result<Self> {
        let resources = Arc::new(profiles::parse(bundle, &member)?);
        action::ActionState::new(member.actor_id, &resources.loadout, member.weapon_id, 1)
            .map_err(invalid)?;
        Ok(Self { member, resources })
    }
}

impl BattleSession {
    pub(crate) fn prepare_hero(&mut self, epoch: u64, selected: &PreparedHero) -> io::Result<bool> {
        let Some(next) = self.selection_state(epoch, selected, true)? else {
            return Ok(false);
        };
        // Preparation remains unpublished; the first running checkpoint includes the final choice.
        self.state = next;
        Ok(true)
    }

    pub(crate) fn select_hero(
        &mut self,
        epoch: u64,
        now_ms: u64,
        selected: &PreparedHero,
        publish: impl FnOnce(&[Output], &OutputContext) -> io::Result<()>,
    ) -> io::Result<bool> {
        if epoch != self.config.epoch || now_ms < self.state.now_ms {
            return Err(invalid("stale hero selection epoch or time"));
        }
        let Some(mut next) = self.selection_state(epoch, selected, false)? else {
            return Ok(false);
        };
        next.now_ms = now_ms;
        let mut events = Vec::new();
        next.append_state_output(&self.state, self.config.epoch, &mut events)?;
        let context = next.output_context();
        publish(&events, &context)?;
        self.state = next;
        Ok(true)
    }

    fn selection_state(
        &self,
        epoch: u64,
        selected: &PreparedHero,
        initial: bool,
    ) -> io::Result<Option<State>> {
        if epoch != self.config.epoch {
            return Err(invalid("stale hero selection epoch"));
        }
        let member = &selected.member;
        self.state
            .validate_source(member.sample_source_id, member.actor_id, MemberKind::Human)?;
        let current = &self.state.actors[&member.actor_id];
        if member.kind != MemberKind::Human || member.camp != current.member.camp {
            return Err(invalid("hero selection changes member identity"));
        }
        let ready = if initial {
            self.state.state_revision == 0
                && self.state.now_ms == self.config.started_at_ms
                && self.state.world.is_none()
                && current.life == Life::Alive
        } else {
            matches!(current.life, Life::Selecting { .. })
        };
        if self.state.settlement.is_some() || !ready {
            return Ok(None);
        }
        let mut next = self.state.clone();
        let actor = next.actor_mut(member.actor_id)?;
        actor.action = action::ActionState::new(
            member.actor_id,
            &selected.resources.loadout,
            member.weapon_id,
            actor.action.snapshot().sequence,
        )
        .map_err(invalid)?;
        actor.member = member.clone();
        actor.resources = selected.resources.clone();
        if initial {
            actor.health = selected.max_health();
        }
        actor.swings.clear();
        actor.buffs.clear();
        actor.release_at_ms = None;
        actor.shields = actor
            .resources
            .damage
            .weapons
            .iter()
            .filter(|(_, weapon)| weapon.shield_max > 0)
            .map(|(&id, weapon)| (id, weapon.shield_max))
            .collect();
        next.guard_meters.insert(
            member.actor_id,
            GuardMeter::new(self.resources.guard_rules()).map_err(invalid)?,
        );
        Ok(Some(next))
    }
}

#[cfg(test)]
pub(super) mod tests {
    use super::*;

    pub(crate) fn fixture() -> (BattleSession, PreparedHero) {
        let bundle = ResourceBundle::from_slice(include_bytes!(
            "../../../../../tests/fixtures/resources/bundle-v1.json"
        ))
        .unwrap();
        let resources = Arc::new(BattleResources::prepare(&bundle).unwrap());
        let member = Member {
            actor_id: 1,
            kind: MemberKind::Human,
            sample_source_id: 1,
            camp: 1,
            hero_id: 1101,
            weapon_id: 701,
            equipment: vec![EquipmentItem {
                slot: 1,
                weapon_id: 701,
            }],
        };
        let selected = PreparedHero::new(
            &bundle,
            Member {
                hero_id: 2102,
                weapon_id: 702,
                equipment: vec![EquipmentItem {
                    slot: 3,
                    weapon_id: 702,
                }],
                ..member.clone()
            },
        )
        .unwrap();
        let session = BattleSession::new(
            &bundle,
            resources,
            Config {
                epoch: 7,
                started_at_ms: 1000,
                duration_ms: 120_000,
                kill_target: 10,
                seed: 42,
                members: vec![member],
            },
        )
        .unwrap();
        (session, selected)
    }

    pub(crate) fn selecting_fixture() -> BattleSession {
        let (mut session, _) = fixture();
        let actor = session.state.actor_mut(1).unwrap();
        actor.life = Life::Selecting { died_at_ms: 1000 };
        actor.health = 0;
        session
    }

    #[test]
    fn selection_commits_hero_equipment_and_resources_together_or_rolls_back() {
        let (mut session, selected) = fixture();
        let actor = session.state.actor_mut(1).unwrap();
        actor.life = Life::Selecting { died_at_ms: 1000 };
        actor.health = 0;
        actor.score = Score {
            kills: 3,
            deaths: 1,
            points: 300,
            streak: 0,
        };
        let before = session.actor(1).unwrap();
        let error = session
            .select_hero(7, 9000, &selected, |events, _| {
                let Output::State(state) = &events[0] else {
                    panic!("missing state")
                };
                assert_eq!(
                    (state.actors[0].hero_id, state.actors[0].weapon_id),
                    (2102, 702)
                );
                Err(io::Error::new(
                    io::ErrorKind::WouldBlock,
                    "output queue full",
                ))
            })
            .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::WouldBlock);
        assert_eq!(session.actor(1).unwrap(), before);
        assert!(
            session
                .select_hero(7, 9000, &selected, |_, _| Ok(()))
                .unwrap()
        );
        let after = session.actor(1).unwrap();
        assert_eq!(
            (after.hero_id, after.action.weapon_id, after.health),
            (2102, 702, 0)
        );
        assert_eq!(
            (after.score, after.life, after.action.sequence),
            (before.score, before.life, before.action.sequence)
        );
        let actor = &session.state.actors[&1];
        assert_eq!(actor.member.equipment, selected.member.equipment);
        assert!(Arc::ptr_eq(&actor.resources, &selected.resources));
        assert!(actor.resources.damage.weapons.contains_key(&702));
        assert!(!actor.resources.damage.weapons.contains_key(&701));
        let world = |at, entity, position| {
            Input::World(Arc::new(World {
                epoch: 7,
                sampled_at_ms: at,
                actors: BTreeMap::from([(
                    1,
                    Pose {
                        entity,
                        position,
                        heading: 0.,
                        sequence: 1,
                        contact: ai::ContactObservation::unavailable(),
                    },
                )]),
            }))
        };
        session
            .dispatch(7, 9000, world(9000, 11, [0.; 3]), |_| Ok(()))
            .unwrap();
        session
            .dispatch(
                7,
                9001,
                Input::SourceSpawn {
                    source_id: 1,
                    actor_id: 1,
                },
                |_| Ok(()),
            )
            .unwrap();
        let spawned = session.actor(1).unwrap();
        let Life::Spawning { spawn, .. } = spawned.life else {
            panic!("missing respawn")
        };
        assert_eq!(
            (spawned.hero_id, spawned.action.weapon_id, spawned.health),
            (2102, 702, 100)
        );
        assert_eq!(spawned.action.sequence, before.action.sequence + 1);
        session
            .dispatch(7, 9002, world(9002, 12, spawn.position), |_| Ok(()))
            .unwrap();
        assert_eq!(session.actor(1).unwrap().life, Life::Alive);
        assert_eq!(session.actor(1).unwrap().score, before.score);
        assert!(
            !session
                .select_hero(7, 9003, &selected, |_, _| panic!("unexpected selection"))
                .unwrap()
        );
    }

    #[test]
    fn selection_requires_current_generation_bound_source_and_selecting_life() {
        let (mut session, selected) = fixture();
        assert!(
            !session
                .select_hero(7, 1000, &selected, |_, _| panic!("unexpected output"))
                .unwrap()
        );
        session.state.actor_mut(1).unwrap().life = Life::Selecting { died_at_ms: 1000 };
        assert!(
            session
                .select_hero(6, 9000, &selected, |_, _| Ok(()))
                .is_err()
        );
        let mut foreign = selected.clone();
        foreign.member.sample_source_id = 2;
        assert!(
            session
                .select_hero(7, 9000, &foreign, |_, _| Ok(()))
                .is_err()
        );
        foreign = selected.clone();
        foreign.member.camp = 2;
        assert!(
            session
                .select_hero(7, 9000, &foreign, |_, _| Ok(()))
                .is_err()
        );
        assert_eq!(session.actor(1).unwrap().hero_id, 1101);
    }
}
