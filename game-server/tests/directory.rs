use t7_game_server::{
    lobby::{DEFAULT_CHANNEL_ID, Lobby, MAX_PAGE_SIZE},
    maps::MapCatalog,
    protocol::{ClientRequest, ErrorCode},
    room::{MAP_ID, Phase, RoomGuid},
};

#[test]
fn configured_maps_keep_the_selection_through_directory_and_member_changes() {
    let maps = MapCatalog::from_json(br#"{"mapIds":[1028,7001,7002]}"#).unwrap();
    let mut lobby = Lobby::with_maps(maps);
    for map_id in [7001, 7002] {
        let owner = format!("owner-{map_id}");
        let created = lobby.create_custom(&owner, "room", map_id, 1, "").unwrap();
        let listed = lobby.listing(1, created.address.room_number).unwrap();
        assert_eq!(listed.map_id, map_id);
        assert_eq!(
            lobby.page(1, 0, 20).unwrap().rooms.last().unwrap().map_id,
            map_id
        );
        let joined = lobby
            .join_custom("peer", created.address.room_guid, "")
            .unwrap();
        assert_eq!(joined.map_id, map_id);
        assert_eq!(lobby.ready("peer", true).unwrap().map_id, map_id);
        assert_eq!(lobby.ready(&owner, true).unwrap().map_id, map_id);
        let before = lobby.snapshot(&created.room_id).unwrap();
        assert_eq!(lobby.start(&owner, 0), Err(ErrorCode::UnsupportedMap));
        assert_eq!(lobby.snapshot(&created.room_id).unwrap(), before);
        lobby.leave("peer", false).unwrap();
        assert_eq!(lobby.snapshot(&created.room_id).unwrap().map_id, map_id);
    }
    assert_eq!(
        lobby.create("other", "room", 7003),
        Err(ErrorCode::UnsupportedMap)
    );
    assert_eq!(lobby.list().len(), 2);
    assert_eq!(
        Lobby::default().create("owner", "room", 7001),
        Err(ErrorCode::UnsupportedMap)
    );
}

#[test]
fn custom_and_development_operations_share_one_directory_and_identity() {
    let mut lobby = Lobby::default();
    let a = lobby.create("player-a", "first", MAP_ID).unwrap();
    let b = lobby
        .create_custom("player-b", "second", MAP_ID, DEFAULT_CHANNEL_ID, "")
        .unwrap();
    assert_ne!(a.room_id, b.room_id);
    assert_ne!(a.address.room_guid, b.address.room_guid);
    let page = lobby.page(DEFAULT_CHANNEL_ID, 0, MAX_PAGE_SIZE).unwrap();
    assert_eq!(page.total, 2);
    assert_eq!(page.rooms[0].room_id, a.room_id);
    assert_eq!(page.rooms[1].room_id, b.room_id);
    assert_eq!(page.rooms[0].address, a.address);
    assert_eq!(
        lobby
            .listing(DEFAULT_CHANNEL_ID, a.address.room_number)
            .unwrap(),
        page.rooms[0]
    );
    assert_eq!(
        lobby.listing(2, a.address.room_number),
        Err(ErrorCode::UnsupportedChannel)
    );
    assert_eq!(
        lobby.listing(DEFAULT_CHANNEL_ID, 0),
        Err(ErrorCode::RoomNotFound)
    );
    assert!(page.rooms.iter().all(|room| room.joinable));
    let joined = lobby
        .join_custom("player-c", a.address.room_guid, "")
        .unwrap();
    assert_eq!(joined.room_id, a.room_id);
    assert_eq!(joined.address, a.address);
    assert_eq!(joined.phase, Phase::Waiting);
    assert!(joined.current_match.is_none());
    assert_eq!(lobby.list().len(), 2);
    let full = lobby.page(DEFAULT_CHANNEL_ID, 0, 1).unwrap();
    assert_eq!(full.rooms[0].member_count, 2);
    assert!(full.rooms[0].joinable);
    let further = lobby
        .join_custom("player-d", a.address.room_guid, "")
        .unwrap();
    assert_eq!(further.members.len(), 3);
    assert_eq!(further.address, joined.address);
}

#[test]
fn page_order_and_totals_are_consistent_for_empty_and_partial_pages() {
    let mut lobby = Lobby::default();
    assert!(
        lobby
            .page(DEFAULT_CHANNEL_ID, 0, 1)
            .unwrap()
            .rooms
            .is_empty()
    );
    let created: Vec<_> = (0..23)
        .map(|i| {
            lobby
                .create(&format!("player-{i}"), "room", MAP_ID)
                .unwrap()
        })
        .collect();
    let first = lobby.page(DEFAULT_CHANNEL_ID, 0, MAX_PAGE_SIZE).unwrap();
    let last = lobby.page(DEFAULT_CHANNEL_ID, 20, MAX_PAGE_SIZE).unwrap();
    assert_eq!(
        (first.total, first.rooms.len(), last.total, last.rooms.len()),
        (23, 20, 23, 3)
    );
    let ids: Vec<_> = first
        .rooms
        .iter()
        .chain(&last.rooms)
        .map(|room| &room.room_id)
        .collect();
    assert_eq!(
        ids,
        created.iter().map(|room| &room.room_id).collect::<Vec<_>>()
    );
    let beyond = lobby.page(DEFAULT_CHANNEL_ID, u32::MAX, 1).unwrap();
    assert_eq!(beyond.total, 23);
    assert!(beyond.rooms.is_empty());
    lobby.leave("player-0", false).unwrap();
    let refreshed = lobby.page(DEFAULT_CHANNEL_ID, 0, MAX_PAGE_SIZE).unwrap();
    assert_eq!(refreshed.total, 22);
    assert_eq!(refreshed.rooms[0].room_id, created[1].room_id);
}

#[test]
fn identity_survives_owner_transfer_and_phases_but_empty_room_keys_expire() {
    let mut lobby = Lobby::default();
    let original = lobby.create("player-a", "room", MAP_ID).unwrap();
    let guid = original.address.room_guid;
    lobby.join_custom("player-b", guid, "").unwrap();
    lobby.ready("player-a", true).unwrap();
    lobby.ready("player-b", true).unwrap();
    let loading = lobby.start("player-a", 0).unwrap();
    assert_eq!(loading.address, original.address);
    let page = lobby.page(DEFAULT_CHANNEL_ID, 0, 1).unwrap();
    assert_eq!(page.rooms[0].phase, Phase::Loading);
    assert!(!page.rooms[0].joinable);
    assert_eq!(
        lobby.join_custom("player-c", guid, ""),
        Err(ErrorCode::WrongPhase)
    );
    assert_eq!(lobby.snapshot(&original.room_id).unwrap(), loading);
    lobby.leave("player-a", true).unwrap();
    let inherited = lobby.snapshot(&original.room_id).unwrap();
    assert_eq!(inherited.address, original.address);
    assert_eq!(inherited.owner_account_id, "player-b");
    lobby.join_custom("player-c", guid, "").unwrap();
    lobby.leave("player-b", false).unwrap();
    lobby.leave("player-c", false).unwrap();
    assert_eq!(
        lobby.join_custom("player-a", guid, ""),
        Err(ErrorCode::RoomNotFound)
    );
    let replacement = lobby.create("player-a", "new", MAP_ID).unwrap();
    assert_eq!(
        lobby.listing(DEFAULT_CHANNEL_ID, original.address.room_number),
        Err(ErrorCode::RoomNotFound)
    );
    assert!(replacement.address.room_number > original.address.room_number);
    assert_ne!(replacement.address.room_guid, guid);
    assert_eq!(
        lobby.join_custom("player-b", guid, ""),
        Err(ErrorCode::RoomNotFound)
    );
}

#[test]
fn unsupported_options_and_invalid_pages_do_not_mutate_or_reserve_membership() {
    let mut lobby = Lobby::default();
    assert_eq!(lobby.page(0, 0, 1), Err(ErrorCode::UnsupportedChannel));
    for limit in [0, MAX_PAGE_SIZE + 1] {
        assert_eq!(
            lobby.page(DEFAULT_CHANNEL_ID, 0, limit),
            Err(ErrorCode::InvalidRoomPage)
        );
    }
    assert_eq!(
        lobby.create_custom("player-a", "room", MAP_ID, 2, ""),
        Err(ErrorCode::UnsupportedChannel)
    );
    assert_eq!(
        lobby.create_custom("player-a", "room", MAP_ID, DEFAULT_CHANNEL_ID, "secret"),
        Err(ErrorCode::PasswordUnsupported)
    );
    assert!(lobby.list().is_empty());
    let room = lobby.create("player-a", "room", MAP_ID).unwrap();
    assert_eq!(room.address.room_number, 1);
    assert_eq!(
        lobby.join_custom("player-b", room.address.room_guid, "secret"),
        Err(ErrorCode::PasswordUnsupported)
    );
    assert_eq!(lobby.snapshot(&room.room_id).unwrap(), room);
    assert!(
        lobby
            .join_custom("player-b", room.address.room_guid, "")
            .is_ok()
    );
}

#[test]
fn guid_round_trips_all_u64_bits_and_rejects_ambiguous_input() {
    let guid: RoomGuid = serde_json::from_str("\"18446744073709551615\"").unwrap();
    assert_eq!(guid.value(), u64::MAX);
    assert_eq!(
        serde_json::to_string(&guid).unwrap(),
        "\"18446744073709551615\""
    );
    for input in ["0", "01", "-1", "+1", "1.0", "18446744073709551616", ""] {
        assert!(serde_json::from_str::<RoomGuid>(&format!("\"{input}\"")).is_err());
    }
    assert!(serde_json::from_str::<RoomGuid>("1").is_err());
    let request = r#"{"protocolVersion":1,"requestId":"1","command":{"type":"joinCustomRoom","payload":{"roomGuid":"1"}}}"#;
    assert!(serde_json::from_str::<ClientRequest>(request).is_ok());
    assert!(
        serde_json::from_str::<ClientRequest>(
            &request.replace("\"roomGuid\":\"1\"", "\"roomGuid\":1")
        )
        .is_err()
    );
    assert!(
        serde_json::from_str::<ClientRequest>(&request.replace(
            "\"roomGuid\":\"1\"",
            "\"roomGuid\":\"1\",\"accountId\":\"other\""
        ))
        .is_err()
    );
}

#[test]
fn configured_room_capacity_is_enforced_without_changing_client_slot_limits() {
    use t7_game_server::{limits::RoomRules, maps::MapCatalog};
    assert!(RoomRules::new(0).is_err());
    assert!(RoomRules::new(21).is_err());
    let mut lobby = Lobby::with_rules(MapCatalog::default(), RoomRules::new(4).unwrap());
    let room = lobby.create("p0", "small", 1003).unwrap();
    assert_eq!(room.capacity, 4);
    for n in 1..4 {
        lobby.join(&format!("p{n}"), &room.room_id).unwrap();
    }
    assert_eq!(lobby.join("extra", &room.room_id), Err(ErrorCode::RoomFull));
    assert_eq!(lobby.set_camp("p1", 1), Err(ErrorCode::InvalidTeams));
}
