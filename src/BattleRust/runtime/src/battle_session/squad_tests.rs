use super::*;

pub(crate) fn selecting_squad_fixture() -> BattleSession {
    let (base, _) = selection::tests::fixture();
    let bundle = ResourceBundle::from_slice(include_bytes!(
        "../../../../../tests/fixtures/resources/bundle-v1.json"
    ))
    .unwrap();
    let members = vec![
        base.config.members[0].clone(),
        Member {
            actor_id: 2,
            kind: MemberKind::Bot,
            ..base.config.members[0].clone()
        },
    ];
    let mut session = BattleSession::new(
        &bundle,
        base.resources,
        Config {
            members,
            ..base.config
        },
    )
    .unwrap();
    assert_eq!(session.squads().for_actor(1).unwrap().id, 1);
    let actor = session.state.actor_mut(1).unwrap();
    actor.life = Life::Selecting { died_at_ms: 1000 };
    actor.health = 0;
    session
}

fn fixture() -> BattleSession {
    let (base, _) = selection::tests::fixture();
    let bundle = ResourceBundle::from_slice(include_bytes!(
        "../../../../../tests/fixtures/resources/bundle-v1.json"
    ))
    .unwrap();
    let members = (1..=8)
        .map(|id| Member {
            actor_id: id,
            camp: if id == 8 { 2 } else { 1 },
            kind: if id <= 2 {
                MemberKind::Human
            } else {
                MemberKind::Bot
            },
            sample_source_id: if id == 2 { 2 } else { 1 },
            ..base.config.members[0].clone()
        })
        .collect();
    BattleSession::new(
        &bundle,
        base.resources,
        Config {
            members,
            ..base.config
        },
    )
    .unwrap()
}

fn command(session: &mut BattleSession, actor_id: u32, command: SquadCommand) -> i32 {
    let mut result = None;
    session
        .dispatch(
            7,
            1000,
            Input::SourceSquad {
                source_id: u64::from(actor_id),
                actor_id,
                command,
            },
            |events| {
                result = events.iter().find_map(|event| match event {
                    Output::Squad { result, .. } => Some(*result),
                    _ => None,
                });
                Ok(())
            },
        )
        .unwrap();
    result.unwrap()
}

#[test]
fn squads_local_player_starts_in_first_squad_and_other_squads_keep_a_seat() {
    for camp in [1, 2] {
        for bot_count in [0, 1, 4, 5, 8, 9] {
            let base = fixture();
            let bundle = ResourceBundle::from_slice(include_bytes!(
                "../../../../../tests/fixtures/resources/bundle-v1.json"
            ))
            .unwrap();
            let player = Member {
                camp,
                ..base.config.members[0].clone()
            };
            let members = std::iter::once(player.clone())
                .chain((2..bot_count + 2).rev().map(|actor_id| Member {
                    actor_id,
                    kind: MemberKind::Bot,
                    ..player.clone()
                }))
                .chain(std::iter::once(Member {
                    actor_id: 32,
                    camp: 3 - camp,
                    kind: MemberKind::Bot,
                    ..player.clone()
                }))
                .collect();
            let mut session = BattleSession::new(
                &bundle,
                base.resources,
                Config {
                    members,
                    ..base.config
                },
            )
            .unwrap();
            let first = session
                .squads()
                .for_actor(1)
                .expect("local player has a squad");
            assert_eq!((first.camp, first.id), (camp, 1));
            assert_eq!(first.members.len(), 1 + bot_count.min(4) as usize);
            assert!(session.squads().entries.iter().all(|squad| {
                (squad.camp == camp && squad.id == 1) || squad.members.len() <= 4
            }));
            assert_eq!(command(&mut session, 1, SquadCommand::Join(1)), 810004);
            if bot_count > 4 {
                assert_eq!(command(&mut session, 1, SquadCommand::Leave), 0);
                assert_eq!(command(&mut session, 1, SquadCommand::Join(2)), 0);
                assert_eq!(session.squads().for_actor(1).unwrap().id, 2);
            }
        }
    }
}

#[test]
fn squads_auto_group_bots_by_camp_and_leave_a_human_seat() {
    let mut session = fixture();
    let squads = session.squads();
    assert_eq!(squads.entries.len(), 3);
    assert_eq!(squads.entries[0].members.len(), 4);
    assert!(squads.for_actor(1).is_none());
    assert_eq!(command(&mut session, 1, SquadCommand::Join(1)), 0);
    assert_eq!(command(&mut session, 2, SquadCommand::Join(1)), 810007);
    assert_eq!(command(&mut session, 1, SquadCommand::Create), 810004);
    assert_eq!(session.squads().for_actor(1).unwrap().members.len(), 5);
}

#[test]
fn squads_create_join_leave_and_reassign_leader() {
    let mut session = fixture();
    assert_eq!(command(&mut session, 1, SquadCommand::Create), 0);
    let id = session.squads().for_actor(1).unwrap().id;
    assert_eq!(command(&mut session, 2, SquadCommand::Join(id)), 0);
    assert_eq!(command(&mut session, 2, SquadCommand::Dismiss), 810009);
    assert_eq!(command(&mut session, 1, SquadCommand::Leave), 0);
    assert_eq!(session.squads().for_actor(2).unwrap().leader, 2);
    assert_eq!(command(&mut session, 2, SquadCommand::Leave), 0);
    assert!(
        session
            .squads()
            .entries
            .iter()
            .all(|s| s.camp != 1 || s.id != id)
    );
}

#[test]
fn squads_source_validation_publication_rollback_and_match_isolation() {
    let mut session = fixture();
    let before = session.squads().clone();
    let input = |source_id| Input::SourceSquad {
        source_id,
        actor_id: 1,
        command: SquadCommand::Create,
    };
    assert!(session.dispatch(7, 1000, input(2), |_| Ok(())).is_err());
    assert!(session.dispatch(8, 1000, input(1), |_| Ok(())).is_err());
    assert!(
        session
            .dispatch(7, 1000, input(1), |_| Err(io::Error::other("full")))
            .is_err()
    );
    assert_eq!(session.squads(), &before);
    assert_eq!(command(&mut session, 1, SquadCommand::Create), 0);
    assert_eq!(fixture().squads(), &before);
}

fn prepare_spawn() -> BattleSession {
    let mut session = fixture();
    assert_eq!(command(&mut session, 1, SquadCommand::Join(1)), 0);
    session.state.actor_mut(1).unwrap().life = Life::Selecting { died_at_ms: 1000 };
    session.state.actor_mut(1).unwrap().health = 0;
    let actors = (1..=8)
        .map(|id| {
            (
                id,
                Pose {
                    entity: u64::from(id),
                    position: [if id == 3 { 0. } else { 30. }, 20., 0.],
                    heading: 90.,
                    sequence: 1,
                    contact: ai::ContactObservation::unavailable(),
                },
            )
        })
        .collect();
    session.state.world = Some(Arc::new(World {
        epoch: 7,
        sampled_at_ms: 12000,
        actors,
    }));
    session.state.actor_sampled_at_ms = (1..=8).map(|id| (id, 12000)).collect();
    session
}

fn spawn(session: &mut BattleSession, target_id: u32, position: [f64; 3]) -> Vec<Output> {
    let mut outputs = Vec::new();
    session
        .dispatch(
            7,
            12000,
            Input::SourceSquadSpawn {
                source_id: 1,
                actor_id: 1,
                target_id,
                position,
            },
            |events| {
                outputs = events.to_vec();
                Ok(())
            },
        )
        .unwrap();
    outputs
}

#[test]
fn squad_spawn_uses_selected_position_and_resets_life_state() {
    let mut session = prepare_spawn();
    let outputs = spawn(&mut session, 3, [6., 20., 0.]);
    assert!(outputs.iter().any(|e| matches!(
        e,
        Output::Life {
            actor_id: 1,
            state: Life::Spawning { .. }
        }
    )));
    let actor = session.actor(1).unwrap();
    assert_eq!(actor.health, 100);
    assert!(matches!(actor.life, Life::Spawning { spawn, .. } if spawn.position == [6., 20., 0.]));
}

#[test]
fn squad_spawn_rejects_enemy_nonmember_dead_stale_and_recently_attacked_targets() {
    for reason in 0..7 {
        let mut session = prepare_spawn();
        let target = match reason {
            0 => 8,
            1 => 7,
            2 => {
                session.state.actor_mut(3).unwrap().life = Life::Selecting { died_at_ms: 1000 };
                3
            }
            3 => {
                session.state.actor_sampled_at_ms.insert(3, 11000);
                3
            }
            4 => {
                session.state.actor_mut(3).unwrap().last_attacked_at_ms = Some(2001);
                3
            }
            5 => 1,
            _ => 999,
        };
        assert!(
            spawn(&mut session, target, [6., 20., 0.])
                .iter()
                .any(|e| matches!(e, Output::SpawnRejected { actor_id: 1, .. })),
            "reason {reason}"
        );
        assert!(matches!(
            session.actor(1).unwrap().life,
            Life::Selecting { .. }
        ));
        assert_eq!(session.actor(1).unwrap().health, 0);
    }
    let mut session = prepare_spawn();
    session.state.actor_mut(3).unwrap().last_attacked_at_ms = Some(2000);
    spawn(&mut session, 3, [6., 20., 0.]);
    assert!(matches!(
        session.actor(1).unwrap().life,
        Life::Spawning { .. }
    ));
}

#[test]
fn squad_spawn_rejects_bad_distance_height_terrain_and_occupancy() {
    for position in [
        [4.9, 20., 0.],
        [10.1, 20., 0.],
        [6., 20., 1.1],
        [f64::NAN, 20., 0.],
        [6., 20., 0.],
    ] {
        let mut session = prepare_spawn();
        if position == [6., 20., 0.] {
            Arc::make_mut(session.state.world.as_mut().unwrap())
                .actors
                .get_mut(&4)
                .unwrap()
                .position = position;
        }
        assert!(
            spawn(&mut session, 3, position)
                .iter()
                .any(|e| matches!(e, Output::SpawnRejected { .. }))
        );
        assert!(matches!(
            session.actor(1).unwrap().life,
            Life::Selecting { .. }
        ));
    }
    let mut session = prepare_spawn();
    Arc::make_mut(session.state.world.as_mut().unwrap())
        .actors
        .get_mut(&3)
        .unwrap()
        .position = [99., 20., 0.];
    assert!(
        spawn(&mut session, 3, [105., 20., 0.])
            .iter()
            .any(|e| matches!(e, Output::SpawnRejected { result: 810016, .. }))
    );
}

#[test]
fn squad_spawn_revalidates_membership_movement_and_life_before_committing() {
    for reason in 0..4 {
        let mut session = prepare_spawn();
        match reason {
            0 => {
                Arc::make_mut(&mut session.state.squads).entries[0]
                    .members
                    .retain(|m| m.actor_id != 3);
            }
            1 => {
                Arc::make_mut(session.state.world.as_mut().unwrap())
                    .actors
                    .get_mut(&3)
                    .unwrap()
                    .position = [20., 20., 0.];
            }
            2 => {
                session.state.actor_mut(1).unwrap().life = Life::Dead {
                    died_at_ms: 5000,
                    respawn_at_ms: 13000,
                };
            }
            _ => {
                session.state.actor_mut(1).unwrap().life = Life::Alive;
            }
        }
        assert!(
            spawn(&mut session, 3, [6., 20., 0.])
                .iter()
                .any(|e| matches!(e, Output::SpawnRejected { .. }))
        );
    }
    let mut session = prepare_spawn();
    let input = || Input::SourceSquadSpawn {
        source_id: 1,
        actor_id: 1,
        target_id: 3,
        position: [6., 20., 0.],
    };
    assert!(
        session
            .dispatch(7, 12000, input(), |_| Err(io::Error::other("full")))
            .is_err()
    );
    assert!(matches!(
        session.actor(1).unwrap().life,
        Life::Selecting { .. }
    ));
    session.dispatch(7, 12000, input(), |_| Ok(())).unwrap();
    assert!(matches!(
        session.actor(1).unwrap().life,
        Life::Spawning { .. }
    ));
}

#[test]
fn squad_spawn_uses_known_occupancy_without_requiring_unrelated_actor_samples() {
    let mut session = prepare_spawn();
    session.state.actor_sampled_at_ms.remove(&8);
    spawn(&mut session, 3, [6., 20., 0.]);
    assert!(matches!(
        session.actor(1).unwrap().life,
        Life::Spawning { .. }
    ));
}

#[test]
fn squads_reject_enemy_only_ids_missing_ids_and_locked_squads() {
    let mut session = fixture();
    Arc::make_mut(&mut session.state.squads)
        .entries
        .iter_mut()
        .find(|s| s.camp == 2)
        .unwrap()
        .id = 40;
    for id in [0, -1, 40, 41] {
        assert_eq!(command(&mut session, 1, SquadCommand::Join(id)), 810010);
    }
    assert_eq!(command(&mut session, 1, SquadCommand::Leave), 810005);
    assert_eq!(command(&mut session, 1, SquadCommand::Create), 0);
    let id = session.squads().for_actor(1).unwrap().id;
    assert_eq!(
        command(&mut session, 1, SquadCommand::SetJoinable(false)),
        0
    );
    assert_eq!(command(&mut session, 2, SquadCommand::Join(id)), 810008);
    assert_eq!(command(&mut session, 1, SquadCommand::SetJoinable(true)), 0);
    assert_eq!(command(&mut session, 2, SquadCommand::Join(id)), 0);
    assert_eq!(
        command(&mut session, 2, SquadCommand::SetJoinable(false)),
        810009
    );
    assert_eq!(command(&mut session, 1, SquadCommand::Dismiss), 0);
    assert!(session.squads().for_actor(2).is_none());
}

#[test]
fn squad_attack_cooldown_uses_valid_enemy_damage_and_parries_not_invalid_contacts() {
    use t7r_battle_core::action::Command;
    for parry in [false, true] {
        let mut session = prepare_spawn();
        session.state.now_ms = 12000;
        session
            .state
            .action(
                8,
                Command::Start {
                    intent: Intent::Attack(Direction::Up),
                    released: true,
                },
                None,
                &mut Vec::new(),
            )
            .unwrap();
        let due = session.actor(8).unwrap().action.phase_due_ms.unwrap();
        session.state.now_ms = due;
        session.state.timers(&mut Vec::new()).unwrap();
        let attack = session.actor(8).unwrap().action;
        session.state.now_ms = due + 250;
        let event = contacts::ContactEvent {
            actor_id: 8,
            target_id: 3,
            source_entity: 8,
            target_entity: 3,
            sequence: attack.sequence,
            phase_at_ms: due,
            target_intent: parry.then_some(Intent::Guard(Direction::Up)),
            target_phase: if parry { Phase::Hold } else { Phase::Idle },
            target_phase_at_ms: 12000.,
            target_observed_at_ms: None,
            source_observed_at_ms: None,
            occurred_at_ms: (due + 250) as f64,
            along: 0.5,
            defence_weapon_id: if parry { 701 } else { 0 },
            region: Region::Torso,
            body_index: -1,
            source_position: [1., 20., 0.],
            target_position: [0., 20., 0.],
            source_heading: 0.,
            target_heading: 180.,
            source_velocity: [0.; 3],
            target_velocity: [0.; 3],
            weapon_velocity: [0.; 3],
            velocity_ready: false,
            airborne: false,
            target_airborne: false,
            position: [0.5, 20., 1.],
            normal: [-1., 0., 0.],
            direction: [1., 0., 0.],
            material: 0,
        };
        let mut wrong = event.clone();
        wrong.target_entity = 999;
        session
            .state
            .settle_contact_events(
                event.occurred_at_ms,
                vec![wrong],
                &session.config,
                &session.resources,
                &mut Vec::new(),
            )
            .unwrap();
        assert_eq!(session.state.actors[&3].last_attacked_at_ms, None);
        let mut outputs = Vec::new();
        session
            .state
            .settle_contact_events(
                event.occurred_at_ms,
                vec![event.clone()],
                &session.config,
                &session.resources,
                &mut outputs,
            )
            .unwrap();
        assert!(
            outputs
                .iter()
                .any(|e| matches!(e, Output::Damage { .. } | Output::Parry { .. })),
            "parry={parry}"
        );
        assert_eq!(
            session.state.actors[&3].last_attacked_at_ms,
            Some(due + 250)
        );
        assert_eq!(session.state.actors[&8].last_attacked_at_ms, None);
    }
}
