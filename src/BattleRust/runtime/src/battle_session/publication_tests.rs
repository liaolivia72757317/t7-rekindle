use super::*;

fn fixture() -> BattleSession {
    let bundle = ResourceBundle::from_slice(include_bytes!(
        "../../../../../tests/fixtures/resources/bundle-v1.json"
    ))
    .unwrap();
    BattleSession::new(
        &bundle,
        Arc::new(BattleResources::prepare(&bundle).unwrap()),
        Config {
            epoch: 7,
            started_at_ms: 0,
            duration_ms: 10000,
            kill_target: 10,
            seed: 42,
            members: (1..=20)
                .map(|actor_id| Member {
                    actor_id,
                    kind: if actor_id == 1 {
                        MemberKind::Human
                    } else {
                        MemberKind::Bot
                    },
                    sample_source_id: 9,
                    camp: if actor_id <= 10 { 1 } else { 2 },
                    hero_id: 1101,
                    weapon_id: 701,
                    equipment: vec![EquipmentItem {
                        slot: 1,
                        weapon_id: 701,
                    }],
                })
                .collect(),
        },
    )
    .unwrap()
}

fn empty_contacts(batch_id: u64, at_ms: u64) -> Input {
    Input::SourceContacts {
        source_id: 9,
        batch: contacts::ContactBatch {
            epoch: 7,
            batch_id,
            through_at_ms: at_ms as f64,
            dropped: 0,
            events: Vec::new(),
        },
    }
}

#[test]
fn empty_contact_watermarks_do_not_copy_actors_or_publish_deltas() {
    let mut session = fixture();
    session.dispatch(7, 0, Input::Timers, |_| Ok(())).unwrap();
    let before = session.state.actors.clone();
    let revision = session.state.state_revision;
    let mut copies = 0;
    let mut published_actors = 0;
    let started = std::time::Instant::now();
    for batch in 1..=100 {
        let previous = session.state.actors.clone();
        session
            .dispatch(7, batch, empty_contacts(batch, batch), |outputs| {
                published_actors += outputs
                    .iter()
                    .map(|output| match output {
                        Output::State(state) => state.actors.len(),
                        _ => 0,
                    })
                    .sum::<usize>();
                Ok(())
            })
            .unwrap();
        copies += session
            .state
            .actors
            .iter()
            .filter(|(id, actor)| !Arc::ptr_eq(actor, &previous[id]))
            .count();
    }
    println!(
        "20 actors / 100 empty batches: copies={copies}, published_actors={published_actors}, elapsed={:?}",
        started.elapsed()
    );
    assert_eq!(copies, 0);
    assert_eq!(published_actors, 0);
    assert_eq!(session.state.state_revision, revision);
    assert_eq!(session.state.contact_through, 100.);
    for (id, actor) in before {
        assert!(Arc::ptr_eq(&actor, &session.state.actors[&id]));
    }
    session.dispatch(7, 1000, empty_contacts(101, 1000), |outputs| {
        assert!(matches!(outputs, [Output::State(state)] if state.mode == 2 && state.revision == revision && state.actors.len() == 20));
        Ok(())
    }).unwrap();
}

#[test]
fn guard_recovery_is_visible_and_failed_batch_rolls_back_all_cursors() {
    let mut session = fixture();
    let rules = session.resources.guard_rules();
    let meter = session.state.guard_meters[&1]
        .consume(20., 0., rules)
        .unwrap()
        .meter;
    session.state.guard_meters.insert(1, meter);
    session.dispatch(7, 0, Input::Timers, |_| Ok(())).unwrap();
    let revision = session.state.state_revision;
    let at_ms = u64::from(session.resources.guard_rules().recover_delay_ms) + 100;
    let before = session.state.actors.clone();
    let check = |outputs: &[Output]| {
        let state = outputs
            .iter()
            .find_map(|output| match output {
                Output::State(state) => Some(state),
                _ => None,
            })
            .expect("guard recovery must be published");
        assert_eq!(state.revision, revision + 1);
        assert!(
            state
                .actors
                .iter()
                .find(|actor| actor.actor_id == 1)
                .unwrap()
                .guard
                > meter.value()
        );
    };
    session
        .dispatch(7, at_ms, empty_contacts(1, at_ms), |outputs| {
            check(outputs);
            Err(io::Error::new(io::ErrorKind::WouldBlock, "output full"))
        })
        .unwrap_err();
    assert_eq!(session.state.guard_meters[&1], meter);
    assert_eq!(session.state.state_revision, revision);
    assert_eq!(session.state.contact_through, 0.);
    assert!(session.state.contact_cursors.is_empty());
    assert!(session.state.source_contact_through.is_empty());
    session
        .dispatch(7, at_ms, empty_contacts(1, at_ms), |outputs| {
            check(outputs);
            Ok(())
        })
        .unwrap();
    for (id, actor) in before {
        assert!(Arc::ptr_eq(&actor, &session.state.actors[&id]));
    }
}

#[test]
fn history_cleanup_copies_only_changed_actor_without_public_delta() {
    let mut session = fixture();
    session.state.actor_mut(1).unwrap().swings.push(Swing {
        sequence: 1,
        intent: Intent::Attack(Direction::Up),
        weapon_id: 701,
        began_at_ms: 0,
        held_ms: 0,
        strike_duration_ms: 600,
        pending_breaks: BTreeMap::new(),
        start_ms: 0,
        end_ms: 10,
        closed_at_ms: Some(10),
        cutoff_ms: None,
        hits: Vec::new(),
        allies: 0,
        enemies: 0,
        stopped: false,
        ground_played: false,
        buffs: BTreeMap::new(),
    });
    session.dispatch(7, 0, Input::Timers, |_| Ok(())).unwrap();
    let before = session.state.actors.clone();
    let revision = session.state.state_revision;
    assert!(
        session
            .dispatch(7, 10, empty_contacts(1, 10), |_| {
                Err(io::Error::new(io::ErrorKind::WouldBlock, "output full"))
            })
            .is_err()
    );
    assert_eq!(session.state.actors[&1].swings.len(), 1);
    session
        .dispatch(7, 10, empty_contacts(1, 10), |outputs| {
            assert!(outputs.is_empty());
            Ok(())
        })
        .unwrap();
    assert!(session.state.actors[&1].swings.is_empty());
    assert_eq!(session.state.state_revision, revision);
    for (id, actor) in before {
        assert_eq!(Arc::ptr_eq(&actor, &session.state.actors[&id]), id != 1);
    }
}

#[test]
fn player_input_before_current_life_is_rejected_without_consuming_batch() {
    let mut session = fixture();
    session.state.actor_mut(1).unwrap().contact_life_at_ms = 100;
    let input = |at_ms| Input::PlayerAction {
        stamp: t7r_battle_core::battle_semantics::SourceStamp {
            epoch: 7,
            source_id: 9,
            batch_id: 1,
            at_ms,
        },
        actor_id: 1,
        command: action::Command::ReleaseAttack,
    };
    assert!(
        session
            .dispatch(7, 100, input(99), |_| panic!(
                "previous life input was published"
            ))
            .is_err()
    );
    session.dispatch(7, 100, input(100), |_| Ok(())).unwrap();
}
