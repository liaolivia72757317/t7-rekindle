use super::{BATTLE_COMMAND, ClientBattleMessage, client_rid, wire_time};
use crate::battle_session::Output;
use std::{collections::BTreeMap, io};

pub(crate) fn encode_parry_for_client_clock(
    output: &Output,
    server_time_offset_ms: i64,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<Vec<ClientBattleMessage>> {
    let Output::Parry {
        attacker,
        target,
        weapon_id,
        defence_weapon_id,
        evidence,
    } = output
    else {
        return Err(invalid("expected parry output"));
    };
    if *attacker == 0
        || *target == 0
        || evidence.actor_id != *attacker
        || evidence.target_id != *target
        || evidence.defence_weapon_id != *defence_weapon_id
        || *defence_weapon_id == 0
        || !evidence.occurred_at_ms.is_finite()
        || evidence.occurred_at_ms < 0.
        || evidence.occurred_at_ms >= u64::MAX as f64
        || [evidence.position, evidence.direction, evidence.normal]
            .into_iter()
            .flatten()
            .any(|value| !value.is_finite() || value.abs() >= 100000.)
    {
        return Err(invalid("inconsistent parry evidence"));
    }
    let attacker_rid = u64::from(client_rid(actor_rids, *attacker)?);
    let target_rid = u64::from(client_rid(actor_rids, *target)?);
    let weapon =
        i32::try_from(*weapon_id).map_err(|_| invalid("attack weapon exceeds wire range"))?;
    let defence = i32::try_from(*defence_weapon_id)
        .map_err(|_| invalid("defence weapon exceeds wire range"))?;
    if weapon == 0 || defence == 0 {
        return Err(invalid("parry weapon is absent"));
    }
    let at_ms = wire_time(
        evidence.occurred_at_ms.trunc() as u64,
        server_time_offset_ms,
    )?;
    let mut hit = Vec::with_capacity(93);
    hit.extend_from_slice(&27_u16.to_be_bytes());
    for coordinate in evidence.position {
        hit.extend_from_slice(&(coordinate as f32).to_be_bytes());
    }
    hit.push(0);
    for coordinate in evidence.direction {
        hit.extend_from_slice(&(coordinate as f32).to_be_bytes());
    }
    hit.extend_from_slice(&0_f32.to_be_bytes());
    for coordinate in evidence.normal {
        hit.extend_from_slice(&(coordinate as f32).to_be_bytes());
    }
    hit.extend_from_slice(&0_f32.to_be_bytes());
    hit.extend_from_slice(&i32::from(evidence.material).to_be_bytes());
    hit.extend_from_slice(&attacker_rid.to_be_bytes());
    hit.extend_from_slice(&weapon.to_be_bytes());
    hit.extend_from_slice(&target_rid.to_be_bytes());
    hit.extend_from_slice(&defence.to_be_bytes());
    hit.extend_from_slice(&0_u32.to_be_bytes());
    hit.extend_from_slice(&at_ms.to_be_bytes());
    hit.extend_from_slice(&[1, 1]);
    hit.extend_from_slice(&0_i32.to_be_bytes());
    debug_assert_eq!(hit.len(), 93);

    let mut sound = Vec::with_capacity(35);
    sound.extend_from_slice(&0x28_u16.to_be_bytes());
    sound.extend_from_slice(&attacker_rid.to_be_bytes());
    sound.extend_from_slice(&target_rid.to_be_bytes());
    sound.push(2);
    sound.extend_from_slice(&weapon.to_be_bytes());
    sound.extend_from_slice(&defence.to_be_bytes());
    sound.extend_from_slice(&at_ms.to_be_bytes());
    debug_assert_eq!(sound.len(), 35);
    Ok(vec![
        ClientBattleMessage {
            command: BATTLE_COMMAND,
            body: hit,
        },
        ClientBattleMessage {
            command: BATTLE_COMMAND,
            body: sound,
        },
    ])
}

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::battle_session::contacts::ContactEvent;
    use t7r_battle_core::{
        action::{Direction, Intent, Phase},
        damage::Region,
    };

    fn hex(value: &str) -> Vec<u8> {
        value
            .as_bytes()
            .chunks_exact(2)
            .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
            .collect()
    }

    #[test]
    fn parry_feedback_matches_original_hit_and_sound_corpus() {
        let event = ContactEvent {
            actor_id: 1,
            target_id: 2,
            source_entity: 101,
            target_entity: 102,
            sequence: 37,
            phase_at_ms: 1000,
            target_intent: Some(Intent::Guard(Direction::Up)),
            target_phase: Phase::Hold,
            target_phase_at_ms: 1200.,
            target_observed_at_ms: None,
            source_observed_at_ms: None,
            occurred_at_ms: 1250.5,
            along: 0.5,
            defence_weapon_id: 703,
            region: Region::Torso,
            body_index: -1,
            source_position: [0.; 3],
            target_position: [0.; 3],
            source_heading: 0.,
            target_heading: 180.,
            source_velocity: [0.; 3],
            target_velocity: [0.; 3],
            weapon_velocity: [0.; 3],
            velocity_ready: false,
            airborne: false,
            target_airborne: false,
            position: [1.5, 20., 0.5],
            normal: [0., 0., 1.],
            direction: [1., 0., 0.],
            material: 0,
        };
        let output = Output::Parry {
            attacker: 1,
            target: 2,
            weapon_id: 701,
            defence_weapon_id: 703,
            evidence: Box::new(event),
        };
        let rids = BTreeMap::from([(1, 101), (2, 202)]);
        let messages = encode_parry_for_client_clock(&output, 5000, &rids).unwrap();
        let rows = include_str!("../../../../../tests/fixtures/protocol/battle-parry-wire-v1.txt")
            .lines()
            .map(|line| line.split_whitespace().collect::<Vec<_>>())
            .collect::<Vec<_>>();
        assert_eq!(messages.len(), rows.len());
        for (message, row) in messages.iter().zip(rows) {
            assert_eq!(message.command, row[1].parse::<u16>().unwrap());
            assert_eq!(message.body, hex(row[2]), "{}", row[0]);
        }
        assert!(encode_parry_for_client_clock(&output, -1251, &rids).is_err());
        assert!(encode_parry_for_client_clock(&output, 5000, &BTreeMap::new()).is_err());
    }
}
