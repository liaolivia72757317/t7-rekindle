use super::{BATTLE_COMMAND, ClientBattleMessage, client_rid, wire_time};
use crate::battle_session::Output;
use std::{collections::BTreeMap, io};

pub(crate) fn encode_surface_hit_for_client_clock(
    output: &Output,
    server_time_offset_ms: i64,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<ClientBattleMessage> {
    let Output::SurfaceHit {
        attacker,
        weapon_id,
        evidence,
        ..
    } = output
    else {
        return Err(invalid("expected surface-hit output"));
    };
    if *attacker == 0
        || evidence.actor_id != *attacker
        || evidence.target_id != 0
        || !evidence.occurred_at_ms.is_finite()
        || evidence.occurred_at_ms < 0.
        || evidence.occurred_at_ms >= u64::MAX as f64
        || !(-1..=7).contains(&evidence.material)
        || [evidence.position, evidence.direction, evidence.normal]
            .into_iter()
            .flatten()
            .any(|value| !value.is_finite() || value.abs() >= 100000.)
    {
        return Err(invalid("inconsistent surface-hit evidence"));
    }
    let attacker_rid = u64::from(client_rid(actor_rids, *attacker)?);
    let weapon =
        i32::try_from(*weapon_id).map_err(|_| invalid("surface weapon exceeds wire range"))?;
    if weapon == 0 {
        return Err(invalid("surface weapon is absent"));
    }
    let at_ms = wire_time(
        evidence.occurred_at_ms.trunc() as u64,
        server_time_offset_ms,
    )?;
    let mut body = Vec::with_capacity(93);
    body.extend_from_slice(&27_u16.to_be_bytes());
    for coordinate in evidence.position {
        body.extend_from_slice(&(coordinate as f32).to_be_bytes());
    }
    body.push(0);
    for coordinate in evidence.direction {
        body.extend_from_slice(&(coordinate as f32).to_be_bytes());
    }
    body.extend_from_slice(&0_f32.to_be_bytes());
    for coordinate in evidence.normal {
        body.extend_from_slice(&(coordinate as f32).to_be_bytes());
    }
    body.extend_from_slice(&0_f32.to_be_bytes());
    body.extend_from_slice(&i32::from(evidence.material).to_be_bytes());
    body.extend_from_slice(&attacker_rid.to_be_bytes());
    body.extend_from_slice(&weapon.to_be_bytes());
    body.extend_from_slice(&0_u64.to_be_bytes());
    body.extend_from_slice(&0_i32.to_be_bytes());
    body.extend_from_slice(&0_u32.to_be_bytes());
    body.extend_from_slice(&at_ms.to_be_bytes());
    body.extend_from_slice(&[1, 1]);
    body.extend_from_slice(&0_i32.to_be_bytes());
    debug_assert_eq!(body.len(), 93);
    Ok(ClientBattleMessage {
        command: BATTLE_COMMAND,
        body,
    })
}

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::battle_session::contacts::ContactEvent;
    use t7r_battle_core::{action::Phase, damage::Region};

    fn triplet(value: &str) -> [f64; 3] {
        let parsed = value
            .split(',')
            .map(|part| part.parse::<f64>().unwrap())
            .collect::<Vec<_>>();
        parsed.try_into().unwrap()
    }

    fn hex(value: &str) -> Vec<u8> {
        value
            .as_bytes()
            .chunks_exact(2)
            .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
            .collect()
    }

    #[test]
    fn ground_and_terrain_feedback_match_original_wire_and_bound_rids() {
        let rids = BTreeMap::from([(1, 101), (2, 202)]);
        for line in
            include_str!("../../../../../tests/fixtures/protocol/surface-hit-wire-v1.txt").lines()
        {
            let row = line.split_whitespace().collect::<Vec<_>>();
            let actor_id = row[1].parse::<u32>().unwrap();
            let evidence = ContactEvent {
                actor_id,
                target_id: 0,
                source_entity: 100 + u64::from(actor_id),
                target_entity: 0,
                sequence: 7,
                phase_at_ms: 1000,
                target_intent: None,
                target_phase: Phase::Idle,
                target_phase_at_ms: 0.,
                target_observed_at_ms: None,
                source_observed_at_ms: None,
                occurred_at_ms: row[4].parse().unwrap(),
                along: 0.5,
                defence_weapon_id: 0,
                region: Region::Torso,
                body_index: -1,
                source_position: [0.; 3],
                target_position: [0.; 3],
                source_heading: 0.,
                target_heading: 0.,
                source_velocity: [0.; 3],
                target_velocity: [0.; 3],
                weapon_velocity: [0.; 3],
                velocity_ready: false,
                airborne: false,
                target_airborne: false,
                position: triplet(row[5]),
                direction: triplet(row[6]),
                normal: triplet(row[7]),
                material: row[8].parse().unwrap(),
            };
            let output = Output::SurfaceHit {
                attacker: actor_id,
                weapon_id: row[3].parse().unwrap(),
                ground: row[0] == "ground",
                evidence: Box::new(evidence),
            };
            let message = encode_surface_hit_for_client_clock(&output, 5000, &rids).unwrap();
            assert_eq!(message.command, 4);
            assert_eq!(message.body, hex(row[9]));
            let mut wrong = output.clone();
            if let Output::SurfaceHit { evidence, .. } = &mut wrong {
                evidence.target_id = 2;
            }
            assert!(encode_surface_hit_for_client_clock(&wrong, 5000, &rids).is_err());
        }
    }
}
