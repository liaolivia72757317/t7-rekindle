use t7_game_server::{
    battle::ActionIntent as A,
    combat::{CombatEffect, ContactReport},
    lobby::Lobby,
    motion::Pose,
    protocol::ErrorCode,
};
fn scene() -> (Lobby, String) {
    let mut l = Lobby::default();
    let r = l.create("a", "combat", 1003).unwrap();
    l.join("b", &r.room_id).unwrap();
    l.ready("b", true).unwrap();
    let m = l.start("a", 0).unwrap().current_match.unwrap().match_id;
    l.loaded("a", &m, 0).unwrap();
    l.loaded("b", &m, 0).unwrap();
    l.advance(3000);
    (l, m)
}
fn pose(l: &mut Lobby, m: &str, seq: u32, now: u64) {
    for (account, x, yaw) in [("a", 0., 0), ("b", 1.2, 180)] {
        l.pose_input(
            account,
            m,
            seq,
            Pose {
                position: [x, 0., 0.5],
                heading: yaw,
                forward_back: 0,
                left_right: 0,
                running: false,
            },
            now,
        )
        .unwrap();
    }
}
fn strike(l: &mut Lobby, m: &str, seq: u32, now: u64) -> u16 {
    l.action_input("a", m, seq, A::AttackUp, now).unwrap();
    l.action_input("a", m, seq + 1, A::AttackRelease, now + 1)
        .unwrap();
    l.advance(now + 360);
    l.take_action_events()
        .into_iter()
        .rfind(|e| e.actor_id.0 == 1 && e.state == 238)
        .unwrap()
        .action_seq
}
fn report(seq: u16) -> ContactReport {
    ContactReport {
        action_seq: seq,
        state: 238,
        elapsed_ms: 50,
        target_actor_id: 2,
        position: [0.9, 0., 1.5],
        normal: [-1., 0., 0.],
        direction: [1., 0., 0.],
        material: 4,
        weapon_contact: false,
    }
}
#[test]
fn contacts_are_authorized_once_and_interrupt_the_victim() {
    let (mut l, m) = scene();
    pose(&mut l, &m, 1, 3000);
    let seq = strike(&mut l, &m, 1, 3000);
    assert!(l.contact_input("a", &m, 1, report(seq), 3420).unwrap());
    assert!(!l.contact_input("a", &m, 2, report(seq), 3421).unwrap());
    assert_eq!(
        l.contact_input("a", &m, 2, report(seq), 3421),
        Err(ErrorCode::StaleInput)
    );
    let event = l.take_combat_events().pop().unwrap();
    match event.effect {
        CombatEffect::Contact { damage, lives, .. } => {
            assert_eq!(damage, 20);
            assert_eq!(lives[1].health, 80);
        }
        _ => panic!("expected contact"),
    }
    let reaction = l.take_action_events().pop().unwrap();
    assert_eq!(reaction.state, 35);
    assert_eq!(reaction.target_actor_id.unwrap().0, 1);
}
#[test]
fn matching_guard_parries_without_damage_and_can_remain_held() {
    let (mut l, m) = scene();
    pose(&mut l, &m, 1, 3000);
    l.action_input("b", &m, 1, A::BlockUp, 3000).unwrap();
    l.advance(3000);
    let seq = strike(&mut l, &m, 1, 3000);
    assert!(l.contact_input("a", &m, 1, report(seq), 3420).unwrap());
    match l.take_combat_events().pop().unwrap().effect {
        CombatEffect::Contact {
            parried,
            damage,
            lives,
            ..
        } => {
            assert!(parried);
            assert_eq!(damage, 0);
            assert_eq!(lives[1].health, 100);
        }
        _ => panic!("expected parry"),
    }
    assert_eq!(
        l.take_action_events()
            .iter()
            .map(|e| e.state)
            .collect::<Vec<_>>(),
        [240, 231]
    );
    l.advance(3520);
    assert_eq!(l.take_action_events()[0].state, 223);
    l.action_input("b", &m, 2, A::BlockRelease, 3521).unwrap();
    assert_eq!(l.take_action_events()[0].state, 2);
}
#[test]
fn misses_self_unknown_targets_invalid_vectors_and_expired_reports_cannot_damage() {
    let (mut l, m) = scene();
    pose(&mut l, &m, 1, 3000);
    let seq = strike(&mut l, &m, 1, 3000);
    let mut cases = vec![];
    for target in [1, 3] {
        let mut r = report(seq);
        r.target_actor_id = target;
        cases.push(r);
    }
    let mut r = report(seq);
    r.normal = [0.; 3];
    cases.push(r);
    let mut r = report(seq);
    r.elapsed_ms = 600;
    cases.push(r);
    let mut r = report(seq);
    r.position = [500., 0., 0.];
    cases.push(r);
    let mut r = report(seq);
    r.state = 235;
    cases.push(r);
    for (i, r) in cases.into_iter().enumerate() {
        assert!(!l.contact_input("a", &m, i as u32 + 1, r, 3420).unwrap());
    }
    assert!(!l.contact_input("a", &m, 10, report(seq), 6000).unwrap());
    assert!(l.take_combat_events().is_empty());
}
#[test]
fn five_hits_kill_then_server_waits_for_respawn_readiness_and_play() {
    let (mut l, m) = scene();
    let mut last = 0;
    for n in 0..5 {
        let now = 3000 + n * 3000;
        l.advance(now);
        pose(&mut l, &m, n as u32 + 1, now);
        let seq = strike(&mut l, &m, n as u32 * 2 + 1, now);
        last = now + 420;
        assert!(
            l.contact_input("a", &m, n as u32 + 1, report(seq), last)
                .unwrap()
        );
        l.advance(now + 1100);
        l.advance(now + 2000);
        l.take_action_events();
    }
    let events = l.take_combat_events();
    match &events.last().unwrap().effect {
        CombatEffect::Contact { lives, .. } => {
            assert_eq!(lives[1].health, 0);
            assert_eq!(lives[0].kills, 1);
            assert_eq!(lives[1].deaths, 1);
        }
        _ => panic!("death expected"),
    }
    assert_eq!(l.respawn("b", &m, last + 7999), Err(ErrorCode::WrongPhase));
    l.advance(last + 8000);
    assert!(matches!(
        l.take_combat_events()[0].effect,
        CombatEffect::RespawnReady { .. }
    ));
    l.respawn("b", &m, last + 8001).unwrap();
    assert!(matches!(
        l.take_combat_events()[0].effect,
        CombatEffect::Respawned { .. }
    ));
    assert_eq!(l.respawn("b", &m, last + 8002), Err(ErrorCode::WrongPhase));
    l.pose_input(
        "b",
        &m,
        6,
        Pose {
            position: [100., 0., 0.5],
            heading: 0,
            forward_back: 0,
            left_right: 0,
            running: false,
        },
        last + 8003,
    )
    .unwrap();
}

#[test]
fn reversed_ground_normals_do_not_rebound_but_ceilings_and_walls_do() {
    for (normal, height, rebound) in [
        ([0., 0., -1.], 0.5, false),
        ([0., 0., 1.], 2.0, true),
        ([-1., 0., 0.], 1.5, true),
    ] {
        let (mut l, m) = scene();
        pose(&mut l, &m, 1, 3000);
        let seq = strike(&mut l, &m, 1, 3000);
        let mut contact = report(seq);
        contact.target_actor_id = 0;
        contact.normal = normal;
        contact.position[2] = height;
        assert!(l.contact_input("a", &m, 1, contact, 3420).unwrap());
        let reactions = l.take_action_events();
        assert_eq!(reactions.last().map(|e| e.state), rebound.then_some(466));
        match l.take_combat_events().pop().unwrap().effect {
            CombatEffect::Contact { damage, lives, .. } => {
                assert_eq!(damage, 0);
                assert!(lives.iter().all(|life| life.health == 100));
            }
            _ => panic!("expected terrain contact"),
        }
    }
}

#[test]
fn releasing_guard_during_parry_feedback_returns_to_idle() {
    let (mut l, m) = scene();
    pose(&mut l, &m, 1, 3000);
    l.action_input("b", &m, 1, A::BlockUp, 3000).unwrap();
    l.advance(3000);
    let seq = strike(&mut l, &m, 1, 3000);
    assert!(l.contact_input("a", &m, 1, report(seq), 3420).unwrap());
    l.take_action_events();
    l.action_input("b", &m, 2, A::BlockRelease, 3450).unwrap();
    l.advance(3520);
    assert_eq!(l.take_action_events()[0].state, 2);
}

#[test]
fn weapon_contact_can_only_parry_and_does_not_consume_a_later_body_hit_when_rejected() {
    let (mut l, m) = scene();
    pose(&mut l, &m, 1, 3000);
    let seq = strike(&mut l, &m, 1, 3000);
    let mut contact = report(seq);
    contact.weapon_contact = true;
    assert!(!l.contact_input("a", &m, 1, contact.clone(), 3420).unwrap());
    l.action_input("b", &m, 1, A::BlockDown, 3420).unwrap();
    l.advance(3420);
    assert!(!l.contact_input("a", &m, 2, contact, 3430).unwrap());
    assert!(l.contact_input("a", &m, 3, report(seq), 3440).unwrap());
    match l.take_combat_events().pop().unwrap().effect {
        CombatEffect::Contact { damage, .. } => assert_eq!(damage, 20),
        _ => panic!("body damage expected"),
    }

    let (mut l, m) = scene();
    pose(&mut l, &m, 1, 3000);
    l.action_input("b", &m, 1, A::BlockUp, 3000).unwrap();
    l.advance(3000);
    let seq = strike(&mut l, &m, 1, 3000);
    let mut contact = report(seq);
    contact.weapon_contact = true;
    assert!(l.contact_input("a", &m, 1, contact, 3420).unwrap());
    assert!(!l.contact_input("a", &m, 2, report(seq), 3430).unwrap());
    match l.take_combat_events().pop().unwrap().effect {
        CombatEffect::Contact {
            parried, damage, ..
        } => {
            assert!(parried);
            assert_eq!(damage, 0);
        }
        _ => panic!("weapon parry expected"),
    }
}

#[test]
fn teammates_cannot_damage_each_other_but_a_second_opponent_can_be_hit() {
    let mut l = Lobby::default();
    let r = l.create("a", "four", 1003).unwrap();
    for account in ["b", "c", "d"] {
        l.join(account, &r.room_id).unwrap();
        l.ready(account, true).unwrap();
    }
    // Joining invalidates previous ready flags.
    for account in ["b", "c", "d"] {
        l.ready(account, true).unwrap();
    }
    let m = l.start("a", 0).unwrap().current_match.unwrap().match_id;
    for account in ["a", "b", "c", "d"] {
        l.loaded(account, &m, 0).unwrap();
    }
    l.advance(3000);
    for (account, x, yaw) in [
        ("a", 0., 0),
        ("b", 1.2, 180),
        ("c", 1.0, 180),
        ("d", 1.3, 180),
    ] {
        l.pose_input(
            account,
            &m,
            1,
            Pose {
                position: [x, 0., 0.5],
                heading: yaw,
                forward_back: 0,
                left_right: 0,
                running: false,
            },
            3000,
        )
        .unwrap();
    }
    let action = strike(&mut l, &m, 1, 3000);
    let mut hit = report(action);
    hit.target_actor_id = 3;
    assert!(!l.contact_input("a", &m, 1, hit, 3420).unwrap());
    let mut hit = report(action);
    hit.target_actor_id = 4;
    assert!(l.contact_input("a", &m, 2, hit, 3421).unwrap());
    let effects = l.take_combat_events();
    let CombatEffect::Contact { lives, target, .. } = &effects[0].effect else {
        panic!("contact expected");
    };
    assert_eq!(target.unwrap().0, 4);
    assert_eq!(lives[2].health, 100);
    assert_eq!(lives[3].health, 80);
}
