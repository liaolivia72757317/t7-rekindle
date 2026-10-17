use std::{collections::BTreeMap, io, sync::Arc};
use t7r_battle_core::battle_semantics::{PlayerIntent, SourceStamp};
use t7r_battle_resources::ResourceBundle;
use t7r_battle_runtime::{
    battle_resources::BattleResources,
    battle_session::{BattleSession, Config, EquipmentItem, Member, MemberKind},
    semantic_adapter::{SourceBinding, player_input},
};

fn fixture() -> (BattleSession, SourceBinding) {
    let bundle = ResourceBundle::from_slice(include_bytes!(
        "../../../../tests/fixtures/resources/bundle-v1.json"
    ))
    .unwrap();
    let session = BattleSession::new(
        &bundle,
        Arc::new(BattleResources::prepare(&bundle).unwrap()),
        Config {
            epoch: 7,
            started_at_ms: 1000,
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
    .unwrap();
    (
        session,
        SourceBinding {
            epoch: 7,
            source_id: 9,
            actor_rids: BTreeMap::from([(1, 1)]),
        },
    )
}

fn input(
    binding: &SourceBinding,
    batch_id: u64,
    at_ms: u64,
) -> t7r_battle_runtime::battle_session::Input {
    let payload = PlayerIntent {
        stamp: SourceStamp {
            epoch: 7,
            source_id: 9,
            batch_id,
            at_ms,
        },
        actor_id: 1,
        client_rid: 1,
        command: 1,
        flags: 0,
    }
    .encode()
    .unwrap();
    player_input(&payload, binding, 0).unwrap().input
}

#[test]
fn owner_rejects_before_start_future_and_regressing_player_time() {
    let (mut session, binding) = fixture();
    let before = session.actor(1).unwrap();
    for at_ms in [0, 999, 1101, u64::MAX] {
        assert!(
            session
                .dispatch(7, 1100, input(&binding, 1, at_ms), |_| Ok(()))
                .is_err(),
            "accepted invalid player timestamp {at_ms}"
        );
        assert_eq!(session.actor(1).unwrap(), before);
    }
    session
        .dispatch(7, 1100, input(&binding, 1, 1090), |_| Ok(()))
        .unwrap();
    assert_eq!(session.actor(1).unwrap().action.changed_at_ms, 1100);
    assert!(
        session
            .dispatch(7, 1101, input(&binding, 2, 1089), |_| Ok(()))
            .is_err()
    );
    session
        .dispatch(7, 1101, input(&binding, 2, 1090), |_| Ok(()))
        .unwrap();
    assert!(
        session
            .dispatch(7, 1102, input(&binding, 2, 1091), |_| Ok(()))
            .is_err()
    );
    // No lateness window: a valid old command is processed at the owner's current time.
    session
        .dispatch(7, 9000, input(&binding, 3, 1090), |_| Ok(()))
        .unwrap();
}

#[test]
fn owner_checks_source_actor_and_match_without_consuming_the_cursor() {
    use t7r_battle_runtime::battle_session::Input;
    let (mut session, binding) = fixture();
    for (epoch, source_id, actor_id, batch_id) in
        [(6, 9, 1, 1), (7, 10, 1, 1), (7, 9, 2, 1), (7, 9, 1, 0)]
    {
        let Input::PlayerAction { command, .. } = input(&binding, 1, 1000) else {
            unreachable!()
        };
        let invalid = Input::PlayerAction {
            stamp: SourceStamp {
                epoch,
                source_id,
                batch_id,
                at_ms: 1000,
            },
            actor_id,
            command,
        };
        assert!(
            session
                .dispatch(7, 1100, invalid, |_| panic!("invalid input was published"))
                .is_err()
        );
    }
    session
        .dispatch(7, 1100, input(&binding, 1, 1000), |_| Ok(()))
        .unwrap();
}

#[test]
fn failed_publication_does_not_consume_player_batch_or_time() {
    let (mut session, binding) = fixture();
    let before = session.actor(1).unwrap();
    let error = session
        .dispatch(7, 1100, input(&binding, 1, 1100), |_| {
            Err(io::Error::new(io::ErrorKind::WouldBlock, "full output"))
        })
        .unwrap_err();
    assert_eq!(error.kind(), io::ErrorKind::WouldBlock);
    assert_eq!(session.actor(1).unwrap(), before);
    assert!(
        session
            .dispatch(6, 1100, input(&binding, 1, 1090), |_| Ok(()))
            .is_err()
    );
    session
        .dispatch(7, 1100, input(&binding, 1, 1090), |_| Ok(()))
        .unwrap();
}
