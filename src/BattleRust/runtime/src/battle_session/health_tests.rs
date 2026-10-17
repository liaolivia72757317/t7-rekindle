use super::*;

fn fixture() -> (BattleSession, PreparedHero) {
    let (base, selected) = selection::tests::fixture();
    let mut data: serde_json::Value = serde_json::from_slice(include_bytes!(
        "../../../../../tests/fixtures/resources/bundle-v1.json"
    ))
    .unwrap();
    data["damageProfiles"]["1101"]["maxHealth"] = 150.into();
    data["damageProfiles"]["2102"]["maxHealth"] = 240.into();
    let bundle = ResourceBundle::from_slice(&serde_json::to_vec(&data).unwrap()).unwrap();
    let selected = PreparedHero::new(&bundle, selected.member).unwrap();
    let bot = Member {
        actor_id: 2,
        kind: MemberKind::Bot,
        camp: 2,
        ..selected.member.clone()
    };
    let session = BattleSession::new(
        &bundle,
        base.resources,
        Config {
            members: vec![base.config.members[0].clone(), bot],
            ..base.config
        },
    )
    .unwrap();
    (session, selected)
}

#[test]
fn initial_selection_uses_selected_health_and_preserves_identity_until_game() {
    let (mut session, selected) = fixture();
    let before = session.actor(1).unwrap();
    assert!(session.prepare_hero(6, &selected).is_err());
    assert_eq!(session.actor(1).unwrap(), before);
    for foreign in [
        Member {
            sample_source_id: 2,
            ..selected.member.clone()
        },
        Member {
            camp: 2,
            ..selected.member.clone()
        },
        Member {
            kind: MemberKind::Bot,
            ..selected.member.clone()
        },
    ] {
        let mut candidate = selected.clone();
        candidate.member = foreign;
        assert!(session.prepare_hero(7, &candidate).is_err());
        assert_eq!(session.actor(1).unwrap(), before);
    }
    assert!(session.prepare_hero(7, &selected).unwrap());
    assert!(session.prepare_hero(7, &selected).unwrap());
    let after = session.actor(1).unwrap();
    assert_eq!(
        (after.hero_id, after.action.weapon_id, after.health),
        (2102, 702, 240)
    );
    assert_eq!(
        (after.life, after.score, after.action.sequence),
        (before.life, before.score, before.action.sequence)
    );
    assert_eq!(
        session.state.actors[&1].member.equipment,
        selected.member.equipment
    );
    assert_eq!(session.state.state_revision, 0);
    session.dispatch(7, 1000, Input::Tick, |_| Ok(())).unwrap();
    assert!(!session.prepare_hero(7, &selected).unwrap());
    assert!(
        !session
            .select_hero(7, 1000, &selected, |_, _| panic!("unexpected selection"))
            .unwrap()
    );
}

#[test]
fn bot_respawn_and_player_reselection_restore_the_selected_maximum() {
    let (mut session, selected) = fixture();
    let spawn = session.resources.camps()[1].spawns()[0];
    let mut events = Vec::new();
    let original = session.state.actors[&2].member.clone();
    session
        .state
        .kill(1, 2, &session.config, &mut events)
        .unwrap();
    assert_eq!(session.actor(2).unwrap().health, 0);
    session
        .state
        .spawn_actor(2, spawn, &session.resources, &mut events)
        .unwrap();
    assert_eq!(session.actor(2).unwrap().health, 240);
    assert_eq!(session.actor(2).unwrap().hero_id, original.hero_id);
    assert_eq!(session.actor(2).unwrap().action.weapon_id, original.weapon_id);
    assert_eq!(session.state.actors[&2].member.equipment, original.equipment);

    let actor = session.state.actor_mut(1).unwrap();
    actor.life = Life::Selecting { died_at_ms: 1000 };
    actor.health = 0;
    assert!(
        session
            .select_hero(7, 9000, &selected, |_, _| Err(io::Error::other(
                "sink failed"
            )))
            .is_err()
    );
    assert_eq!(session.state.actors[&1].resources.damage.max_health, 150);
    assert!(
        session
            .select_hero(7, 9000, &selected, |_, _| Ok(()))
            .unwrap()
    );
    assert_eq!(session.actor(1).unwrap().health, 0);
    let spawn = session.resources.camps()[0].spawns()[0];
    session
        .state
        .spawn_actor(1, spawn, &session.resources, &mut events)
        .unwrap();
    assert_eq!(session.actor(1).unwrap().health, 240);
    assert_eq!(session.actor(1).unwrap().hero_id, 2102);
}

#[test]
fn ai_health_thresholds_remain_percentages_without_early_rounding() {
    let (mut session, _) = fixture();
    session.state.world = Some(Arc::new(World {
        epoch: 7,
        sampled_at_ms: 1000,
        actors: BTreeMap::from([(
            1,
            Pose {
                entity: 101,
                position: [0.; 3],
                heading: 0.,
                sequence: 1,
                contact: ai::ContactObservation::unavailable(),
            },
        )]),
    }));
    for (health, percent) in [
        (0, 0),
        (1, 1),
        (30, 20),
        (31, 21),
        (60, 40),
        (61, 41),
        (150, 100),
    ] {
        session.state.actor_mut(1).unwrap().health = health;
        assert_eq!(session.state.ai_world(false).actors[0].health, percent);
    }
}
