use super::*;
use crate::gateway::scene_wire;
use t7r_battle_core::{action::EquipmentItem, battle_semantics::state::MatchState};

fn fixture() -> (SceneRefresh, Identity) {
    let member = |id, rid, camp| LocalSceneActor {
        actor_id: id,
        client_rid: rid,
        camp,
        hero_id: 1101,
        max_health: 100,
        position: [0.; 3],
        heading: 0.,
        equipment: vec![EquipmentItem {
            slot: 1,
            weapon_id: 701,
        }],
        weapon_id: 701,
        offhand_id: 0,
    };
    (
        SceneRefresh::new(
            SceneRoster {
                local: member(1, 101, 1),
                bots: vec![member(2, 202, 2)],
                bot_names: vec!["Fixture0".into()],
                kill_target: 10,
                guard_max: 5.,
                local_shields: vec![],
            },
            5000,
        ),
        Identity {
            name: b"Player".to_vec(),
            portrait: 7,
            level: 100,
        },
    )
}

fn state(id: u32) -> StateActor {
    StateActor {
        actor_id: id,
        camp: id as u8,
        kind: id as u8,
        life: 1,
        intent: 0,
        phase: 0,
        flags: 0,
        sequence: 7,
        duration_ms: 0,
        hero_id: 1101,
        health: 100,
        guard: 5.,
        score: [0; 4],
        weapon_id: 701,
        offhand_id: 0,
        main_weapon_id: 701,
        shield_count: 0,
        shields: [(0, 0); 4],
        action_changed_at_ms: 1800,
        life_at_ms: 0,
        respawn_at_ms: 0,
        spawn_position: [0.; 3],
        spawn_heading: 0.,
    }
}

fn golden(name: &str) -> Vec<u8> {
    let row =
        include_str!("../../../../../../tests/fixtures/protocol/instance-refresh-wire-v1.txt")
            .lines()
            .find(|line| line.starts_with(&format!("{name} ")))
            .unwrap();
    row.split_whitespace()
        .nth(1)
        .unwrap()
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
        .collect()
}

fn request(rids: &[u64]) -> Vec<u8> {
    let mut body = 7_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&(rids.len() as i32).to_be_bytes());
    for rid in rids {
        body.extend_from_slice(&rid.to_be_bytes());
    }
    body.push(0);
    body
}

#[test]
fn health_refresh_uses_selected_maximum_before_and_after_state_updates() {
    let (mut refresh, identity) = fixture();
    refresh.roster.local.max_health = 150;
    let initial = refresh
        .objects(&request(&[101]), &identity, 5000, false, false)
        .unwrap();
    assert_eq!(&initial[0].body[230..238], &[0, 0, 0, 150, 0, 0, 0, 150]);
    let mut selected = refresh.roster.local.clone();
    selected.hero_id = 2102;
    selected.max_health = 240;
    let mut actor = state(1);
    actor.hero_id = 2102;
    actor.health = 111;
    refresh.apply(RefreshUpdate {
        local: Some(selected),
        actors: vec![actor],
        ..Default::default()
    });
    let wounded = refresh
        .objects(&request(&[101]), &identity, 5000, false, false)
        .unwrap();
    assert_eq!(&wounded[0].body[230..238], &[0, 0, 0, 111, 0, 0, 0, 240]);
}

#[test]
fn refresh_preserves_game_start_and_merges_absolute_camp_scores() {
    let (mut refresh, _) = fixture();
    let mut blue = state(1);
    blue.score[0] = 2;
    let mut red = state(2);
    red.score[0] = 3;
    for actor in [blue, red] {
        refresh.apply(RefreshUpdate {
            actors: vec![actor],
            ..Default::default()
        });
    }
    assert_eq!(refresh.camp_score(9000).unwrap().body, golden("camp-score"));
    assert_eq!(
        scene_wire::refresh_game(9000, 6000, 120000).unwrap().body,
        golden("round-game")
    );
    let later = scene_wire::refresh_game(9500, 6000, 120000).unwrap();
    assert_eq!(&later.body[22..30], &6000_u64.to_be_bytes());
    assert!(scene_wire::refresh_game(5999, 6000, 120000).is_err());
    assert!(scene_wire::refresh_game(9000, 0, 120000).is_err());
}

#[test]
fn vision_refresh_restores_health_pose_loadout_and_bot_animation_without_restarting_it() {
    let (mut refresh, identity) = fixture();
    let mut local = state(1);
    local.hero_id = 2102;
    local.health = 37;
    local.weapon_id = 100000;
    let mut bot = state(2);
    bot.life = 2;
    bot.health = 0;
    bot.life_at_ms = 2000;
    bot.respawn_at_ms = 10000;
    let outputs = [Output::State(MatchState {
        mode: 2,
        epoch: 7,
        revision: 1,
        base_revision: 0,
        at_ms: 2000,
        actors: vec![local, bot],
    })];
    let context = OutputContext {
        at_ms: 2000,
        actor_poses: BTreeMap::from([
            (
                1,
                SampledPose {
                    position: [5., 6., 0.],
                    heading: 45.,
                    sampled_at_ms: 2000,
                },
            ),
            (
                2,
                SampledPose {
                    position: [8., 9., 0.],
                    heading: -90.,
                    sampled_at_ms: 2000,
                },
            ),
        ]),
    };
    refresh.apply(RefreshUpdate::project(
        &outputs,
        &context,
        None,
        Some(-1000),
        Some(5000),
    ));
    refresh.record_message(&ClientBattleMessage {
        command: 4,
        body: golden("bot-action"),
    });
    let result = refresh
        .objects(&request(&[101, 202]), &identity, 9000, false, true)
        .unwrap();
    assert_eq!(result.len(), 5);
    for (actual, name) in
        result
            .iter()
            .zip(["local-vision", "bot-vision", "bot-action", "bot-dead"])
    {
        assert_eq!(actual.body, golden(name), "{name}");
    }
    assert_eq!(result[4], scene_entry_wire::move_active(3000, true));
    let hidden = refresh
        .objects(&request(&[101, 202]), &identity, 9100, true, false)
        .unwrap();
    assert_eq!(hidden[0].body, golden("bot-vision"));
    assert_eq!(hidden[1].body, golden("bot-action"));
    assert_eq!(
        hidden.last().unwrap(),
        &scene_entry_wire::move_active(3100, false)
    );
    assert!(
        refresh
            .objects(&request(&[101, 999]), &identity, 9000, false, true)
            .is_err()
    );
    assert!(
        refresh
            .objects(&request(&[]), &identity, 9000, false, true)
            .unwrap()
            .is_empty()
    );
}

#[test]
fn movement_refresh_uses_instance_clock_before_native_samples() {
    let (refresh, identity) = fixture();
    for (now_ms, active) in [(5000, false), (9000, false), (15000, true)] {
        let messages = refresh
            .objects(&request(&[101]), &identity, now_ms, false, active)
            .unwrap();
        assert_eq!(messages.len(), 2);
        assert_eq!(messages[0].command, 14);
        assert_eq!(
            messages[1],
            scene_entry_wire::move_active((now_ms - 5000) as u32, active)
        );
    }
    assert!(
        refresh
            .objects(&request(&[101]), &identity, 4999, false, false)
            .is_err()
    );
}

#[test]
fn respawn_floor_blocks_previous_life_pose_and_new_connection_starts_empty() {
    let (mut refresh, _) = fixture();
    let mut actor = state(1);
    actor.life = 5;
    actor.life_at_ms = 2000;
    actor.spawn_position = [5., 6., 0.];
    let old_pose = SampledPose {
        position: [80., 90., 0.],
        heading: 0.,
        sampled_at_ms: 1999,
    };
    refresh.apply(RefreshUpdate {
        actors: vec![actor],
        poses: vec![(1, old_pose)],
        ..Default::default()
    });
    assert_eq!(refresh.roster.local.position, [5., 6., 0.]);
    actor.life = 1;
    actor.life_at_ms = 0;
    refresh.apply(RefreshUpdate {
        actors: vec![actor],
        poses: vec![(1, old_pose)],
        ..Default::default()
    });
    assert_eq!(refresh.roster.local.position, [5., 6., 0.]);
    refresh.apply(RefreshUpdate {
        poses: vec![(
            1,
            SampledPose {
                sampled_at_ms: 2001,
                ..old_pose
            },
        )],
        ..Default::default()
    });
    assert_eq!(refresh.roster.local.position, old_pose.position);
    let (new, _) = fixture();
    assert!(new.actors.is_empty());
    assert!(new.animations.is_empty());
    assert_eq!(new.client_origin_ms, Some(5000));
    assert_eq!(new.roster.local.position, [0.; 3]);
}
