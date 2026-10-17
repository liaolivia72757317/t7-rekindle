use super::*;

fn striking(attack: Intent) -> (BattleSession, u64, u16) {
    striking_at(attack, 250)
}

fn striking_at(attack: Intent, elapsed: u64) -> (BattleSession, u64, u16) {
    strike_session(session(), attack, elapsed)
}

fn strike_session(
    mut battle: BattleSession,
    attack: Intent,
    elapsed: u64,
) -> (BattleSession, u64, u16) {
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
            due + elapsed,
            Input::World(world(due + elapsed, sequence)),
            publish,
        )
        .unwrap();
    (battle, due, sequence)
}

fn guard_session(strike_duration_ms: u16, grade: &str) -> BattleSession {
    let mut raw: serde_json::Value = serde_json::from_slice(include_bytes!(
        "../../../../../tests/fixtures/resources/bundle-v1.json"
    ))
    .unwrap();
    raw["damageProfiles"]["1101"]["weapons"]["701"]["attackGrade"] = serde_json::json!(grade);
    for id in ["300540", "300550", "300560", "300570"] {
        let profile = &mut raw["combatProfiles"]["1101"]["701"]["attacks"][id];
        profile["phases"][2] = serde_json::json!(strike_duration_ms);
        profile["contactEndMs"] = serde_json::json!(strike_duration_ms - 50);
    }
    let bundle = ResourceBundle::from_slice(&serde_json::to_vec(&raw).unwrap()).unwrap();
    BattleSession::new(
        &bundle,
        Arc::new(BattleResources::prepare(&bundle).unwrap()),
        Config {
            epoch: 7,
            started_at_ms: 0,
            duration_ms: 1200000,
            kill_target: 10,
            seed: 42,
            members: vec![member(1), member(2)],
        },
    )
    .unwrap()
}

#[test]
fn guard_volume_break_waits_for_body_across_batches_and_rolls_back() {
    use t7r_battle_runtime::battle_session::ParryOutcome;
    for body_later in [false, true] {
        let battle = guard_session(600, "SS");
        let (mut battle, due, sequence) =
            strike_session(battle, Intent::Attack(Direction::Up), 250);
        let at = due + 250;
        let before = battle.actor(2).unwrap();
        let mut volume = contact(sequence, due, at as f64);
        volume.guard_volume = true;
        volume.region = Region::None;
        volume.defence_weapon_id = 701;
        volume.target_intent = Some(Intent::Guard(Direction::Up));
        volume.target_phase = Phase::Hold;
        let input = Input::Contacts(ContactBatch {
            epoch: 7,
            batch_id: at,
            through_at_ms: at as f64,
            dropped: 0,
            events: vec![volume.clone()],
        });
        assert!(
            battle
                .dispatch(7, at, input, |_| Err(io::Error::other(
                    "publication failed"
                )))
                .is_err()
        );
        assert_eq!(battle.actor(2).unwrap(), before);
        let output = settle(&mut battle, at, vec![volume.clone(), volume.clone()]);
        assert_eq!(
            output
                .iter()
                .filter(|e| matches!(
                    e,
                    Output::Parry {
                        outcome: ParryOutcome::Broken,
                        ..
                    }
                ))
                .count(),
            1
        );
        assert!(output.iter().all(|e| !matches!(e, Output::Damage { .. })));
        assert_eq!(battle.actor(2).unwrap().health, before.health);
        assert_eq!(battle.actor(1).unwrap().action.phase, Phase::Strike);
        volume.occurred_at_ms += 10.;
        assert!(
            settle(&mut battle, at + 10, vec![volume])
                .iter()
                .all(|e| !matches!(e, Output::Parry { .. }))
        );
        let mut body = contact(sequence, due, (at + 20) as f64);
        body.region = Region::Head;
        if body_later {
            let input = Input::Contacts(ContactBatch {
                epoch: 7,
                batch_id: at + 20,
                through_at_ms: (at + 20) as f64,
                dropped: 0,
                events: vec![body.clone()],
            });
            assert!(
                battle
                    .dispatch(7, at + 20, input, |_| Err(io::Error::other("retry body")))
                    .is_err()
            );
            assert_eq!(battle.actor(2).unwrap().health, before.health);
            let output = settle(&mut battle, at + 20, vec![body.clone(), body]);
            assert_eq!(
                output
                    .iter()
                    .filter(
                        |e| matches!(e, Output::Damage { guard_break: true, evidence, .. }
                if evidence.region == Region::Head && !evidence.guard_volume)
                    )
                    .count(),
                1,
                "{output:#?}"
            );
            assert!(battle.actor(2).unwrap().health < before.health);
        } else {
            body.occurred_at_ms = (due + 601) as f64;
            settle(&mut battle, due + 601, vec![body]);
            assert_eq!(battle.actor(2).unwrap().health, before.health);
        }
    }
}

#[test]
fn guard_volume_break_settles_a_later_body_in_the_same_batch() {
    use t7r_battle_runtime::battle_session::ParryOutcome;
    let (mut battle, due, sequence) =
        strike_session(guard_session(600, "SS"), Intent::Attack(Direction::Up), 250);
    let at = due + 250;
    let mut volume = contact(sequence, due, at as f64);
    volume.guard_volume = true;
    volume.region = Region::None;
    volume.defence_weapon_id = 701;
    volume.target_intent = Some(Intent::Guard(Direction::Up));
    volume.target_phase = Phase::Hold;
    let mut body = contact(sequence, due, at as f64);
    body.along = volume.along + 0.1;
    body.region = Region::Legs;
    let output = settle(&mut battle, at, vec![body.clone(), volume, body]);
    assert_eq!(
        output
            .iter()
            .filter(|e| matches!(
                e,
                Output::Parry {
                    outcome: ParryOutcome::Broken,
                    ..
                }
            ))
            .count(),
        1
    );
    assert_eq!(
        output
            .iter()
            .filter(
                |e| matches!(e, Output::Damage { guard_break: true, evidence, .. }
        if evidence.region == Region::Legs && !evidence.guard_volume)
            )
            .count(),
        1
    );
}

#[test]
fn pending_guard_break_does_not_transfer_to_a_replaced_entity_or_a_stopped_swing() {
    for replaced in [false, true] {
        let (mut battle, due, sequence) =
            strike_session(guard_session(600, "SS"), Intent::Attack(Direction::Up), 250);
        let at = due + 250;
        let before = battle.actor(2).unwrap().health;
        let mut volume = contact(sequence, due, at as f64);
        volume.guard_volume = true;
        volume.region = Region::None;
        volume.defence_weapon_id = 701;
        volume.target_intent = Some(Intent::Guard(Direction::Up));
        volume.target_phase = Phase::Hold;
        settle(&mut battle, at, vec![volume]);
        let mut body = contact(sequence, due, (at + 20) as f64);
        if replaced {
            let mut next = (*world(at + 10, sequence)).clone();
            next.actors.get_mut(&2).unwrap().entity = 902;
            battle
                .dispatch(7, at + 10, Input::World(Arc::new(next)), publish)
                .unwrap();
            settle(
                &mut battle,
                at + 11,
                vec![contact(sequence, due, (at + 11) as f64)],
            );
            assert_eq!(battle.actor(2).unwrap().health, before);
            body.target_entity = 902;
        } else {
            let mut wall = contact(sequence, due, (at + 10) as f64);
            wall.target_id = 0;
            wall.target_entity = 0;
            wall.region = Region::None;
            settle(&mut battle, at + 10, vec![wall]);
        }
        let output = settle(&mut battle, at + 20, vec![body]);
        assert!(output.iter().all(|e| !matches!(
            e,
            Output::Damage {
                guard_break: true,
                ..
            }
        )));
        assert_eq!(battle.actor(2).unwrap().health < before, replaced);
    }
}

#[test]
fn chamber_window_scales_with_full_strike_duration_and_reports_rejections() {
    use t7r_battle_runtime::battle_session::{GuardRejectReason, ParryOutcome};
    for duration in [300, 602, 1200] {
        for offset in [0, 1] {
            let elapsed = u64::from(duration / 3) + offset;
            let (mut battle, due, sequence) = strike_session(
                guard_session(duration, "C"),
                Intent::Attack(Direction::Down),
                elapsed,
            );
            let at = due + elapsed;
            let mut volume = contact(sequence, due, at as f64);
            volume.guard_volume = true;
            volume.region = Region::None;
            volume.defence_weapon_id = 701;
            volume.target_intent = Some(Intent::Attack(Direction::Up));
            volume.target_phase = Phase::Prepare;
            let output = settle(&mut battle, at, vec![volume]);
            assert_eq!(
                output.iter().any(|e| matches!(
                    e,
                    Output::Parry {
                        outcome: ParryOutcome::Chambered,
                        ..
                    }
                )),
                offset == 0
            );
            assert_eq!(
                output.iter().any(|e| matches!(e, Output::GuardRejected {
                reason: GuardRejectReason::ChamberWindow, strike_duration_ms, evidence
            } if *strike_duration_ms == duration && evidence.guard_volume)),
                offset == 1
            );
        }
    }
}

#[test]
fn chamber_uses_attacker_first_third_instead_of_defender_elapsed() {
    for elapsed in [70, 150, 200, 201, 250] {
        for preparation_age in [0, 49, 50, 201, 400] {
            for phase in [Phase::Prepare, Phase::Hold, Phase::Strike] {
                let (mut battle, due, sequence) =
                    striking_at(Intent::Attack(Direction::Down), elapsed);
                let at = due + elapsed;
                let mut weapon = contact(sequence, due, at as f64);
                weapon.defence_weapon_id = 701;
                weapon.region = Region::None;
                weapon.target_intent = Some(Intent::Attack(Direction::Up));
                weapon.target_phase = phase;
                weapon.target_phase_at_ms = at as f64 - f64::from(preparation_age);
                weapon.source_observed_at_ms = Some(at);
                weapon.target_observed_at_ms = Some(at);
                let output = settle(&mut battle, at, vec![weapon]);
                assert_eq!(
                    output.iter().any(|e| matches!(e, Output::Parry { .. })),
                    elapsed <= 200 && phase == Phase::Prepare,
                    "attack elapsed={elapsed}, preparation age={preparation_age}, phase={phase:?}"
                );
            }
        }
    }
}

fn settle(battle: &mut BattleSession, at: u64, events: Vec<ContactEvent>) -> Vec<Output> {
    let mut output = Vec::new();
    battle
        .dispatch(
            7,
            at,
            Input::Contacts(ContactBatch {
                epoch: 7,
                batch_id: at,
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
                            batch_id: at,
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
        (5, -4999., 5, true),
        (5, -4999., 6, false),
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
        weapon.flags = if version == 5 { 8 } else { 0 };
        weapon.region = 0;
        weapon.body_index = -1;
        weapon.along = 0.25;
        let mut body = *weapon;
        body.flags = 0;
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
