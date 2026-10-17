use super::*;

fn striking(attack: Intent) -> (BattleSession, u64, u16) {
    let mut battle = session();
    battle
        .dispatch(7, 0, Input::World(world(0, 1)), publish)
        .unwrap();
    battle
        .dispatch(
            7,
            0,
            Input::Action {
                actor_id: 1,
                command: Command::Start {
                    intent: attack,
                    released: true,
                },
            },
            publish,
        )
        .unwrap();
    let due = battle.actor(1).unwrap().action.phase_due_ms.unwrap();
    battle.dispatch(7, due, Input::Timers, publish).unwrap();
    let sequence = battle.actor(1).unwrap().action.sequence;
    battle
        .dispatch(
            7,
            due + 250,
            Input::World(world(due + 250, sequence)),
            publish,
        )
        .unwrap();
    (battle, due, sequence)
}

fn settle(battle: &mut BattleSession, at: u64, events: Vec<ContactEvent>) -> Vec<Output> {
    let mut output = Vec::new();
    battle
        .dispatch(
            7,
            at,
            Input::Contacts(ContactBatch {
                epoch: 7,
                batch_id: 1,
                through_at_ms: at as f64,
                dropped: 0,
                events,
            }),
            |events| {
                output.extend_from_slice(events);
                Ok(())
            },
        )
        .unwrap();
    output
}

#[test]
fn ordinary_guard_requires_contact_and_all_six_direction_pairs() {
    use Direction::*;
    for phase in [Phase::Hold, Phase::ParrySuccess] {
        for attack in [Up, Right, Down, Left] {
            for defence in [Up, Right, Down, Left] {
                let (mut battle, due, sequence) = striking(Intent::Attack(attack));
                let at = due + 250;
                let mut weapon = contact(sequence, due, at as f64);
                weapon.defence_weapon_id = 701;
                weapon.target_intent = Some(Intent::Guard(defence));
                weapon.target_phase = phase;
                weapon.along = 0.25;
                let mut body = weapon.clone();
                body.defence_weapon_id = 0;
                body.along = 0.5;
                let before = battle.actor(2).unwrap();
                // Deliberately submit the body first: settlement must sort actual contacts.
                let output = settle(&mut battle, at, vec![body, weapon]);
                let allowed = matches!(
                    (attack, defence),
                    (Up, Up) | (Down, Down) | (Left | Right, Left | Right)
                );
                assert_eq!(
                    output.iter().any(|e| matches!(e, Output::Parry { .. })),
                    allowed,
                    "{attack:?} against {defence:?} in {phase:?}"
                );
                let after = battle.actor(2).unwrap();
                assert_eq!(after.health == before.health, allowed);
                assert_eq!(after.guard < before.guard, allowed);
                assert_eq!(
                    battle.actor(1).unwrap().action.phase == Phase::Parried,
                    allowed
                );
            }
        }
    }
}

#[test]
fn guard_pose_alone_and_body_before_weapon_do_not_block() {
    for (has_weapon, earlier_body) in [(false, false), (true, true)] {
        let (mut battle, due, sequence) = striking(Intent::Attack(Direction::Up));
        let at = due + 250;
        let mut body = contact(sequence, due, at as f64);
        body.target_intent = Some(Intent::Guard(Direction::Up));
        body.target_phase = Phase::Hold;
        body.along = if earlier_body { 0.1 } else { 0.5 };
        let mut weapon = body.clone();
        weapon.defence_weapon_id = 701;
        weapon.along = 0.25;
        let before = battle.actor(2).unwrap();
        let output = settle(
            &mut battle,
            at,
            if has_weapon {
                vec![weapon, body]
            } else {
                vec![body]
            },
        );
        assert!(output.iter().all(|e| !matches!(e, Output::Parry { .. })));
        assert!(battle.actor(2).unwrap().health < before.health);
        assert_eq!(battle.actor(2).unwrap().guard, before.guard);
    }
}

#[test]
fn equal_contacts_prefer_weapon_and_invalid_weapon_keeps_body_candidate() {
    for weapon_id in [701, 20002, 20006] {
        let (mut battle, due, sequence) = striking(Intent::Attack(Direction::Up));
        let at = due + 250;
        let mut body = contact(sequence, due, at as f64);
        body.target_intent = Some(Intent::Guard(Direction::Up));
        body.target_phase = Phase::Hold;
        let mut weapon = body.clone();
        weapon.defence_weapon_id = weapon_id;
        let before = battle.actor(2).unwrap().health;
        let output = settle(&mut battle, at, vec![body, weapon]);
        assert_eq!(
            output.iter().any(|e| matches!(e, Output::Parry { .. })),
            weapon_id == 701
        );
        assert_eq!(battle.actor(2).unwrap().health == before, weapon_id == 701);
    }
}

#[test]
fn chamber_retains_its_own_direction_and_prepare_window() {
    // Up chamber catches a downward thrust even though ordinary Up guard does not.
    for elapsed in [49, 50, 200, 201] {
        let (mut battle, due, sequence) = striking(Intent::Attack(Direction::Down));
        let at = due + 250;
        let mut weapon = contact(sequence, due, at as f64);
        weapon.defence_weapon_id = 701;
        weapon.target_intent = Some(Intent::Attack(Direction::Up));
        weapon.target_phase = Phase::Prepare;
        weapon.target_phase_at_ms = (at - elapsed) as f64;
        weapon.along = 0.25;
        let mut body = weapon.clone();
        body.defence_weapon_id = 0;
        body.along = 0.5;
        let before = battle.actor(2).unwrap().health;
        let output = settle(&mut battle, at, vec![body, weapon]);
        let allowed = (50..=200).contains(&elapsed);
        assert_eq!(
            output.iter().any(|e| matches!(e, Output::Parry { .. })),
            allowed
        );
        assert_eq!(battle.actor(2).unwrap().health == before, allowed);
    }
}

#[test]
fn prestart_target_phase_preserves_idle_guard_and_chamber_outcomes() {
    for (intent, phase, parried) in [
        (None, Phase::Idle, false),
        (Some(Intent::Guard(Direction::Down)), Phase::Hold, true),
        (Some(Intent::Attack(Direction::Up)), Phase::Prepare, false),
    ] {
        let (mut battle, due, sequence) = striking(Intent::Attack(Direction::Down));
        let at = due + 250;
        let mut body = contact(sequence, due, at as f64);
        body.target_intent = intent;
        body.target_phase = phase;
        body.target_phase_at_ms = -4999.;
        body.target_observed_at_ms = Some(at);
        body.source_observed_at_ms = Some(at);
        body.along = 0.5;
        let mut weapon = body.clone();
        weapon.defence_weapon_id = 701;
        weapon.along = 0.25;
        for (source, target) in [(None, None), (Some(at), None), (None, Some(at))] {
            let mut legacy = body.clone();
            legacy.source_observed_at_ms = source;
            legacy.target_observed_at_ms = target;
            assert!(
                battle
                    .dispatch(
                        7,
                        at,
                        Input::Contacts(ContactBatch {
                            epoch: 7,
                            batch_id: 1,
                            through_at_ms: at as f64,
                            dropped: 0,
                            events: vec![legacy],
                        }),
                        publish
                    )
                    .is_err()
            );
        }
        let before = battle.actor(2).unwrap().health;
        let output = settle(&mut battle, at, vec![weapon, body]);
        assert_eq!(
            output.iter().any(|e| matches!(e, Output::Parry { .. })),
            parried
        );
        assert_eq!(battle.actor(2).unwrap().health == before, parried);
    }
}

#[test]
fn invalid_guard_phase_does_not_consume_or_hide_body_damage() {
    for phase in [Phase::Idle, Phase::Prepare, Phase::Strike] {
        let (mut battle, due, sequence) = striking(Intent::Attack(Direction::Up));
        let at = due + 250;
        let mut weapon = contact(sequence, due, at as f64);
        weapon.defence_weapon_id = 701;
        weapon.target_intent = Some(Intent::Guard(Direction::Up));
        weapon.target_phase = phase;
        weapon.along = 0.25;
        let mut body = weapon.clone();
        body.defence_weapon_id = 0;
        body.along = 0.5;
        let before = battle.actor(2).unwrap();
        let output = settle(&mut battle, at, vec![weapon, body]);
        assert!(output.iter().all(|e| !matches!(e, Output::Parry { .. })));
        assert!(battle.actor(2).unwrap().health < before.health);
        assert_eq!(battle.actor(2).unwrap().guard, before.guard);
    }
}

#[test]
fn serialized_weapon_and_body_contacts_reach_guard_settlement_without_relabeling() {
    use t7r_battle_core::battle_semantics::evidence::ContactBatch as WireBatch;
    use t7r_battle_runtime::semantic_adapter::ContactSample;
    let hex = include_str!("../../../../../tests/fixtures/protocol/battle-contacts-v3.txt")
        .lines()
        .find(|line| line.starts_with("contacts "))
        .unwrap()
        .split_whitespace()
        .nth(1)
        .unwrap();
    let payload: Vec<_> = hex
        .as_bytes()
        .chunks_exact(2)
        .map(|v| u8::from_str_radix(std::str::from_utf8(v).unwrap(), 16).unwrap())
        .skip(40)
        .collect();
    for (version, target_phase, guard, allowed) in [
        (3, 0., 5, true),
        (3, 0., 6, false),
        (4, -4999., 5, true),
        (4, -4999., 6, false),
    ] {
        let (mut battle, due, sequence) = striking(Intent::Attack(Direction::Up));
        let at = due + 250;
        let mut batch = WireBatch::decode(&payload).unwrap();
        batch.version = version;
        batch.stamp.at_ms = at;
        let weapon = &mut batch.events[0];
        weapon.source_entity = 101;
        weapon.target_entity = 102;
        weapon.sequence = sequence;
        weapon.phase_at_ms = due;
        weapon.occurred_at_ms = at as f64;
        weapon.target_phase_at_ms = target_phase;
        weapon.source_observed_at_ms = Some(at);
        weapon.target_observed_at_ms = Some(at);
        weapon.target_intent = guard;
        weapon.target_phase = 2;
        weapon.defence_weapon_id = 701;
        weapon.region = 0;
        weapon.body_index = -1;
        weapon.along = 0.25;
        let mut body = *weapon;
        body.defence_weapon_id = 0;
        body.region = 2;
        body.along = 0.5;
        batch.events.push(body);
        let sample = ContactSample::decode(&batch.encode().unwrap()).unwrap();
        assert_eq!(sample.events[0].event.target_phase_at_ms, target_phase);
        assert_eq!(sample.events[0].event.region, Region::None);
        assert_eq!(sample.events[0].event.defence_weapon_id, 701);
        let before = battle.actor(2).unwrap().health;
        let output = settle(
            &mut battle,
            at,
            sample.events.into_iter().map(|v| v.event).collect(),
        );
        assert_eq!(battle.actor(2).unwrap().health == before, allowed);
        if allowed {
            assert!(
                output
                    .iter()
                    .any(|v| matches!(v, Output::Parry { evidence, .. }
                if evidence.region == Region::None && evidence.defence_weapon_id == 701))
            );
            assert!(output.iter().all(|v| !matches!(v, Output::Damage { .. })));
        } else {
            assert!(
                output
                    .iter()
                    .any(|v| matches!(v, Output::Damage { evidence, .. }
                if evidence.region == Region::Torso && evidence.defence_weapon_id == 0))
            );
        }
    }
}
