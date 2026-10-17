use super::*;
use serde_json::{Value, json};

fn fixture(traits: Value) -> BattleSession {
    let mut data: Value = serde_json::from_slice(include_bytes!(
        "../../../../../tests/fixtures/resources/bundle-v1.json"
    ))
    .unwrap();
    data["damageProfiles"]["1101"]["traits"] = traits;
    data["rules"]["damageBuffs"] = json!({
        "riposte": [1.5, 1234], "combo": [1.75, 2345], "rage": [2.0, 3456]
    });
    let bundle = ResourceBundle::from_slice(&serde_json::to_vec(&data).unwrap()).unwrap();
    BattleSession::new(
        &bundle,
        Arc::new(BattleResources::prepare(&bundle).unwrap()),
        Config {
            epoch: 7,
            started_at_ms: 0,
            duration_ms: 10000,
            kill_target: 10,
            seed: 42,
            members: vec![Member {
                actor_id: 1,
                kind: MemberKind::Human,
                sample_source_id: 9,
                camp: 1,
                hero_id: 1101,
                weapon_id: 701,
                equipment: vec![EquipmentItem {
                    slot: 1,
                    weapon_id: 701,
                }],
            }],
        },
    )
    .unwrap()
}

#[test]
fn grants_use_resource_multipliers_and_millisecond_durations() {
    let mut session = fixture(json!(["riposte", "combo", "rage"]));
    for (name, multiplier, duration_ms) in [
        ("riposte", 1.5, 1234.),
        ("combo", 1.75, 2345.),
        ("rage", 2., 3456.),
    ] {
        session
            .state
            .grant_buff(1, name, 250.5, &session.resources)
            .unwrap();
        assert_eq!(
            session.state.actors[&1].buffs[name],
            Buff {
                since_ms: 250.5,
                until_ms: 250.5 + duration_ms,
                multiplier,
            }
        );
    }
}

#[test]
fn resource_rules_do_not_enable_missing_hero_traits() {
    let mut session = fixture(json!([]));
    for name in ["riposte", "combo", "rage"] {
        session
            .state
            .grant_buff(1, name, 250.5, &session.resources)
            .unwrap();
    }
    assert!(session.state.actors[&1].buffs.is_empty());
}

#[test]
fn desperate_trait_uses_the_attacking_hero_maximum_health() {
    let mut session = fixture(json!(["desperate"]));
    let actor = session.state.actor_mut(1).unwrap();
    Arc::make_mut(&mut actor.resources).damage.max_health = 150;
    let mut target = actor.clone();
    target.member.actor_id = 2;
    target.member.camp = 2;
    session.state.actors.insert(2, Arc::new(target));
    let event = ContactEvent {
        actor_id: 1,
        target_id: 2,
        source_entity: 101,
        target_entity: 102,
        sequence: 1,
        phase_at_ms: 0,
        target_intent: None,
        target_phase: Phase::Idle,
        target_phase_at_ms: 0.,
        target_observed_at_ms: None,
        source_observed_at_ms: None,
        occurred_at_ms: 300.,
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
    };
    let swing = Swing {
        sequence: 1,
        intent: Intent::Attack(Direction::Up),
        weapon_id: 701,
        began_at_ms: 0,
        held_ms: 0,
        start_ms: 0,
        end_ms: 600,
        closed_at_ms: None,
        cutoff_ms: None,
        hits: vec![],
        allies: 0,
        enemies: 0,
        stopped: false,
        ground_played: false,
        buffs: BTreeMap::new(),
    };
    for (health, expected) in [(150, 20), (46, 20), (45, 24), (1, 24)] {
        session.state.actor_mut(1).unwrap().health = health;
        let result = session
            .state
            .calculate_damage(&event, &swing, &session.resources, 1., None)
            .unwrap();
        assert_eq!(result.hp_damage, expected, "health={health}");
    }
}
