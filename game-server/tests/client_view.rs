use t7_game_server::{
    client_view::{ActorId, LocalRid},
    lobby::Lobby,
    protocol::{ClientRequest, ErrorCode},
    room::{COUNTDOWN_MS, MAP_ID},
};

fn start(lobby: &mut Lobby) -> String {
    lobby.ready("player-a", true).unwrap();
    lobby.ready("player-b", true).unwrap();
    lobby
        .start("player-a", 100)
        .unwrap()
        .current_match
        .unwrap()
        .match_id
}

#[test]
fn each_client_has_itself_at_rid_one_and_round_trips_all_actor_references() {
    let mut lobby = Lobby::default();
    let room = lobby.create("player-a", "room", MAP_ID).unwrap();
    lobby.join("player-b", &room.room_id).unwrap();
    // Keep global camps even when the room owner is on camp 2.
    lobby.set_camp("player-a", 2).unwrap();
    lobby.set_camp("player-b", 1).unwrap();
    let match_id = start(&mut lobby);
    let before = lobby.snapshot(&room.room_id).unwrap();
    let a = lobby.client_view("player-a", &match_id).unwrap();
    let b = lobby.client_view("player-b", &match_id).unwrap();
    assert_eq!(a.to_actor(&match_id, LocalRid(1)), Ok(ActorId(1)));
    assert_eq!(b.to_actor(&match_id, LocalRid(1)), Ok(ActorId(2)));
    assert_eq!(a.to_actor(&match_id, LocalRid(2)), Ok(ActorId(2)));
    assert_eq!(b.to_actor(&match_id, LocalRid(2)), Ok(ActorId(1)));
    assert_eq!(a.actors()[0].camp, 2);
    assert_eq!(b.actors()[0].camp, 1);
    for view in [&a, &b] {
        assert_eq!(
            view.actors().iter().filter(|actor| actor.is_local).count(),
            1
        );
        for actor in view.actors() {
            let rid = view.to_local(&match_id, actor.actor_id).unwrap();
            assert_eq!(view.to_actor(&match_id, rid), Ok(actor.actor_id));
        }
        assert_eq!(
            view.to_actor(&match_id, LocalRid(0)),
            Err(ErrorCode::UnknownActor)
        );
        assert_eq!(
            view.to_actor(&match_id, LocalRid(3)),
            Err(ErrorCode::UnknownActor)
        );
        assert_eq!(
            view.to_local(&match_id, ActorId(99)),
            Err(ErrorCode::UnknownActor)
        );
        assert_eq!(
            view.to_actor("old-match", LocalRid(1)),
            Err(ErrorCode::StaleMatch)
        );
        assert_eq!(
            view.to_local("old-match", ActorId(1)),
            Err(ErrorCode::StaleMatch)
        );
    }
    // Reading an adapter view does not advance or change shared room state.
    assert_eq!(lobby.snapshot(&room.room_id).unwrap(), before);
    lobby.loaded("player-a", &match_id, 101).unwrap();
    lobby.loaded("player-b", &match_id, 102).unwrap();
    assert_eq!(
        lobby.client_view("player-a", &match_id).unwrap().actors(),
        a.actors()
    );
    lobby.advance(102 + COUNTDOWN_MS);
    assert_eq!(
        lobby.client_view("player-b", &match_id).unwrap().actors(),
        b.actors()
    );
}

#[test]
fn reconnect_assigns_a_new_global_actor_but_still_uses_local_rid_one() {
    let mut lobby = Lobby::default();
    let room = lobby.create("player-a", "room", MAP_ID).unwrap();
    lobby.join("player-b", &room.room_id).unwrap();
    let old = start(&mut lobby);
    lobby.leave("player-b", true).unwrap();
    assert_eq!(
        lobby.client_view("player-b", &old),
        Err(ErrorCode::NotInRoom)
    );
    assert_eq!(
        lobby.client_view("player-a", &old),
        Err(ErrorCode::WrongPhase)
    );
    lobby.join("player-b", &room.room_id).unwrap();
    let new = start(&mut lobby);
    assert_eq!(
        lobby.client_view("player-a", &old),
        Err(ErrorCode::StaleMatch)
    );
    let a = lobby.client_view("player-a", &new).unwrap();
    let b = lobby.client_view("player-b", &new).unwrap();
    assert_eq!(b.to_actor(&new, LocalRid(1)), Ok(ActorId(3)));
    assert_eq!(a.to_local(&new, ActorId(3)), Ok(LocalRid(2)));
    assert_eq!(a.to_local(&new, ActorId(2)), Err(ErrorCode::UnknownActor));
    assert_eq!(b.to_local(&new, ActorId(1)), Ok(LocalRid(2)));
}

#[test]
fn clients_can_only_read_their_own_frozen_match_view() {
    let mut lobby = Lobby::default();
    let room = lobby.create("player-a", "room", MAP_ID).unwrap();
    assert_eq!(
        lobby.client_view("player-a", "any"),
        Err(ErrorCode::WrongPhase)
    );
    lobby.join("player-b", &room.room_id).unwrap();
    let match_id = start(&mut lobby);
    assert_eq!(
        lobby.client_view("outsider", &match_id),
        Err(ErrorCode::NotInRoom)
    );
    lobby.create("outsider", "other room", MAP_ID).unwrap();
    assert_eq!(
        lobby.client_view("outsider", &match_id),
        Err(ErrorCode::WrongPhase)
    );
    // The wire request accepts a match reference, never a claimed recipient.
    let spoofed = serde_json::json!({
        "protocolVersion": 1, "requestId": "1",
        "command": {"type": "getClientView", "payload": {
            "matchId": match_id, "accountId": "player-a"
        }}
    });
    assert!(serde_json::from_value::<ClientRequest>(spoofed).is_err());
}

#[test]
fn twenty_player_rosters_keep_unique_enemy_and_teammate_slots_for_every_recipient() {
    use t7_game_server::room::CAPACITY;
    let mut lobby = Lobby::default();
    let room = lobby.create("p0", "full", MAP_ID).unwrap();
    for n in 1..CAPACITY {
        lobby.join(&format!("p{n}"), &room.room_id).unwrap();
    }
    assert_eq!(
        lobby.join("overflow", &room.room_id),
        Err(ErrorCode::RoomFull)
    );
    let before = lobby.snapshot(&room.room_id).unwrap();
    assert_eq!(before.members.iter().filter(|m| m.camp == 1).count(), 10);
    assert_eq!(before.members.iter().filter(|m| m.camp == 2).count(), 10);
    assert_eq!(lobby.set_camp("p1", 1), Err(ErrorCode::InvalidTeams));
    assert_eq!(lobby.start("p0", 0), Err(ErrorCode::NotReady));
    for n in 1..CAPACITY {
        lobby.ready(&format!("p{n}"), true).unwrap();
    }
    let loading = lobby.start("p0", 0).unwrap();
    let id = loading.current_match.unwrap().match_id;
    for n in 0..CAPACITY {
        let view = lobby.client_view(&format!("p{n}"), &id).unwrap();
        let local = &view.actors()[0];
        let mut slots = std::collections::HashSet::new();
        for actor in view.actors() {
            assert!(slots.insert(actor.local_rid.0));
            assert_eq!(view.to_actor(&id, actor.local_rid), Ok(actor.actor_id));
            if actor.is_local {
                assert_eq!(actor.local_rid.0, 1);
            } else if actor.camp == local.camp {
                assert!((12..=20).contains(&actor.local_rid.0));
            } else {
                assert!((2..=11).contains(&actor.local_rid.0));
            }
        }
        assert_eq!(slots.len(), CAPACITY);
        let snapshot = lobby.loaded(&format!("p{n}"), &id, n as u64).unwrap();
        assert_eq!(
            snapshot.phase,
            if n == CAPACITY - 1 {
                t7_game_server::room::Phase::Countdown
            } else {
                t7_game_server::room::Phase::Loading
            }
        );
    }
}
