use super::*;
use t7r_battle_core::damage;

fn fixture(map_id: u32) -> BattleSession {
    let (base, _) = selection::tests::fixture();
    let bundle = ResourceBundle::from_slice(include_bytes!(
        "../../../../../tests/fixtures/resources/bundle-v1.json"
    ))
    .unwrap();
    let bundle = t7r_battle_resources::match_maps::select(&bundle, map_id).unwrap();
    let resources = Arc::new(BattleResources::prepare(&bundle).unwrap());
    let player = Member {
        camp: 2,
        hero_id: if map_id == 6006 { 10001 } else { 10002 },
        weapon_id: if map_id == 6006 { 1030111 } else { 1040511 },
        equipment: vec![EquipmentItem {
            slot: 1,
            weapon_id: if map_id == 6006 { 1030111 } else { 1040511 },
        }],
        ..base.config.members[0].clone()
    };
    let bot = Member {
        actor_id: 2,
        kind: MemberKind::Bot,
        camp: 1,
        ..player.clone()
    };
    BattleSession::new(
        &bundle,
        resources,
        Config {
            members: vec![player, bot],
            kill_target: 1,
            duration_ms: 100,
            ..base.config
        },
    )
    .unwrap()
}

fn command(session: &mut BattleSession, at: u64, command: training::Command) -> Vec<Output> {
    let mut output = Vec::new();
    session
        .dispatch(
            7,
            at,
            Input::Training {
                source_id: 1,
                actor_id: 1,
                command,
            },
            |events| {
                output = events.to_vec();
                Ok(())
            },
        )
        .unwrap();
    output
}

fn choose(talk_id: i32, option: i32) -> training::Command {
    training::Command::Talk {
        npc_rid: training::INSTRUCTOR_RID,
        talk_id,
        option,
    }
}

#[test]
fn training_round_uses_native_arena_points_and_walkable_navigation() {
    use t7r_battle_core::navigation::{DEFAULT_RADIUS, PathStatus};

    for (map, player, opponent, opponent_heading) in [
        (
            6006,
            [459.267, 492.263, 90.0482],
            [461.477, 494.853, 89.8506],
            107.,
        ),
        (
            6009,
            [460.897, 488.194, 90.1177],
            [458.642, 493.314, 90.2794],
            69.,
        ),
    ] {
        let mut session = fixture(map);
        command(&mut session, 1000, training::Command::Ready);
        if map == 6009 {
            command(&mut session, 1000, choose(2, 0));
        }
        let events = command(
            &mut session,
            1001,
            choose(if map == 6006 { 1 } else { 3 }, 0),
        );
        for (id, position, heading) in [(1, player, -108.), (2, opponent, opponent_heading)] {
            let Life::Spawning { spawn, .. } = session.actor(id).unwrap().life else {
                panic!("training must place both actors on the arena");
            };
            assert_eq!(spawn.position, position);
            assert_eq!(spawn.heading, heading);
            assert!(events.iter().any(|event| matches!(event,
                Output::Life { actor_id, state: Life::Spawning { spawn: sent, .. } }
                    if *actor_id == id && *sent == spawn)));
        }
        let nav = session.resources.navigation();
        assert!(nav.locate(player).is_some());
        assert!(nav.locate(opponent).is_some());
        assert_eq!(
            nav.find_path(player, opponent, DEFAULT_RADIUS)
                .unwrap()
                .status,
            PathStatus::Ready
        );
        assert_eq!(
            nav.find_path(opponent, player, DEFAULT_RADIUS)
                .unwrap()
                .status,
            PathStatus::Ready
        );
        assert_ne!(session.resources.training_entry().unwrap().position, player);
        assert_ne!(
            session.resources.training_return().unwrap().position,
            player
        );
        for camp in session.resources.camps() {
            assert_eq!(camp.respawns(), camp.spawns());
            assert!(
                camp.candidates()
                    .groups()
                    .iter()
                    .all(|group| !group.is_empty())
            );
        }
    }
}

#[test]
fn training_round_deadlines_personalities_and_retry_are_session_owned() {
    for map in [6006, 6009] {
        let mut session = fixture(map);
        let dialog = if map == 6006 { 1 } else { 3 };
        let bot_before = session.actor(2).unwrap();
        command(&mut session, 1000, choose(dialog, 0));
        assert_eq!(session.state.training.as_ref().unwrap().snapshot.state, 0);
        let ready = command(&mut session, 1000, training::Command::Ready);
        assert!(
            ready
                .iter()
                .any(|event| matches!(event, Output::Training(s) if s.state == 0 && !s.show_menu))
        );
        assert!(
            command(&mut session, 1000, training::Command::Ready)
                .iter()
                .all(|event| !matches!(event, Output::Training(_)))
        );
        if map == 6009 {
            command(&mut session, 1000, choose(2, 0));
        }
        assert_eq!(session.next_deadline_ms(None), None);
        command(&mut session, 1001, choose(dialog, 0));
        assert_eq!(
            session.state.ai.state(2).unwrap().config.profile,
            ai::Profile::Balanced
        );
        assert_eq!(session.next_deadline_ms(None), Some(4001));
        command(&mut session, 1002, choose(dialog, 1));
        assert_eq!(
            session.state.ai.state(2).unwrap().config.profile,
            ai::Profile::Balanced
        );
        session
            .dispatch(7, 4000, Input::Tick, |events| {
                assert!(!events.iter().any(|e| matches!(e, Output::Bot(_))));
                Ok(())
            })
            .unwrap();
        session
            .dispatch(7, 4001, Input::Timers, |_| Ok(()))
            .unwrap();
        assert_eq!(session.state.training.as_ref().unwrap().snapshot.state, 3);
        assert_eq!(session.next_deadline_ms(None), Some(64001));
        assert!(session.settlement().is_none());
        session
            .dispatch(7, 64001, Input::Timers, |events| {
                assert!(
                    events
                        .iter()
                        .any(|e| matches!(e, Output::Training(s) if s.state == 4))
                );
                assert!(events.iter().any(
                    |e| matches!(e, Output::Bot(b) if b.movement == ai::MovementIntent::Stop)
                ));
                Ok(())
            })
            .unwrap();
        command(&mut session, 64002, training::Command::Dismiss);
        let Life::Spawning { spawn, .. } = session.actor(1).unwrap().life else {
            panic!("training must return the player to the instructor");
        };
        assert_eq!(spawn, session.resources.training_return().unwrap());
        assert_eq!(spawn.position, [473.845, 439.808, 87.4901]);
        if map == 6009 {
            command(&mut session, 64003, choose(2, 0));
        }
        command(&mut session, 64003, choose(dialog, 1));
        assert_eq!(
            session.state.ai.state(2).unwrap().config.profile,
            ai::Profile::Aggressive
        );
        let bot = session.actor(2).unwrap();
        assert_eq!(
            (bot.hero_id, bot.health, bot.action.weapon_id),
            (
                bot_before.hero_id,
                bot_before.health,
                bot_before.action.weapon_id
            )
        );
        assert_eq!(session.state.training.as_ref().unwrap().snapshot.score, 0);
        session.dispatch(7, 64004, Input::Quit, |_| Ok(())).unwrap();
        assert_eq!(session.settlement().unwrap().reason, EndReason::Quit);
        let fresh = fixture(map);
        assert_eq!(fresh.state.training.as_ref().unwrap().snapshot.state, 0);
        assert_eq!(fresh.state.training.as_ref().unwrap().snapshot.score, 0);
    }
}

#[test]
fn training_rejects_foreign_sources_and_rolls_back_failed_publication() {
    let mut session = fixture(6006);
    assert!(
        session
            .dispatch(
                7,
                1000,
                Input::Training {
                    source_id: 2,
                    actor_id: 1,
                    command: training::Command::Ready
                },
                |_| Ok(())
            )
            .is_err()
    );
    command(&mut session, 1000, training::Command::Ready);
    for wrong in [
        training::Command::Talk {
            npc_rid: 2,
            talk_id: 1,
            option: 0,
        },
        training::Command::Talk {
            npc_rid: 61,
            talk_id: 2,
            option: 0,
        },
        choose(18, 0),
        choose(1, -1),
        choose(1, 2),
        training::Command::Dismiss,
    ] {
        command(&mut session, 1000, wrong);
        assert_eq!(session.state.training.as_ref().unwrap().snapshot.state, 0);
    }
    let before = session.actor(2).unwrap();
    assert!(
        session
            .dispatch(
                7,
                1001,
                Input::Training {
                    source_id: 1,
                    actor_id: 1,
                    command: choose(1, 1)
                },
                |_| Err(io::Error::other("output full"))
            )
            .is_err()
    );
    assert_eq!(session.actor(2).unwrap(), before);
    assert_eq!(session.state.training.as_ref().unwrap().snapshot.state, 0);
    assert_eq!(session.state.now_ms, 1000);
}

#[test]
fn training_kills_do_not_end_the_match_and_ordinary_sessions_keep_their_limit() {
    let mut session = fixture(6006);
    let mut events = Vec::new();
    session
        .state
        .kill(1, 2, &session.config, &mut events)
        .unwrap();
    assert!(session.settlement().is_none());
    assert!(!events.iter().any(|e| matches!(e, Output::Ended(_))));
    session.state.training = None;
    session
        .state
        .kill(2, 1, &session.config, &mut events)
        .unwrap();
    assert!(session.settlement().is_some());
}

#[test]
fn training_defence_uses_native_submenus_before_starting_a_round() {
    for (option, dialog) in [(0, 3), (1, 4), (2, 4)] {
        for difficulty in [0, 1] {
            let mut session = fixture(6009);
            command(&mut session, 1000, training::Command::Ready);
            let player_before = session.actor(1).unwrap();
            for invalid in [
                choose(1, 0),
                choose(18, 0),
                choose(3, 0),
                choose(4, 0),
                choose(2, -1),
                choose(2, 3),
            ] {
                assert!(command(&mut session, 1000, invalid).is_empty());
            }
            let menu = command(&mut session, 1000, choose(2, option));
            assert!(matches!(menu.as_slice(), [Output::Training(s)]
                if s.state == 0 && s.dialog_id == dialog && s.show_menu));
            assert_eq!(session.next_deadline_ms(None), None);
            assert_eq!(session.actor(1).unwrap(), player_before);
            let other_dialog = if dialog == 3 { 4 } else { 3 };
            assert!(command(&mut session, 1000, choose(other_dialog, 0)).is_empty());
            assert!(command(&mut session, 1000, choose(dialog, 2)).is_empty());
            command(&mut session, 1001, choose(dialog, difficulty));
            let snapshot = &session.state.training.as_ref().unwrap().snapshot;
            assert_eq!((snapshot.state, snapshot.show_menu), (2, false));
            assert_eq!(
                session.state.ai.state(2).unwrap().config.profile,
                if difficulty == 0 {
                    ai::Profile::Balanced
                } else {
                    ai::Profile::Aggressive
                }
            );
        }
    }
}

#[test]
fn training_defence_menu_can_be_reopened_after_native_cancel() {
    let mut session = fixture(6009);
    command(&mut session, 1000, training::Command::Ready);
    command(&mut session, 1000, choose(2, 0));
    // Cancel is handled locally; C reopens the instructor's base dialog 2.
    command(&mut session, 1001, choose(2, 2));
    assert!(command(&mut session, 1002, choose(3, 1)).is_empty());
    command(&mut session, 1003, choose(4, 1));
    assert_eq!(session.state.training.as_ref().unwrap().snapshot.state, 2);
}

fn evidence(attacker: u32, target: u32, at: f64) -> Box<contacts::ContactEvent> {
    Box::new(contacts::ContactEvent {
        guard_volume: false,
        actor_id: attacker,
        target_id: target,
        source_entity: u64::from(attacker),
        target_entity: u64::from(target),
        sequence: 1,
        phase_at_ms: 4000,
        target_intent: None,
        target_phase: action::Phase::Idle,
        target_phase_at_ms: 4000.,
        target_observed_at_ms: None,
        source_observed_at_ms: None,
        occurred_at_ms: at,
        along: 0.5,
        defence_weapon_id: 0,
        region: damage::Region::Torso,
        body_index: -1,
        source_position: [0.; 3],
        target_position: [1., 0., 0.],
        source_heading: 0.,
        target_heading: 180.,
        source_velocity: [0.; 3],
        target_velocity: [0.; 3],
        weapon_velocity: [0.; 3],
        velocity_ready: false,
        airborne: false,
        target_airborne: false,
        position: [0.5, 0., 1.],
        normal: [-1., 0., 0.],
        direction: [1., 0., 0.],
        material: 0,
    })
}

fn hit(attacker: u32, target: u32, at: f64, guard_break: bool, hp_damage: u32) -> Output {
    Output::Damage {
        attacker,
        target,
        sequence: 1,
        health: 90,
        previous_health: 100,
        weapon_id: 701,
        evidence: evidence(attacker, target, at),
        guard_break,
        result: damage::DamageResult {
            hp_damage,
            ..Default::default()
        },
    }
}

fn defence(target: u32, at: f64, chamber: bool) -> Output {
    let attacker = if target == 1 { 2 } else { 1 };
    let mut evidence = evidence(attacker, target, at);
    evidence.target_intent = Some(if chamber {
        Intent::Attack(action::Direction::Up)
    } else {
        Intent::Guard(action::Direction::Up)
    });
    Output::Parry {
        outcome: if chamber {
            ParryOutcome::Chambered
        } else {
            ParryOutcome::Blocked
        },
        attacker,
        target,
        weapon_id: 701,
        defence_weapon_id: 701,
        evidence,
    }
}

fn broken_defence(target: u32, at: f64) -> Output {
    let mut event = defence(target, at, false);
    if let Output::Parry { outcome, .. } = &mut event {
        *outcome = ParryOutcome::Broken;
    }
    event
}

#[test]
fn training_scores_only_authoritative_successes_in_the_active_round() {
    for map in [6006, 6009] {
        let mut session = fixture(map);
        let dialog = if map == 6006 { 1 } else { 3 };
        let events = vec![
            hit(1, 2, 4000., false, 10),
            defence(1, 4000., false),
            defence(1, 4000., true),
        ];
        session.state.training_score(&mut events.clone());
        assert_eq!(session.state.training.as_ref().unwrap().snapshot.score, 0);
        command(&mut session, 1000, training::Command::Ready);
        if map == 6009 {
            command(&mut session, 1000, choose(2, 0));
        }
        command(&mut session, 1000, choose(dialog, 0));
        session.state.training_score(&mut events.clone());
        assert_eq!(session.state.training.as_ref().unwrap().snapshot.score, 0);
        session
            .dispatch(7, 4000, Input::Timers, |_| Ok(()))
            .unwrap();
        let mut scored = events.clone();
        session.state.training_score(&mut scored);
        let snapshot = &session.state.training.as_ref().unwrap().snapshot;
        let expected = if map == 6006 {
            (1, 0, 0, 10)
        } else {
            (0, 1, 1, 20)
        };
        assert_eq!(
            (
                snapshot.hits,
                snapshot.blocks,
                snapshot.parries,
                snapshot.score
            ),
            expected
        );
        assert!(matches!(scored.last(), Some(Output::Training(_))));
        // Wrong actor, shield-only hit, broken guard and contacts outside the round do not score.
        for mut ignored in [
            vec![hit(2, 1, 5000., false, 10), defence(2, 5000., false)],
            vec![hit(1, 2, 5000., false, 0)],
            vec![hit(2, 1, 5000., true, 10), broken_defence(1, 5000.)],
            vec![broken_defence(1, 5001.)],
            vec![hit(1, 2, 3999., false, 10), defence(1, 3999., false)],
            vec![hit(1, 2, 64000., false, 10), defence(1, 64000., false)],
        ] {
            session.state.training_score(&mut ignored);
            assert!(
                !ignored
                    .iter()
                    .any(|event| matches!(event, Output::Training(_)))
            );
            assert_eq!(
                session.state.training.as_ref().unwrap().snapshot.score,
                expected.3
            );
        }
        session
            .dispatch(7, 64000, Input::Timers, |_| Ok(()))
            .unwrap();
        session.state.training_score(&mut events.clone());
        assert_eq!(
            session.state.training.as_ref().unwrap().snapshot.score,
            expected.3
        );
        command(&mut session, 64001, training::Command::Dismiss);
        if map == 6009 {
            command(&mut session, 64002, choose(2, 0));
        }
        command(&mut session, 64002, choose(dialog, 1));
        let snapshot = &session.state.training.as_ref().unwrap().snapshot;
        assert_eq!(
            (
                snapshot.hits,
                snapshot.blocks,
                snapshot.parries,
                snapshot.score
            ),
            (0, 0, 0, 0)
        );
    }
}
