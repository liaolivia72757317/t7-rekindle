use t7_game_server::{
    lobby::{Lobby, MAX_ROOMS},
    protocol::ErrorCode,
    room::{COUNTDOWN_MS, LOAD_TIMEOUT_MS, MAP_ID, Phase},
};

fn pair(lobby: &mut Lobby) -> String {
    let room = lobby.create("player-a", "测试房间", MAP_ID).unwrap();
    lobby.join("player-b", &room.room_id).unwrap();
    lobby.ready("player-a", true).unwrap();
    lobby.ready("player-b", true).unwrap();
    room.room_id
}

#[test]
fn ordinary_solo_scenario_keeps_its_selection_through_start_and_loading() {
    let mut lobby = Lobby::default();
    let room = lobby.create("owner", "solo", 1003).unwrap();
    let loading = lobby.start("owner", 0).unwrap();
    assert_eq!(loading.map_id, 1003);
    assert_eq!(loading.phase, Phase::Loading);
    let current = loading.current_match.unwrap();
    let countdown = lobby.loaded("owner", &current.match_id, 10).unwrap();
    assert_eq!(countdown.map_id, 1003);
    assert_eq!(countdown.phase, Phase::Countdown);
    lobby.advance(10 + COUNTDOWN_MS);
    assert_eq!(lobby.snapshot(&room.room_id).unwrap().phase, Phase::Running);
}

#[test]
fn shared_room_has_unique_actors_and_frozen_members_after_start() {
    let mut lobby = Lobby::default();
    let id = pair(&mut lobby);
    let before = lobby.snapshot(&id).unwrap();
    assert_eq!(before.members.len(), 2);
    assert_ne!(before.members[0].actor_id, before.members[1].actor_id);
    let loading = lobby.start("player-a", 100).unwrap();
    assert_eq!(loading.phase, Phase::Loading);
    assert_eq!(lobby.ready("player-b", false), Err(ErrorCode::WrongPhase));
    assert_eq!(lobby.set_camp("player-b", 1), Err(ErrorCode::WrongPhase));
    assert_eq!(lobby.join("player-c", &id), Err(ErrorCode::WrongPhase));
    assert_eq!(lobby.snapshot(&id).unwrap(), loading);
}

#[test]
fn only_both_loaded_can_start_at_the_server_deadline() {
    let mut lobby = Lobby::default();
    pair(&mut lobby);
    let loading = lobby.start("player-a", 0).unwrap();
    let match_id = &loading.current_match.unwrap().match_id;
    assert_eq!(
        lobby.loaded("player-a", match_id, 100).unwrap().phase,
        Phase::Loading
    );
    assert!(lobby.advance(200).is_empty());
    let countdown = lobby.loaded("player-b", match_id, 200).unwrap();
    assert_eq!(countdown.phase, Phase::Countdown);
    assert_eq!(
        countdown.current_match.unwrap().starts_at_ms,
        Some(200 + COUNTDOWN_MS)
    );
    assert!(lobby.advance(200 + COUNTDOWN_MS - 1).is_empty());
    let changed = lobby.advance(200 + COUNTDOWN_MS);
    assert_eq!(changed.len(), 1);
    assert_eq!(changed[0].phase, Phase::Running);
    assert!(lobby.advance(200 + COUNTDOWN_MS + 1).is_empty());
}

#[test]
fn failed_start_preserves_room_and_checks_guest_readiness_and_teams() {
    let mut lobby = Lobby::default();
    let room = lobby.create("player-a", "room", MAP_ID).unwrap();
    lobby.join("player-b", &room.room_id).unwrap();
    let before = lobby.snapshot(&room.room_id).unwrap();
    assert_eq!(lobby.start("player-a", 0), Err(ErrorCode::NotReady));
    assert_eq!(lobby.snapshot(&room.room_id).unwrap(), before);
    lobby.ready("player-a", true).unwrap();
    lobby.ready("player-b", true).unwrap();
    let before = lobby.snapshot(&room.room_id).unwrap();
    assert_eq!(lobby.start("player-b", 0), Err(ErrorCode::NotOwner));
    assert_eq!(lobby.snapshot(&room.room_id).unwrap(), before);
    lobby.set_camp("player-b", 1).unwrap();
    assert!(
        lobby
            .snapshot(&room.room_id)
            .unwrap()
            .members
            .iter()
            .all(|m| !m.ready)
    );
    lobby.ready("player-a", true).unwrap();
    lobby.ready("player-b", true).unwrap();
    let before = lobby.snapshot(&room.room_id).unwrap();
    assert_eq!(lobby.start("player-a", 0), Err(ErrorCode::InvalidTeams));
    assert_eq!(lobby.snapshot(&room.room_id).unwrap(), before);
}

#[test]
fn solo_owner_starts_without_ready_and_only_waits_for_its_own_load() {
    let mut lobby = Lobby::default();
    let created = lobby.create("owner", "solo", MAP_ID).unwrap();
    assert!(!created.members[0].ready);
    let loading = lobby.start("owner", 10).unwrap();
    assert_eq!(loading.phase, Phase::Loading);
    assert_eq!(loading.members.len(), 1);
    assert_eq!(loading.members[0].actor_id, created.members[0].actor_id);
    assert_eq!(
        lobby.join("guest", &created.room_id),
        Err(ErrorCode::WrongPhase)
    );
    let match_id = loading.current_match.unwrap().match_id;
    let view = lobby.client_view("owner", &match_id).unwrap();
    assert_eq!(view.actors().len(), 1);
    assert_eq!(view.actors()[0].local_rid.0, 1);
    let countdown = lobby.loaded("owner", &match_id, 20).unwrap();
    assert_eq!(countdown.phase, Phase::Countdown);
    assert!(lobby.advance(20 + COUNTDOWN_MS - 1).is_empty());
    assert_eq!(lobby.advance(20 + COUNTDOWN_MS)[0].phase, Phase::Running);
    lobby.leave("owner", false).unwrap();
    assert!(lobby.list().is_empty());
}

#[test]
fn owner_does_not_need_ready_but_guests_do_and_only_owner_can_start() {
    let mut lobby = Lobby::default();
    let created = lobby.create("owner", "room", MAP_ID).unwrap();
    lobby.join("guest", &created.room_id).unwrap();
    lobby.ready("owner", true).unwrap();
    let before = lobby.snapshot(&created.room_id).unwrap();
    assert_eq!(lobby.start("guest", 0), Err(ErrorCode::NotOwner));
    assert_eq!(lobby.start("owner", 0), Err(ErrorCode::NotReady));
    assert_eq!(lobby.snapshot(&created.room_id).unwrap(), before);
    lobby.ready("guest", true).unwrap();
    lobby.ready("owner", false).unwrap();
    let loading = lobby.start("owner", 0).unwrap();
    assert_eq!(loading.phase, Phase::Loading);
    assert!(!loading.members[0].ready);
}

#[test]
fn last_guest_leaving_allows_solo_restart_and_transfer_exempts_the_new_owner() {
    let mut lobby = Lobby::default();
    let created = lobby.create("owner", "room", MAP_ID).unwrap();
    lobby.join("guest", &created.room_id).unwrap();
    lobby.leave("owner", false).unwrap();
    let inherited = lobby.snapshot(&created.room_id).unwrap();
    assert_eq!(inherited.owner_account_id, "guest");
    assert!(!inherited.members[0].ready);
    let loading = lobby.start("guest", 0).unwrap();
    lobby.advance(LOAD_TIMEOUT_MS);
    let waiting = lobby.snapshot(&created.room_id).unwrap();
    assert_eq!(waiting.phase, Phase::Waiting);
    assert!(!waiting.members[0].ready);
    lobby.join("owner", &created.room_id).unwrap();
    assert_eq!(
        lobby.start("guest", LOAD_TIMEOUT_MS + 1),
        Err(ErrorCode::NotReady)
    );
    lobby.leave("owner", false).unwrap();
    let restarted = lobby.start("guest", LOAD_TIMEOUT_MS + 2).unwrap();
    assert_ne!(
        restarted.current_match.unwrap().match_id,
        loading.current_match.unwrap().match_id
    );
}

#[test]
fn timeout_clears_load_state_and_rejects_previous_match() {
    let mut lobby = Lobby::default();
    let id = pair(&mut lobby);
    let old = lobby
        .start("player-a", 10)
        .unwrap()
        .current_match
        .unwrap()
        .match_id;
    lobby.loaded("player-a", &old, 20).unwrap();
    assert_eq!(
        lobby.loaded("player-b", &old, 10 + LOAD_TIMEOUT_MS),
        Err(ErrorCode::LoadTimeout)
    );
    let changed = lobby.advance(10 + LOAD_TIMEOUT_MS);
    assert_eq!(changed[0].phase, Phase::Waiting);
    assert_eq!(changed[0].last_abort_reason, Some("loadTimeout"));
    assert!(changed[0].members.iter().all(|m| !m.loaded && !m.ready));
    assert!(changed[0].current_match.is_none());
    lobby.ready("player-a", true).unwrap();
    lobby.ready("player-b", true).unwrap();
    let new = lobby.start("player-a", 100_000).unwrap();
    assert_ne!(new.current_match.as_ref().unwrap().match_id, old);
    assert_eq!(
        lobby.loaded("player-a", &old, 100_001),
        Err(ErrorCode::StaleMatch)
    );
    assert_eq!(lobby.snapshot(&id).unwrap(), new);
}

#[test]
fn owner_disconnect_aborts_loading_transfers_ownership_and_destroys_empty_room() {
    let mut lobby = Lobby::default();
    let id = pair(&mut lobby);
    lobby.start("player-a", 0).unwrap();
    assert_eq!(lobby.leave("player-a", true).unwrap(), id);
    let room = lobby.snapshot(&id).unwrap();
    assert_eq!(room.owner_account_id, "player-b");
    assert_eq!(room.phase, Phase::Waiting);
    assert_eq!(room.last_abort_reason, Some("disconnected"));
    assert_eq!(room.members.len(), 1);
    assert!(!room.members[0].ready);
    lobby.join("player-a", &id).unwrap();
    let room = lobby.snapshot(&id).unwrap();
    assert!(room.members[1].actor_id > room.members[0].actor_id);
    lobby.leave("player-a", false).unwrap();
    lobby.leave("player-b", false).unwrap();
    assert!(lobby.snapshot(&id).is_none());
    assert!(lobby.list().is_empty());
    assert!(lobby.create("player-a", "new room", MAP_ID).is_ok());
}

#[test]
fn a_member_cannot_join_two_rooms_or_claim_other_room_ownership() {
    let mut lobby = Lobby::default();
    let a = lobby.create("player-a", "a", MAP_ID).unwrap();
    let b = lobby.create("player-b", "b", MAP_ID).unwrap();
    assert_eq!(
        lobby.join("player-a", &b.room_id),
        Err(ErrorCode::AlreadyInRoom)
    );
    assert_eq!(
        lobby.create("player-a", "c", MAP_ID),
        Err(ErrorCode::AlreadyInRoom)
    );
    assert_eq!(lobby.snapshot(&a.room_id).unwrap(), a);
    assert_eq!(lobby.snapshot(&b.room_id).unwrap(), b);
    assert_eq!(lobby.ready("unknown", true), Err(ErrorCode::NotInRoom));
}

#[test]
fn invalid_requests_do_not_reserve_accounts_and_room_count_is_bounded() {
    let mut lobby = Lobby::default();
    assert_eq!(
        lobby.create("player-a", "", MAP_ID),
        Err(ErrorCode::InvalidRoomName)
    );
    assert_eq!(
        lobby.create("player-a", "room\nname", MAP_ID),
        Err(ErrorCode::InvalidRoomName)
    );
    assert_eq!(
        lobby.create("player-a", "room", 0),
        Err(ErrorCode::UnsupportedMap)
    );
    assert_eq!(
        lobby.join("player-a", "missing"),
        Err(ErrorCode::RoomNotFound)
    );
    for index in 0..MAX_ROOMS {
        lobby
            .create(&format!("player-{index}"), "room", MAP_ID)
            .unwrap();
    }
    assert_eq!(
        lobby.create("extra", "room", MAP_ID),
        Err(ErrorCode::RoomLimit)
    );
    lobby.leave("player-0", false).unwrap();
    assert!(lobby.create("extra", "room", MAP_ID).is_ok());
}

#[test]
fn leaving_running_room_allows_a_clean_new_match() {
    let mut lobby = Lobby::default();
    let id = pair(&mut lobby);
    let first = lobby
        .start("player-a", 0)
        .unwrap()
        .current_match
        .unwrap()
        .match_id;
    lobby.loaded("player-a", &first, 1).unwrap();
    lobby.loaded("player-b", &first, 1).unwrap();
    lobby.advance(COUNTDOWN_MS + 1);
    lobby.leave("player-b", false).unwrap();
    assert_eq!(lobby.snapshot(&id).unwrap().phase, Phase::Waiting);
    lobby.join("player-b", &id).unwrap();
    lobby.ready("player-a", true).unwrap();
    lobby.ready("player-b", true).unwrap();
    let second = lobby
        .start("player-a", 10_000)
        .unwrap()
        .current_match
        .unwrap()
        .match_id;
    assert_ne!(first, second);
    assert_eq!(
        lobby.loaded("player-b", &first, 10_001),
        Err(ErrorCode::StaleMatch)
    );
}

#[test]
fn malformed_protocol_and_noncanonical_sequences_are_rejected() {
    use t7_game_server::protocol::ClientRequest;
    let valid = r#"{"protocolVersion":1,"requestId":"1","command":{"type":"ready","payload":{"ready":true}}}"#;
    assert_eq!(
        serde_json::from_str::<ClientRequest>(valid)
            .unwrap()
            .sequence(),
        Ok(1)
    );
    for id in ["0", "01", "-1", "18446744073709551616", ""] {
        let body = valid.replace("\"requestId\":\"1\"", &format!("\"requestId\":\"{id}\""));
        assert_eq!(
            serde_json::from_str::<ClientRequest>(&body)
                .unwrap()
                .sequence(),
            Err(ErrorCode::InvalidRequestId)
        );
    }
    assert!(serde_json::from_str::<ClientRequest>(&valid.replace("true", "1")).is_err());
    assert!(
        serde_json::from_str::<ClientRequest>(
            &valid.replace("\"ready\":true", "\"ready\":true,\"actorId\":1")
        )
        .is_err()
    );
    assert!(
        serde_json::from_str::<ClientRequest>(
            &valid.replace("\"protocolVersion\":1", "\"protocolVersion\":1,\"extra\":0")
        )
        .is_err()
    );
}
