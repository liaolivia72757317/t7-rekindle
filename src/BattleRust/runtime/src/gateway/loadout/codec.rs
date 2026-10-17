use super::profile::{Catalogue, Profile, invalid};
use serde_json::{Value, json};
use std::{collections::BTreeSet, io};

#[derive(Default)]
struct Wire(Vec<u8>);
impl Wire {
    fn byte(&mut self, value: u8) -> &mut Self {
        self.0.push(value);
        self
    }
    fn short(&mut self, value: i16) -> &mut Self {
        self.0.extend(value.to_be_bytes());
        self
    }
    fn int(&mut self, value: i32) -> &mut Self {
        self.0.extend(value.to_be_bytes());
        self
    }
    fn long(&mut self, value: u64) -> &mut Self {
        self.0.extend(value.to_be_bytes());
        self
    }
    fn zero(&mut self, count: usize) -> &mut Self {
        self.0.resize(self.0.len() + count, 0);
        self
    }
    fn bytes(&mut self, value: &[u8]) -> &mut Self {
        self.0.extend_from_slice(value);
        self
    }
    fn reference(&mut self, cat: &Catalogue, id: i32) -> &mut Self {
        self.long(id as u64).int(cat.position(id))
    }
    fn weapon(&mut self, id: i32) -> &mut Self {
        self.int(id).zero(101)
    }
    fn slot(&mut self, position: i32, id: i32, kind: i32) -> &mut Self {
        self.int(position)
            .byte(0)
            .long(id as u64)
            .int(1)
            .int(kind)
            .int(id)
            .byte(0)
            .long(0)
    }
}

pub(super) fn hero_card(profile: &Profile, cat: &Catalogue, id: i32) -> Vec<u8> {
    let hero = &cat.heroes[&id];
    let mut out = Wire::default();
    out.slot(cat.position(id), id, 1)
        .byte(if profile.formation.contains(&id) {
            2
        } else {
            1
        })
        .byte(1)
        .byte(0)
        .int(0)
        .short(100)
        .int(0)
        .int(0);
    for group in 0..8 {
        for index in 0..6 {
            out.byte(if hero.groups[group][index] != 0 { 1 } else { 2 })
                .weapon(hero.native[group][index]);
        }
    }
    let slots = &profile.weapons[&id];
    out.short(slots.len() as i16);
    for item in slots {
        out.byte(item.slot)
            .int(hero.group_ids[item.group])
            .int(item.index as i32);
    }
    out.zero(192).short(0).short(0).int(0).int(0).long(0);
    out.0
}
fn battle_hero(profile: &Profile, cat: &Catalogue, id: i32, position: i32) -> Vec<u8> {
    let slots = &profile.weapons[&id];
    let mut out = Wire::default();
    out.slot(position, id, 2).short(slots.len() as i16);
    for item in slots {
        out.byte(item.slot)
            .weapon(cat.heroes[&id].groups[item.group][item.index]);
    }
    out.zero(44).byte(1).byte(0).int(0).short(100);
    out.0
}
pub(super) fn containers(
    profile: &Profile,
    cat: &Catalogue,
    container: i32,
    sequence: i32,
) -> Vec<Vec<u8>> {
    let items: Vec<_> = if container == 1 {
        cat.heroes
            .keys()
            .map(|id| hero_card(profile, cat, *id))
            .collect()
    } else {
        profile
            .formation
            .iter()
            .enumerate()
            .map(|(i, id)| battle_hero(profile, cat, *id, (i + 1) as i32))
            .collect()
    };
    let capacity = if container == 1 {
        cat.heroes.len() as i32
    } else {
        4
    };
    let count = items.len().div_ceil(50).max(1);
    (0..count)
        .map(|index| {
            let mut out = Wire::default();
            let start = index * 50;
            let end = (start + 50).min(items.len());
            out.short(2)
                .int(container)
                .int(sequence)
                .byte(count as u8)
                .byte(index as u8)
                .int(capacity)
                .int(capacity)
                .int(items.len() as i32)
                .int((end - start) as i32);
            for item in &items[start..end] {
                out.bytes(item);
            }
            out.0
        })
        .collect()
}
pub(super) fn empty_container(container: i32, sequence: i32) -> Vec<u8> {
    let mut out = Wire::default();
    out.short(2).int(container).int(sequence).byte(1).zero(17);
    out.0
}
pub(super) fn formations(profile: &Profile, cat: &Catalogue) -> Vec<u8> {
    let mut out = Wire::default();
    out.short(4).int(1);
    for formation in 0..5 {
        if formation == 1 {
            out.int(4).int(profile.formation.len() as i32);
            for id in &profile.formation {
                out.reference(cat, *id);
            }
        } else {
            out.int(0).int(0);
        }
        out.int(0);
    }
    out.0
}
pub(super) fn result(selector: u16, error: i32) -> Vec<u8> {
    let mut out = Wire::default();
    out.short(match selector {
        5 => 6,
        14 => 15,
        8 => 9,
        _ => unreachable!(),
    })
    .int(error);
    if selector == 5 {
        out.int(1).int(0);
    }
    out.0
}
pub(super) fn weapon_notify(profile: &Profile, cat: &Catalogue, id: i32) -> Vec<u8> {
    let mut out = Wire::default();
    out.short(16)
        .reference(cat, id)
        .short(profile.weapons[&id].len() as i16);
    for item in &profile.weapons[&id] {
        out.int(item.slot as i32)
            .int(cat.heroes[&id].group_ids[item.group])
            .int(item.index as i32);
    }
    out.0
}
pub(super) fn hero_update(profile: &Profile, cat: &Catalogue, id: i32) -> Vec<u8> {
    let mut out = Wire::default();
    out.short(3)
        .int(1)
        .long(profile.revision)
        .int(1)
        .bytes(&hero_card(profile, cat, id));
    out.0
}

fn int(body: &[u8], offset: usize) -> i32 {
    i32::from_be_bytes(
        body[offset..offset + 4]
            .try_into()
            .expect("checked request length"),
    )
}
fn reference(body: &[u8], offset: usize, cat: &Catalogue) -> io::Result<i32> {
    cat.reference(
        u64::from_be_bytes(
            body[offset..offset + 8]
                .try_into()
                .expect("checked request length"),
        ),
        int(body, offset + 8),
    )
}
pub(super) fn container_request(body: &[u8]) -> io::Result<(i32, i32)> {
    if body.len() != 10 {
        return Err(invalid());
    }
    Ok((int(body, 2), int(body, 6)))
}
pub(super) fn decode_formation(body: &[u8], cat: &Catalogue) -> io::Result<Value> {
    if body.len() < 10 {
        return Err(invalid());
    }
    let count = int(body, 6);
    if int(body, 2) != 1 || !(0..=4).contains(&count) || body.len() != 10 + count as usize * 12 {
        return Err(invalid());
    }
    let ids = (0..count as usize)
        .map(|i| reference(body, 10 + i * 12, cat))
        .collect::<io::Result<Vec<_>>>()?;
    Ok(json!({"formation": ids}))
}
pub(super) fn decode_weapons(body: &[u8], cat: &Catalogue) -> io::Result<Value> {
    if body.len() < 16 {
        return Err(invalid());
    }
    let count = i16::from_be_bytes(body[14..16].try_into().expect("checked request length"));
    if !(0..=12).contains(&count) || body.len() != 16 + count as usize * 12 {
        return Err(invalid());
    }
    let id = reference(body, 2, cat)?;
    let hero = &cat.heroes[&id];
    let mut occupied = BTreeSet::new();
    let mut slots = Vec::new();
    for i in 0..count as usize {
        let offset = 16 + i * 12;
        let (slot, group_id, index) = (
            int(body, offset),
            int(body, offset + 4),
            int(body, offset + 8),
        );
        if !(0..=12).contains(&slot) || (slot != 0 && !occupied.insert(slot)) {
            return Err(invalid());
        }
        if group_id == 0 && (-1..=0).contains(&index) {
            continue;
        }
        let group = hero
            .group_ids
            .iter()
            .position(|id| *id == group_id)
            .ok_or_else(invalid)?;
        if group_id <= 0 || !(0..6).contains(&index) || hero.groups[group][index as usize] == 0 {
            return Err(invalid());
        }
        slots.push(json!({"slot":slot, "group":group, "index":index}));
    }
    Ok(json!({"heroId": id, "slots": slots}))
}
