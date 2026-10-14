use t7_game_server::{battle::ActionIntent, lobby::Lobby, protocol::ErrorCode, room::COUNTDOWN_MS};

fn running(peer: bool) -> (Lobby, String, String) {
    let mut lobby = Lobby::default();
    let room = lobby.create("owner", "room", 1003).unwrap();
    if peer {
        lobby.join("guest", &room.room_id).unwrap();
        lobby.ready("guest", true).unwrap();
    }
    let loading = lobby.start("owner", 0).unwrap();
    let match_id = loading.current_match.unwrap().match_id;
    lobby.loaded("owner", &match_id, 0).unwrap();
    if peer {
        lobby.loaded("guest", &match_id, 0).unwrap();
    }
    lobby.advance(COUNTDOWN_MS);
    (lobby, room.room_id, match_id)
}

fn states(lobby: &mut Lobby) -> Vec<u16> {
    lobby
        .take_action_events()
        .into_iter()
        .map(|event| event.state)
        .collect()
}

#[test]
fn early_attack_release_waits_for_prepare_then_strikes_recovers_and_idles() {
    let (mut lobby, _, id) = running(false);
    let now = COUNTDOWN_MS;
    lobby
        .action_input("owner", &id, 1, ActionIntent::AttackUp, now)
        .unwrap();
    assert_eq!(states(&mut lobby), [235]);
    lobby
        .action_input("owner", &id, 2, ActionIntent::AttackRelease, now + 5)
        .unwrap();
    assert!(states(&mut lobby).is_empty());
    lobby.advance(now + 359);
    assert!(states(&mut lobby).is_empty());
    lobby.advance(now + 360);
    assert_eq!(states(&mut lobby), [238]);
    lobby.advance(now + 1000);
    assert_eq!(states(&mut lobby), [239]);
    lobby.advance(now + 1500);
    assert_eq!(states(&mut lobby), [2]);
}

#[test]
fn held_attack_and_block_require_their_own_release() {
    let (mut lobby, _, id) = running(false);
    let now = COUNTDOWN_MS;
    lobby
        .action_input("owner", &id, 1, ActionIntent::AttackDown, now)
        .unwrap();
    assert_eq!(states(&mut lobby), [247]);
    lobby.advance(now + 400);
    assert_eq!(states(&mut lobby), [249]);
    lobby
        .action_input("owner", &id, 2, ActionIntent::BlockRelease, now + 500)
        .unwrap();
    lobby.advance(now + 10000);
    assert!(states(&mut lobby).is_empty());
    lobby
        .action_input("owner", &id, 3, ActionIntent::BlockLeft, now + 10000)
        .unwrap();
    assert_eq!(states(&mut lobby), [306]);
    lobby.advance(now + 10000);
    assert_eq!(states(&mut lobby), [226]);
    lobby
        .action_input("owner", &id, 4, ActionIntent::AttackRelease, now + 10001)
        .unwrap();
    lobby.advance(now + 20000);
    assert!(states(&mut lobby).is_empty());
    lobby
        .action_input("owner", &id, 5, ActionIntent::BlockRelease, now + 20000)
        .unwrap();
    assert_eq!(states(&mut lobby), [2]);
    lobby.advance(now + 20001);
    assert!(states(&mut lobby).is_empty());
}

#[test]
fn buffered_released_input_cannot_leave_a_stuck_held_action() {
    let (mut lobby, _, id) = running(false);
    let now = COUNTDOWN_MS;
    for (seq, intent) in [ActionIntent::AttackRight, ActionIntent::AttackRelease]
        .into_iter()
        .enumerate()
    {
        lobby
            .action_input("owner", &id, seq as u32 + 1, intent, now)
            .unwrap();
    }
    states(&mut lobby);
    lobby.advance(now + 360);
    assert_eq!(states(&mut lobby), [244]);
    lobby
        .action_input("owner", &id, 3, ActionIntent::BlockUp, now + 400)
        .unwrap();
    lobby
        .action_input("owner", &id, 4, ActionIntent::BlockRelease, now + 410)
        .unwrap();
    lobby.advance(now + 1000);
    assert_eq!(states(&mut lobby), [305]);
    lobby.advance(now + 1500);
    assert_eq!(states(&mut lobby), [2]);
    lobby.advance(now + 1501);
    assert!(states(&mut lobby).is_empty());
    lobby.advance(now + 9000);
    assert!(states(&mut lobby).is_empty());
}

#[test]
fn actions_are_match_bound_identity_bound_ordered_and_discarded_on_exit() {
    let (mut lobby, room, id) = running(true);
    assert_eq!(
        lobby.action_input("owner", "old-match", 1, ActionIntent::AttackLeft, 3000),
        Err(ErrorCode::StaleMatch)
    );
    assert_eq!(
        lobby.action_input("outsider", &id, 1, ActionIntent::AttackLeft, 3000),
        Err(ErrorCode::NotInRoom)
    );
    lobby
        .action_input("guest", &id, 1, ActionIntent::AttackLeft, 3000)
        .unwrap();
    lobby
        .action_input("owner", &id, 1, ActionIntent::BlockRight, 3000)
        .unwrap();
    let events = lobby.take_action_events();
    assert_eq!(
        events.iter().map(|e| e.actor_id.0).collect::<Vec<_>>(),
        [2, 1]
    );
    assert_eq!(
        events.iter().map(|e| e.event_seq).collect::<Vec<_>>(),
        [1, 2]
    );
    assert!(
        events
            .iter()
            .all(|e| e.match_id == id && e.room_id == room && e.action_seq == 2)
    );
    assert_eq!(
        lobby.action_input("guest", &id, 1, ActionIntent::AttackRelease, 3100),
        Err(ErrorCode::StaleInput)
    );
    assert!(states(&mut lobby).is_empty());
    lobby.leave("owner", false).unwrap();
    lobby.advance(5000);
    assert!(states(&mut lobby).is_empty());
    assert_eq!(
        lobby.action_input("guest", &id, 2, ActionIntent::AttackRelease, 5000),
        Err(ErrorCode::WrongPhase)
    );
}

#[test]
fn delayed_ticks_do_not_burst_or_shorten_recovery() {
    let (mut lobby, _, id) = running(false);
    lobby
        .action_input("owner", &id, 1, ActionIntent::AttackUp, 3000)
        .unwrap();
    lobby
        .action_input("owner", &id, 2, ActionIntent::AttackRelease, 3001)
        .unwrap();
    states(&mut lobby);
    lobby.advance(100000);
    assert_eq!(states(&mut lobby), [238]);
    lobby.advance(100001);
    assert!(states(&mut lobby).is_empty());
    lobby.advance(100640);
    assert_eq!(states(&mut lobby), [239]);
    lobby.advance(101140);
    assert_eq!(states(&mut lobby), [2]);
}

#[test]
fn next_attack_waits_one_hundred_ms_of_recovery_then_uses_buffered_release() {
    let (mut lobby, _, id) = running(false);
    lobby
        .action_input("owner", &id, 1, ActionIntent::AttackUp, 3000)
        .unwrap();
    lobby
        .action_input("owner", &id, 2, ActionIntent::AttackRelease, 3001)
        .unwrap();
    states(&mut lobby);
    lobby.advance(3360);
    states(&mut lobby);
    lobby.advance(4000);
    assert_eq!(states(&mut lobby), [239]);
    lobby
        .action_input("owner", &id, 3, ActionIntent::AttackLeft, 4001)
        .unwrap();
    assert!(states(&mut lobby).is_empty());
    lobby
        .action_input("owner", &id, 4, ActionIntent::AttackRelease, 4002)
        .unwrap();
    lobby.advance(4099);
    assert!(states(&mut lobby).is_empty());
    lobby.advance(4100);
    assert_eq!(states(&mut lobby), [253]);
    lobby.advance(4460);
    assert_eq!(states(&mut lobby), [256]);
}

#[test]
fn a_released_buffered_attack_preserves_the_full_strike_and_minimum_recovery() {
    let (mut lobby, _, id) = running(false);
    for (seq, intent) in [
        ActionIntent::AttackUp,
        ActionIntent::AttackRelease,
        ActionIntent::AttackRight,
        ActionIntent::AttackRelease,
    ]
    .into_iter()
    .enumerate()
    {
        lobby
            .action_input("owner", &id, seq as u32 + 1, intent, 3000)
            .unwrap();
    }
    states(&mut lobby);
    lobby.advance(3360);
    assert_eq!(states(&mut lobby), [238]);
    lobby.advance(3799);
    assert!(states(&mut lobby).is_empty());
    lobby.advance(3800);
    assert!(states(&mut lobby).is_empty());
    lobby.advance(4000);
    assert_eq!(states(&mut lobby), [239]);
    lobby.advance(4099);
    assert!(states(&mut lobby).is_empty());
    lobby.advance(4100);
    assert_eq!(states(&mut lobby), [241]);
    lobby.advance(4460);
    assert_eq!(states(&mut lobby), [244]);
}

#[test]
fn a_new_attack_cannot_cancel_the_strike_or_first_one_hundred_ms_of_recovery() {
    let (mut lobby, _, id) = running(false);
    lobby
        .action_input("owner", &id, 1, ActionIntent::AttackDown, 3000)
        .unwrap();
    lobby
        .action_input("owner", &id, 2, ActionIntent::AttackRelease, 3001)
        .unwrap();
    states(&mut lobby);
    lobby.advance(3400);
    assert_eq!(states(&mut lobby), [250]);
    lobby.advance(3899);
    assert!(states(&mut lobby).is_empty());
    lobby
        .action_input("owner", &id, 3, ActionIntent::AttackLeft, 3900)
        .unwrap();
    assert!(states(&mut lobby).is_empty());
    lobby.advance(4100);
    assert_eq!(states(&mut lobby), [251]);
    lobby.advance(4199);
    assert!(states(&mut lobby).is_empty());
    lobby.advance(4200);
    assert_eq!(states(&mut lobby), [253]);
}
