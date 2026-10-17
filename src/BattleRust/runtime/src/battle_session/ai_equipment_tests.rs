use super::*;

fn fixture(restricted: bool) -> BattleSession {
    equipped_fixture(restricted, false)
}

fn equipped_fixture(restricted: bool, shield: bool) -> BattleSession {
    let (base, _) = selection::tests::fixture();
    let mut data: serde_json::Value = serde_json::from_slice(include_bytes!(
        "../../../../../tests/fixtures/resources/bundle-v1.json"
    ))
    .unwrap();
    for (weapon, fastest) in [("701", "300570"), ("100000", "300560")] {
        for (intent, attack) in data["combatProfiles"]["1101"][weapon]["attacks"]
            .as_object_mut()
            .unwrap()
        {
            let fastest = if restricted { "300560" } else { fastest };
            attack["phases"][0] = if intent == fastest { 1 } else { 900 }.into();
            attack["enabled"] = (!restricted || weapon == "100000" || intent == "300570").into();
        }
    }
    let bundle = ResourceBundle::from_slice(&serde_json::to_vec(&data).unwrap()).unwrap();
    let mut bot = Member {
        actor_id: 2,
        kind: MemberKind::Bot,
        camp: 2,
        ..base.config.members[0].clone()
    };
    if shield {
        bot.equipment.extend([
            EquipmentItem {
                slot: 2,
                weapon_id: 702,
            },
            EquipmentItem {
                slot: 4,
                weapon_id: 703,
            },
        ]);
    }
    let mut session = BattleSession::new(
        &bundle,
        base.resources,
        Config {
            members: vec![base.config.members[0].clone(), bot],
            ..base.config
        },
    )
    .unwrap();
    let mut bot_config =
        ai::BotConfig::from_spawns(2, 0, 2, 7, &[[10., 0., 0.]], &[[-10., 0., 0.]]).unwrap();
    bot_config.profile = ai::Profile::Berserker;
    session.state.ai = ai::Controller::new(1000, vec![bot_config]).unwrap();
    session.state.world = Some(Arc::new(World {
        epoch: 7,
        sampled_at_ms: 1000,
        actors: [(1, [0.; 3], 0.), (2, [1.9, 0., 0.], 180.)]
            .into_iter()
            .map(|(id, position, heading)| {
                (
                    id,
                    Pose {
                        entity: 100 + u64::from(id),
                        position,
                        heading,
                        sequence: 1,
                        contact: ai::ContactObservation {
                            target_id: Some(3 - id),
                            state: ai::ContactState::Clear,
                        },
                    },
                )
            })
            .collect(),
    }));
    session.state.actor_sampled_at_ms = BTreeMap::from([(1, 1000), (2, 1000)]);
    session
}

#[test]
fn ai_reads_spacing_and_direction_timings_from_current_weapon_not_spawn_weapon() {
    for (weapon, preferred, direction) in
        [(701, 1.1, Direction::Down), (100000, 0.9, Direction::Up)]
    {
        let mut session = fixture(false);
        let actor = session.state.actor_mut(2).unwrap();
        actor.action = action::ActionState::new(2, &actor.resources.loadout, weapon, 1).unwrap();
        assert_eq!(actor.member.weapon_id, 701);
        let world = session.state.ai_world(true);
        assert_eq!(
            world
                .actors
                .iter()
                .find(|actor| actor.id == 2)
                .unwrap()
                .preferred_distance,
            preferred
        );
        let mut events = Vec::new();
        session
            .state
            .decide(&session.resources, &mut events)
            .unwrap();
        let intent = events.iter().find_map(|event| match event {
            Output::Bot(bot) if bot.actor_id == 2 => bot.action.map(|action| action.intent),
            _ => None,
        });
        assert_eq!(intent, Some(Intent::Attack(direction)));
        assert_eq!(session.actor(2).unwrap().action.phase_due_ms, Some(1001));
    }
}

#[test]
fn ai_filters_disabled_attacks_and_refreshes_capabilities_after_weapon_change() {
    for fists in [false, true] {
        let mut session = fixture(true);
        if fists {
            session
                .state
                .action(2, Command::ChangeSlot(13), None, &mut Vec::new())
                .unwrap();
            session.state.now_ms = session.actor(2).unwrap().action.phase_due_ms.unwrap();
            session.state.timers(&mut Vec::new()).unwrap();
            let now = session.state.now_ms;
            let mut world = (**session.state.world.as_ref().unwrap()).clone();
            world.sampled_at_ms = now;
            session.state.world = Some(Arc::new(world));
            session.state.actor_sampled_at_ms = BTreeMap::from([(1, now), (2, now)]);
        }
        assert_eq!(session.state.actors[&2].member.weapon_id, 701);
        let mut events = Vec::new();
        session
            .state
            .decide(&session.resources, &mut events)
            .unwrap();
        let intent = events.iter().find_map(|event| match event {
            Output::Bot(bot) if bot.actor_id == 2 => bot.action.map(|action| action.intent),
            _ => None,
        });
        let direction = if fists {
            Direction::Up
        } else {
            Direction::Down
        };
        assert_eq!(intent, Some(Intent::Attack(direction)));
        assert_eq!(session.actor(2).unwrap().action.intent, intent);
        assert_eq!(
            session.actor(2).unwrap().action.phase_due_ms,
            Some(session.state.now_ms + if fists { 1 } else { 900 })
        );
    }
}

fn advance(session: &mut BattleSession, now: u64) {
    session.state.now_ms = now;
    let mut world = (**session.state.world.as_ref().unwrap()).clone();
    world.sampled_at_ms = now;
    session.state.world = Some(Arc::new(world));
    session.state.actor_sampled_at_ms = BTreeMap::from([(1, now), (2, now)]);
    session.state.timers(&mut Vec::new()).unwrap();
}

fn defend(session: &mut BattleSession) -> Vec<Output> {
    let now = session.state.now_ms;
    let mut config =
        ai::BotConfig::from_spawns(2, 0, 2, 7, &[[10., 0., 0.]], &[[-10., 0., 0.]]).unwrap();
    config.profile = ai::Profile::Defensive;
    session.state.ai = ai::Controller::new(now, vec![config]).unwrap();
    session
        .state
        .action(
            1,
            Command::Start {
                intent: Intent::Attack(Direction::Up),
                released: false,
            },
            None,
            &mut Vec::new(),
        )
        .unwrap();
    session
        .state
        .decide(&session.resources, &mut Vec::new())
        .unwrap();
    assert_eq!(session.actor(2).unwrap().action.phase, Phase::Idle);
    advance(session, now + 200);
    let mut events = Vec::new();
    session
        .state
        .decide(&session.resources, &mut events)
        .unwrap();
    events
}

#[test]
fn bot_uses_equipped_shield_and_releases_it_on_the_existing_guard_timer() {
    let mut session = equipped_fixture(false, true);
    assert_eq!(session.actor(2).unwrap().action.offhand_id, 703);
    let events = defend(&mut session);
    assert_eq!(
        session.actor(2).unwrap().action.intent,
        Some(Intent::Shield)
    );
    assert!(events.iter().any(|event| matches!(event, Output::Bot(bot)
        if bot.actor_id == 2 && bot.action.is_some_and(|a| a.intent == Intent::Shield && !a.released))));
    assert!(events.iter().any(|event| matches!(
        event,
        Output::Action(ActionEvent::Animation(action::AnimationEvent {
            actor_id: 2,
            animation: action::Animation::Action {
                intent: Intent::Shield,
                phase: Phase::Prepare
            },
            ..
        }))
    )));
    let release_at = session.state.actors[&2].release_at_ms.unwrap();
    let prepared_at = session.actor(2).unwrap().action.phase_due_ms.unwrap();
    assert!(release_at > prepared_at);
    advance(&mut session, prepared_at);
    assert_eq!(session.actor(2).unwrap().action.phase, Phase::Hold);
    advance(&mut session, release_at);
    assert_eq!(session.actor(2).unwrap().action.phase, Phase::Idle);
    assert_eq!(session.state.actors[&2].release_at_ms, None);
}

#[test]
fn bot_falls_back_to_weapon_guard_when_shield_is_broken_or_not_equipped() {
    for mode in ["broken", "two-handed", "fists", "re-equipped"] {
        let mut session = equipped_fixture(false, true);
        if mode == "broken" {
            let actor = session.state.actor_mut(2).unwrap();
            actor.shields.insert(703, 0);
            actor.action = actor
                .action
                .break_shield(703, &actor.resources.loadout)
                .unwrap();
        } else {
            let slot = if mode == "two-handed" { 2 } else { 13 };
            session
                .state
                .action(2, Command::ChangeSlot(slot), None, &mut Vec::new())
                .unwrap();
            let due = session.actor(2).unwrap().action.phase_due_ms.unwrap();
            advance(&mut session, due);
            assert_eq!(session.actor(2).unwrap().action.offhand_id, 0);
            if mode == "re-equipped" {
                session
                    .state
                    .action(2, Command::ChangeSlot(13), None, &mut Vec::new())
                    .unwrap();
                let due = session.actor(2).unwrap().action.phase_due_ms.unwrap();
                advance(&mut session, due);
            }
        }
        let expected = if mode == "re-equipped" {
            Intent::Shield
        } else {
            Intent::Guard(Direction::Up)
        };
        defend(&mut session);
        assert_eq!(
            session.actor(2).unwrap().action.intent,
            Some(expected),
            "{mode}"
        );
    }
}
