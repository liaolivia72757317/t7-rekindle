use t7_game_server::{lobby::Lobby, motion::Pose, protocol::ErrorCode, room::COUNTDOWN_MS};

fn running() -> (Lobby, String) {
    let mut lobby = Lobby::default();
    let room = lobby.create("owner", "room", 1003).unwrap();
    lobby.join("guest", &room.room_id).unwrap();
    lobby.ready("guest", true).unwrap();
    let id = lobby
        .start("owner", 0)
        .unwrap()
        .current_match
        .unwrap()
        .match_id;
    lobby.loaded("owner", &id, 0).unwrap();
    lobby.loaded("guest", &id, 0).unwrap();
    lobby.advance(COUNTDOWN_MS);
    (lobby, id)
}

fn pose(x: f32) -> Pose {
    Pose {
        position: [x, 1.0, 0.5],
        heading: 90,
        forward_back: -1,
        left_right: 0,
        running: false,
    }
}

#[test]
fn poses_are_coalesced_at_twenty_hertz_and_keep_server_actor_identity() {
    let (mut lobby, id) = running();
    lobby.pose_input("guest", &id, 1, pose(0.0), 3000).unwrap();
    lobby.pose_input("guest", &id, 2, pose(0.25), 3010).unwrap();
    lobby.advance(3010);
    let events = lobby.take_pose_events();
    assert_eq!(events.len(), 1);
    assert_eq!(
        (
            events[0].actor_id.0,
            events[0].input_seq,
            events[0].event_seq
        ),
        (2, 2, 1)
    );
    assert_eq!(events[0].pose.position[0], 0.25);
    lobby.pose_input("owner", &id, 1, pose(20.0), 3020).unwrap();
    lobby.advance(3020);
    assert!(lobby.take_pose_events().is_empty());
    lobby.advance(3060);
    assert_eq!(lobby.take_pose_events()[0].actor_id.0, 1);
    lobby.advance(9000);
    assert!(lobby.take_pose_events().is_empty());
}

#[test]
fn invalid_poses_cannot_advance_sequence_or_replace_the_last_accepted_pose() {
    let (mut lobby, id) = running();
    lobby.pose_input("owner", &id, 1, pose(0.0), 3000).unwrap();
    assert_eq!(
        lobby.pose_input("owner", &id, 2, pose(10.0), 3001),
        Err(ErrorCode::InvalidPose)
    );
    for bad in [
        pose(f32::NAN),
        pose(f32::INFINITY),
        pose(100_000.0),
        Pose {
            heading: 181,
            ..pose(0.1)
        },
        Pose {
            forward_back: 2,
            ..pose(0.1)
        },
        Pose {
            forward_back: 0,
            running: true,
            ..pose(0.1)
        },
    ] {
        assert_eq!(
            lobby.pose_input("owner", &id, 2, bad, 3050),
            Err(ErrorCode::InvalidPose)
        );
    }
    lobby.pose_input("owner", &id, 2, pose(0.5), 3050).unwrap();
    assert_eq!(
        lobby.pose_input("owner", &id, 2, pose(0.5), 3100),
        Err(ErrorCode::StaleInput)
    );
    lobby.advance(3050);
    assert_eq!(lobby.take_pose_events()[0].pose.position[0], 0.5);
    // A delayed native sample can cover more than the short burst allowance.
    lobby.pose_input("owner", &id, 3, pose(6.0), 4050).unwrap();
}

#[test]
fn pose_identity_match_phase_and_exit_are_checked_on_each_input() {
    let (mut lobby, id) = running();
    assert_eq!(
        lobby.pose_input("outsider", &id, 1, pose(0.0), 3000),
        Err(ErrorCode::NotInRoom)
    );
    assert_eq!(
        lobby.pose_input("owner", "old-match", 1, pose(0.0), 3000),
        Err(ErrorCode::StaleMatch)
    );
    lobby.pose_input("guest", &id, 1, pose(0.0), 3000).unwrap();
    lobby.leave("owner", false).unwrap();
    lobby.advance(3050);
    assert!(lobby.take_pose_events().is_empty());
    assert_eq!(
        lobby.pose_input("guest", &id, 2, pose(0.0), 3050),
        Err(ErrorCode::WrongPhase)
    );
}
