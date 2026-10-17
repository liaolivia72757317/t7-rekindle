use super::*;

fn health_session() -> BattleSession {
    let mut data = bundle().data().clone();
    data["damageProfiles"]["1101"]["maxHealth"] = 150.into();
    data["damageProfiles"]["2102"]["maxHealth"] = 240.into();
    let bundle = ResourceBundle::from_slice(&serde_json::to_vec(&data).unwrap()).unwrap();
    BattleSession::new(
        &bundle,
        Arc::new(BattleResources::prepare(&bundle).unwrap()),
        Config {
            epoch: 7,
            started_at_ms: 0,
            duration_ms: 1200000,
            kill_target: 10,
            seed: 42,
            members: vec![
                member(1),
                Member {
                    hero_id: 2102,
                    weapon_id: 702,
                    equipment: vec![t7r_battle_core::action::EquipmentItem {
                        slot: 3,
                        weapon_id: 702,
                    }],
                    ..member(2)
                },
            ],
        },
    )
    .unwrap()
}

#[test]
fn initial_health_follows_each_selected_hero() {
    let session = health_session();
    assert_eq!(session.actor(1).unwrap().health, 150);
    assert_eq!(session.actor(2).unwrap().health, 240);
}

#[test]
fn damage_above_one_hundred_publishes_absolute_health() {
    let mut session = health_session();
    session
        .dispatch(7, 0, Input::World(world(0, 1)), publish)
        .unwrap();
    session
        .dispatch(
            7,
            0,
            Input::Action {
                actor_id: 1,
                command: Command::Start {
                    intent: Intent::Attack(Direction::Up),
                    released: true,
                },
            },
            publish,
        )
        .unwrap();
    let due = session.actor(1).unwrap().action.phase_due_ms.unwrap();
    session.dispatch(7, due, Input::Timers, publish).unwrap();
    let sequence = session.actor(1).unwrap().action.sequence;
    session
        .dispatch(
            7,
            due + 20,
            Input::World(world(due + 20, sequence)),
            publish,
        )
        .unwrap();
    let mut outputs = Vec::new();
    session
        .dispatch(
            7,
            due + 300,
            Input::Contacts(ContactBatch {
                epoch: 7,
                batch_id: 1,
                through_at_ms: (due + 300) as f64,
                dropped: 0,
                events: vec![contact(sequence, due, (due + 300) as f64)],
            }),
            |events| {
                outputs.extend_from_slice(events);
                Ok(())
            },
        )
        .unwrap();
    assert_eq!(session.actor(2).unwrap().health, 220);
    assert!(outputs.iter().any(|e| matches!(
        e,
        Output::Damage {
            previous_health: 240,
            health: 220,
            ..
        }
    )));
    let state = outputs
        .iter()
        .find_map(|e| match e {
            Output::State(s) => Some(s),
            _ => None,
        })
        .unwrap();
    let bytes = state.encode().unwrap();
    let decoded = t7r_battle_core::battle_semantics::state::MatchState::decode(&bytes).unwrap();
    assert_eq!(
        decoded
            .actors
            .iter()
            .find(|a| a.actor_id == 2)
            .unwrap()
            .health,
        220
    );
}
