use super::{
    instance::InstanceIngress,
    platform::{PlatformMailbox, profile_request},
    room,
};
use serde_json::json;
use std::{
    collections::BTreeSet,
    io,
    sync::{Arc, Mutex},
};
use t7r_battle_resources::match_maps::{MAP_IDS, TEAM_MAP_IDS};
use tokio::sync::watch;

// The native pool also sends these two entries, which have no checkbox in the dialog.
const HIDDEN_MAP_IDS: [u32; 2] = [1013, 1018];

fn known_map(id: u32) -> bool {
    MAP_IDS.contains(&id) || HIDDEN_MAP_IDS.contains(&id)
}

fn invalid() -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, "invalid match map selection")
}

/// Original PROTO_MATCH_PATTERN: int16 type, five int32 fields including map count,
/// followed by checked IDs, unchecked IDs and auto-join flag. The native client
/// partitions the display pool into two disjoint arrays; it does not send the
/// full pool followed by exclusions.
pub(super) fn candidates(body: &[u8]) -> io::Result<Vec<u32>> {
    if body.len() < 37 || body.len() > 2100 || body[..2] != [0, 1] {
        return Err(invalid());
    }
    let mut cursor = 24;
    let mut read = || -> io::Result<u32> {
        let bytes = body.get(cursor..cursor + 4).ok_or_else(invalid)?;
        cursor += 4;
        Ok(u32::from_be_bytes(bytes.try_into().expect("four bytes")))
    };
    let count = read()?;
    if !(1..=256).contains(&count) {
        return Err(invalid());
    }
    let mut included = BTreeSet::new();
    for _ in 0..count {
        let id = read()?;
        if !included.insert(id) || (!known_map(id) && id != 1028) {
            return Err(invalid());
        }
    }
    let excluded_count = read()?;
    if excluded_count > 256 {
        return Err(invalid());
    }
    let mut excluded = BTreeSet::new();
    for _ in 0..excluded_count {
        let id = read()?;
        if included.contains(&id) || !known_map(id) || !excluded.insert(id) {
            return Err(invalid());
        }
    }
    let auto_join = read()?;
    if auto_join > 1 || cursor + 1 != body.len() || body[cursor] > 1 {
        return Err(invalid());
    }
    included.retain(|id| TEAM_MAP_IDS.contains(id) || *id == 1028);
    if included.is_empty() {
        return Err(invalid());
    }
    Ok(included.into_iter().collect())
}

pub(super) async fn prepare(
    body: &[u8],
    mailbox: Arc<Mutex<PlatformMailbox>>,
    instances: &watch::Receiver<Option<Arc<InstanceIngress>>>,
) -> io::Result<Option<Vec<u8>>> {
    let rejected = || room::match_start_response(body, 630001).map(Some);
    let selected = match candidates(body) {
        Ok(value) => value,
        Err(_) => return rejected(),
    };
    let current = instances.borrow().clone().ok_or_else(invalid)?;
    if !current.preparation_open() {
        return Err(invalid());
    }
    if selected.len() == 1 && selected[0] == current.map_id() {
        return Ok(None);
    }
    let response = profile_request(mailbox, "prepareMatch", json!({"mapIds": selected})).await?;
    if response["success"] != true {
        return rejected();
    }
    let map_id = response["mapId"]
        .as_u64()
        .and_then(|value| u32::try_from(value).ok())
        .ok_or_else(invalid)?;
    let next = instances.borrow().clone().ok_or_else(invalid)?;
    if !selected.contains(&map_id) || next.map_id() != map_id || !next.preparation_open() {
        return Err(invalid());
    }
    Ok(None)
}

pub(super) async fn prepare_training(
    body: &[u8],
    mailbox: Arc<Mutex<PlatformMailbox>>,
    instances: &watch::Receiver<Option<Arc<InstanceIngress>>>,
) -> io::Result<()> {
    let (_, requested) = room::decode_create(body)?;
    let map_id = u32::try_from(requested).map_err(|_| invalid())?;
    if !t7r_battle_resources::training_maps::contains(map_id) {
        return Ok(());
    }
    let current = instances.borrow().clone().ok_or_else(invalid)?;
    if !current.preparation_open() {
        return Err(invalid());
    }
    if current.map_id() == map_id {
        return Ok(());
    }
    let response = profile_request(mailbox, "prepareMatch", json!({"mapIds": [map_id]})).await?;
    let next = instances.borrow().clone().ok_or_else(invalid)?;
    if response["success"] != true
        || response["mapId"] != map_id
        || next.map_id() != map_id
        || !next.preparation_open()
    {
        return Err(invalid());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_team_maps_are_eligible() {
        assert!(candidates(&request(&[20015, 90003], &[])).is_err());
        assert_eq!(
            candidates(&request(&[1003, 20015, 90003], &[])).unwrap(),
            vec![1003]
        );
        assert_eq!(
            candidates(&request(&[1003, 1030, 1021, 1001, 6003, 1034], &[])).unwrap(),
            vec![1001, 1003, 1021, 1030, 1034, 6003]
        );
    }

    #[test]
    fn native_default_selection_only_matches_open_team_maps() {
        let fixture = include_str!("../../../../../tests/fixtures/protocol/match-maps-v1.txt");
        let hex = fixture
            .lines()
            .find(|line| line.starts_with("all "))
            .unwrap()
            .split_whitespace()
            .nth(1)
            .unwrap();
        let body: Vec<u8> = (0..hex.len())
            .step_by(2)
            .map(|index| u8::from_str_radix(&hex[index..index + 2], 16).unwrap())
            .collect();
        assert_eq!(u32::from_be_bytes(body[24..28].try_into().unwrap()), 14);
        let selected = candidates(&body).unwrap();
        assert_eq!(selected.len(), 6);
        assert!(selected.iter().all(|id| TEAM_MAP_IDS.contains(id)));
    }
    fn request(maps: &[u32], excluded: &[u32]) -> Vec<u8> {
        let mut body = vec![0; 24];
        body[1] = 1;
        for values in [maps, excluded] {
            body.extend_from_slice(&(values.len() as u32).to_be_bytes());
            for id in values {
                body.extend_from_slice(&id.to_be_bytes());
            }
        }
        body.extend_from_slice(&[0; 5]);
        body
    }
    #[test]
    fn selected_maps_follow_the_original_disjoint_checked_and_unchecked_arrays() {
        assert_eq!(candidates(&request(&[1003], &[])).unwrap(), vec![1003]);
        assert_eq!(
            candidates(&request(&[1003, 1030], &[1021])).unwrap(),
            vec![1003, 1030]
        );
        assert!(candidates(&request(&[1013, 90003, 1018], &[1003])).is_err());
        assert_eq!(
            candidates(&request(&[1003], &HIDDEN_MAP_IDS)).unwrap(),
            vec![1003]
        );
        for body in [
            request(&[], &[]),
            request(&HIDDEN_MAP_IDS, &MAP_IDS),
            request(&[1003, 1013, 1013], &[]),
            request(&[1003, 1018], &[1018]),
            request(&[1003], &[1013, 1013]),
            request(&[1033], &[]),
            request(&[1003], &[1003]),
            request(&[1003, 1003], &[]),
            request(&[9999], &[]),
            request(&[1003], &[9999]),
            request(&[1003], &[1021, 1021]),
        ] {
            assert!(candidates(&body).is_err());
        }
    }
    #[test]
    fn truncated_oversized_and_trailing_arrays_are_rejected() {
        let body = request(&MAP_IDS, &[]);
        for end in 0..body.len() {
            assert!(candidates(&body[..end]).is_err());
        }
        let mut trailing = body.clone();
        trailing.push(0);
        assert!(candidates(&trailing).is_err());
        let mut oversized = body;
        oversized[24..28].copy_from_slice(&257_u32.to_be_bytes());
        assert!(candidates(&oversized).is_err());
    }
}
