use super::*;
use crate::battle_session::SquadMember;

fn bytes(value: &str) -> Vec<u8> {
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
        .collect()
}

fn roster() -> SquadRoster {
    SquadRoster {
        entries: vec![Squad {
            id: 1,
            camp: 1,
            leader: 1,
            joinable: true,
            members: vec![SquadMember {
                actor_id: 1,
                joined_at_s: 12,
            }],
        }],
    }
}

#[test]
fn squad_requests_and_responses_match_native_tdr_layout() {
    for (body, command, reply) in [
        ("000100", SquadCommand::Create, "000200000000"),
        ("000700000001", SquadCommand::Join(1), "000800000000"),
        ("000a00", SquadCommand::Leave, "000b00000000"),
        ("000400", SquadCommand::Dismiss, "000500000000"),
        (
            "001000000002",
            SquadCommand::SetJoinable(false),
            "001100000000",
        ),
    ] {
        assert_eq!(decode(COMMAND, &bytes(body)).unwrap(), Some(command));
        assert_eq!(response(command, 0).body, bytes(reply));
        assert!(decode(COMMAND, &bytes(body)[..bytes(body).len() - 1]).is_err());
        let mut extra = bytes(body);
        extra.push(0);
        assert!(decode(COMMAND, &extra).is_err());
    }
    assert_eq!(decode(54, &[0, 1, 0]).unwrap(), None);
    assert!(decode(COMMAND, &bytes("001000000003")).is_err());
}

#[test]
fn squad_notifications_bind_actor_ids_to_client_rids() {
    let rids = BTreeMap::from([(1, 101), (2, 202)]);
    let first = roster();
    let created = changes(&SquadRoster::default(), &first, &rids).unwrap();
    assert_eq!(
        created[0].body,
        bytes("00030000000100000001000000010000000000000065")
    );
    assert_eq!(created.len(), 1);
    let mut joined = first.clone();
    joined.entries[0].members.push(SquadMember {
        actor_id: 2,
        joined_at_s: 15,
    });
    assert_eq!(
        changes(&first, &joined, &rids).unwrap()[0].body,
        bytes("0009000000010000000100000000000000ca0000000f")
    );
    let mut left = joined.clone();
    left.entries[0].members.remove(0);
    left.entries[0].leader = 2;
    let messages = changes(&joined, &left, &rids).unwrap();
    assert_eq!(
        messages[0].body,
        bytes("000c00000001000000010000000000000065")
    );
    assert_eq!(
        messages[1].body,
        bytes("000f000000010000000100000000000000ca")
    );
    assert_eq!(
        changes(&left, &SquadRoster::default(), &rids).unwrap()[0].body,
        bytes("00060000000100000001")
    );
    assert!(changes(&first, &joined, &BTreeMap::from([(1, 101)])).is_err());
    assert!(changes(&joined, &joined, &rids).unwrap().is_empty());
}

#[test]
fn squad_instance_refresh_serializes_red_then_blue_and_member_counts() {
    let original = super::super::scene_wire::instance_start(5000, 1028, 101).unwrap()[1].clone();
    let update = enable(original.clone(), &roster(), &BTreeMap::from([(1, 101)])).unwrap();
    assert_eq!(&update.body[..61], &original.body[..61]);
    assert_eq!(
        &update.body[61..],
        bytes("01002800000028000100000001000000010000000000000065000100000000000000650000000c")
    );
    assert!(enable(update, &roster(), &BTreeMap::from([(1, 101)])).is_err());
}

#[test]
fn squad_spawn_decodes_original_member_rid_and_three_float_coordinates() {
    let request = bytes("0064000300000000000000ca40c0000041a0000000000000");
    assert_eq!(
        decode_spawn(&request, &BTreeMap::from([(3, 202)])).unwrap(),
        SpawnRequest::Squad {
            target_id: 3,
            position: [6., 20., 0.]
        }
    );
    assert_eq!(
        decode_spawn(&request, &BTreeMap::new()).unwrap(),
        SpawnRequest::Squad {
            target_id: 0,
            position: [6., 20., 0.]
        }
    );
    assert!(decode_spawn(&request[..23], &BTreeMap::new()).is_err());
    assert_eq!(
        decode_spawn(&bytes("0064000100"), &BTreeMap::new()).unwrap(),
        SpawnRequest::System
    );
}
