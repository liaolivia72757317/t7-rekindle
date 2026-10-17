use serde_json::{Map, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::io;

pub(crate) fn require(ok: bool, message: &str) -> io::Result<()> {
    if ok {
        Ok(())
    } else {
        Err(io::Error::new(io::ErrorKind::InvalidData, message))
    }
}
fn number(value: &Value) -> io::Result<u32> {
    value
        .as_u64()
        .and_then(|v| u32::try_from(v).ok())
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "expected u32 resource field"))
}
fn array(value: &Value) -> io::Result<&Vec<Value>> {
    value
        .as_array()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "expected resource array"))
}
fn object(value: &Value) -> io::Result<&Map<String, Value>> {
    value
        .as_object()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "expected resource object"))
}
fn vector(value: &Value) -> io::Result<()> {
    let row = array(value)?;
    require(
        row.len() == 3
            && row.iter().all(|v| {
                v.as_f64()
                    .is_some_and(|f| f.is_finite() && f.abs() < 100000.)
            }),
        "invalid resource vector",
    )
}
fn identifiers(rows: &[Value]) -> io::Result<BTreeMap<u32, &Value>> {
    let mut result = BTreeMap::new();
    for row in rows {
        let id = number(&row["resourceId"])?;
        require(
            id > 0 && id <= i32::MAX as u32 && result.insert(id, row).is_none(),
            "duplicate or zero resource identifier",
        )?;
    }
    Ok(result)
}
fn matching_keys(values: &Map<String, Value>, ids: &BTreeSet<u32>) -> bool {
    values.keys().cloned().collect::<BTreeSet<_>>() == ids.iter().map(u32::to_string).collect()
}

pub(crate) fn bundle(data: &Value) -> io::Result<()> {
    let fields = [
        "schemaVersion",
        "rulesVersion",
        "resourceVersion",
        "clientVersion",
        "catalogue",
        "combatProfiles",
        "damageProfiles",
        "rules",
        "botNames",
        "map",
    ];
    require(
        object(data)?.len() == fields.len() && fields.iter().all(|f| data.get(f).is_some()),
        "unexpected resource bundle fields",
    )?;
    require(
        number(&data["schemaVersion"])? == crate::SCHEMA_VERSION,
        "unsupported resource schema",
    )?;
    require(
        number(&data["rulesVersion"])? > 0 && number(&data["resourceVersion"])? > 0,
        "zero resource/rules version",
    )?;
    let client = data["clientVersion"].as_str().unwrap_or("");
    require(
        !client.is_empty()
            && client.len() <= 64
            && client
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || b"_.-".contains(&c)),
        "invalid client compatibility identifier",
    )?;
    let heroes = identifiers(array(&data["catalogue"]["heroes"])?)?;
    let weapons = identifiers(array(&data["catalogue"]["weapons"])?)?;
    for weapon in weapons.values() {
        require(
            number(&weapon["family"])? <= i32::MAX as u32,
            "invalid weapon family",
        )?;
    }
    require(
        !heroes.is_empty()
            && heroes.len() <= 500
            && heroes.contains_key(&1101)
            && weapons.contains_key(&100000)
            && weapons.len() <= 10000,
        "incomplete or oversized catalogue",
    )?;
    let hero_ids = heroes.keys().copied().collect();
    require(
        matching_keys(object(&data["combatProfiles"])?, &hero_ids)
            && matching_keys(object(&data["damageProfiles"])?, &hero_ids),
        "profile hero set differs from catalogue",
    )?;
    for (id, hero) in heroes {
        let groups = array(&hero["groups"])?;
        require(
            groups.len() == 8
                && array(&hero["groupIds"])?.len() == 8
                && array(&hero["nativeGroups"])?.len() == 8,
            "invalid hero group count",
        )?;
        let mut owned = BTreeSet::from([100000]);
        for (group_index, group) in groups.iter().enumerate() {
            let slots = array(group)?;
            require(slots.len() == 6, "invalid weapon style count")?;
            let native = array(&hero["nativeGroups"][group_index])?;
            require(native.len() == 6, "invalid native weapon style count")?;
            let group_id = number(&hero["groupIds"][group_index])?;
            require(group_id <= i32::MAX as u32, "invalid native weapon group")?;
            for (index, slot) in slots.iter().enumerate() {
                let weapon = number(slot)?;
                let native_weapon = number(&native[index])?;
                require(
                    native_weapon <= i32::MAX as u32
                        && (weapon == 0 || (group_id > 0 && native_weapon == weapon)),
                    "native weapon mapping differs from supported catalogue",
                )?;
                require(
                    weapon == 0 || weapons.contains_key(&weapon),
                    "hero references unknown weapon",
                )?;
                if weapon > 0 {
                    owned.insert(weapon);
                }
            }
        }
        for selection in array(&hero["defaults"])? {
            let pair = array(selection)?;
            require(pair.len() == 2, "invalid default weapon slot")?;
            let (group, index) = (number(&pair[0])? as usize, number(&pair[1])? as usize);
            require(
                group < 8 && index < 6 && groups[group][index].as_u64().is_some_and(|n| n > 0),
                "unbound default weapon",
            )?;
        }
        let key = id.to_string();
        let combat = object(&data["combatProfiles"][&key])?;
        let damage = &data["damageProfiles"][&key];
        require(
            matching_keys(combat, &owned) && matching_keys(object(&damage["weapons"])?, &owned),
            "profile weapon set differs from hero",
        )?;
        require(
            number(&damage["heroId"])? == id,
            "damage profile hero mismatch",
        )?;
        number(&damage["armor"])?;
        require(
            (1..=i32::MAX as u32).contains(&number(&damage["maxHealth"])?),
            "invalid hero maximum health",
        )?;
        for weapon in owned {
            let key = weapon.to_string();
            let profile = &combat[&key];
            require(
                number(&profile["weaponId"])? == weapon
                    && number(&profile["weaponCategory"])?
                        == number(&weapons[&weapon]["category"])?
                    && number(&profile["hand"])? == number(&weapons[&weapon]["hand"])?
                    && number(&damage["weapons"][&key]["weaponId"])? == weapon,
                "weapon profile identity mismatch",
            )?;
            combat_profile(profile)?;
        }
    }
    for key in ["damage", "guard", "damageBuffs"] {
        require(
            !object(&data["rules"][key])?.is_empty(),
            "missing rule parameters",
        )?;
    }
    let names = array(&data["botNames"])?;
    let mut unique = BTreeSet::new();
    require(
        (19..=4096).contains(&names.len()),
        "insufficient or oversized AI name set",
    )?;
    for name in names {
        let name = name.as_str().unwrap_or("");
        require(
            !name.is_empty()
                && name.len() <= 128
                && !name.chars().any(char::is_control)
                && unique.insert(name),
            "invalid or duplicate AI name",
        )?;
    }
    map(&data["map"])
}

fn combat_profile(profile: &Value) -> io::Result<()> {
    let attacks = object(&profile["attacks"])?;
    require(
        number(&profile["weaponCategory"])? <= 32767,
        "invalid weapon category",
    )?;
    require(
        (1..=3).contains(&number(&profile["hand"])?),
        "invalid weapon hand",
    )?;
    if profile["shield"] == true {
        return require(
            attacks.is_empty()
                && number(&profile["changeMs"])? == 0
                && number(&profile["hand"])? == 1,
            "invalid shield profile",
        );
    }
    require(
        profile["shield"] == false && (1..=65535).contains(&number(&profile["changeMs"])?),
        "invalid combat profile",
    )?;
    let intents = ["300560", "300550", "300570", "300540"];
    require(
        attacks.len() == 4 && intents.iter().all(|key| attacks.contains_key(*key)),
        "invalid directional attacks",
    )?;
    for attack in attacks.values() {
        require(
            attack.get("enabled").is_none_or(Value::is_boolean),
            "invalid attack availability",
        )?;
        let phases = array(&attack["phases"])?;
        require(phases.len() == 4, "invalid attack phase count")?;
        for (index, phase) in phases.iter().enumerate() {
            let time = number(phase)?;
            require(
                if index == 1 {
                    time == 0
                } else {
                    (1..=65535).contains(&time)
                },
                "invalid attack phase time",
            )?;
        }
        for key in ["parried", "rebound"] {
            require(
                (1..=65535).contains(&number(&attack[key])?),
                "invalid reaction duration",
            )?;
        }
        require(
            number(&attack["contactStartMs"])? < number(&attack["contactEndMs"])?
                && number(&attack["contactEndMs"])? <= number(&phases[2])?,
            "invalid contact window",
        )?;
    }
    require(
        attacks.values().any(|attack| attack["enabled"] != false),
        "weapon has no enabled attacks",
    )?;
    for key in [
        "forwardSlow",
        "lateralSlow",
        "backwardSlow",
        "forwardSprint",
    ] {
        require(
            profile["movementAnimation"][key]
                .as_f64()
                .is_some_and(|n| n.is_finite() && n > 0.),
            "invalid movement profile",
        )?;
    }
    require(
        number(&profile["movementAnimation"]["weaponId"])? == number(&profile["weaponId"])?,
        "movement weapon mismatch",
    )?;
    for key in ["prepareMs", "successMs"] {
        require(
            (1..=65535).contains(&number(&profile["guard"][key])?),
            "invalid guard timing",
        )?;
    }
    if let Some(hurt) = profile.get("hurtMs") {
        require((1..=65535).contains(&number(hurt)?), "invalid hurt timing")?;
    }
    let dash = object(&profile["dashAttack"])?;
    if !dash.is_empty() {
        dash_profile(&profile["dashAttack"], number(&profile["weaponId"])?)?;
    }
    Ok(())
}

fn dash_profile(dash: &Value, weapon: u32) -> io::Result<()> {
    require(
        dash["enabled"].is_boolean()
            && number(&dash["weaponId"])? == weapon
            && dash["weaponCategory"]
                .as_i64()
                .is_some_and(|n| (-1..=32767).contains(&n))
            && dash["animationPath"].is_string()
            && matches!(
                dash["durationSource"].as_str(),
                Some("table" | "animation" | "unavailable")
            ),
        "invalid dash profile",
    )?;
    for key in [
        "durationMs",
        "moveMs",
        "contactStartMs",
        "contactEndMs",
        "reboundMs",
    ] {
        require(number(&dash[key])? <= 65535, "invalid dash timing")?;
    }
    require(
        number(&dash["speedMilli"])? <= i32::MAX as u32,
        "invalid dash speed",
    )?;
    if dash["enabled"] == true {
        require(
            number(&dash["weaponCategory"])? <= 32767
                && number(&dash["durationMs"])? > 0
                && number(&dash["moveMs"])? <= number(&dash["durationMs"])?
                && number(&dash["contactStartMs"])? <= number(&dash["contactEndMs"])?
                && number(&dash["contactEndMs"])? <= number(&dash["durationMs"])?
                && number(&dash["reboundMs"])? > 0
                && dash["durationSource"] != "unavailable",
            "invalid enabled dash",
        )?;
    }
    Ok(())
}

pub(crate) fn map(data: &Value) -> io::Result<()> {
    require(number(&data["resourceId"])? > 0, "invalid map identifier")?;
    vector(&data["position"])?;
    vector(&data["enemyPosition"])?;
    for field in ["spawns", "respawns"] {
        require(object(&data[field])?.len() == 2, "invalid spawn camps")?;
        let mut occupied = Vec::new();
        for camp in ["1", "2"] {
            let points = array(&data[field][camp])?;
            require(
                if field == "spawns" {
                    (1..=60).contains(&points.len())
                } else {
                    (1..=256).contains(&points.len())
                },
                "invalid spawn count",
            )?;
            for point in points {
                require(array(point)?.len() == 2, "invalid spawn row")?;
                vector(&point[0])?;
                let position = [
                    point[0][0].as_f64().expect("validated vector"),
                    point[0][1].as_f64().expect("validated vector"),
                    point[0][2].as_f64().expect("validated vector"),
                ];
                require(!occupied.contains(&position), "overlapping spawn positions")?;
                occupied.push(position);
                require(
                    point[1].as_i64().is_some_and(|n| (-180..=180).contains(&n)),
                    "invalid spawn heading",
                )?;
            }
        }
    }
    require(
        array(&data["spawns"]["1"])?.len() + array(&data["spawns"]["2"])?.len() <= 60,
        "map roster exceeds actor capacity",
    )?;
    navigation(&data["navigation"])
}

pub(crate) fn navigation(data: &Value) -> io::Result<()> {
    let vertices = array(&data["vertices"])?;
    let raw_edges = array(&data["edges"])?;
    let faces = array(&data["faces"])?;
    for size in [vertices.len(), raw_edges.len(), faces.len()] {
        require((1..=100000).contains(&size), "invalid navigation extent")?;
    }
    for vertex in vertices {
        vector(vertex)?;
    }
    let mut edges = Vec::with_capacity(raw_edges.len());
    for row in raw_edges {
        require(array(row)?.len() == 4, "invalid navigation edge")?;
        let edge: Vec<i64> = array(row)?
            .iter()
            .map(|n| {
                n.as_i64()
                    .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "invalid edge index"))
            })
            .collect::<io::Result<_>>()?;
        require(
            edge[..2]
                .iter()
                .all(|i| *i >= 0 && (*i as usize) < vertices.len()),
            "invalid navigation vertex index",
        )?;
        edges.push(edge);
    }
    let mut owners = vec![None; edges.len()];
    for (index, face) in faces.iter().enumerate() {
        require(array(face)?.len() == 3, "invalid navigation face")?;
        let start = number(&face[0])? as usize;
        let count = number(&face[1])? as usize;
        require(
            (3..=128).contains(&count)
                && start <= edges.len()
                && count <= edges.len() - start
                && number(&face[2])? == 0,
            "invalid navigation face edge range",
        )?;
        for ordinal in start..start + count {
            require(
                owners[ordinal].replace(index).is_none(),
                "navigation edge has multiple owners",
            )?;
            require(
                edges[ordinal][1] == edges[start + (ordinal - start + 1) % count][0],
                "navigation polygon not closed",
            )?;
        }
    }
    require(
        owners.iter().all(Option::is_some),
        "unowned navigation edges",
    )?;
    for (index, edge) in edges.iter().enumerate() {
        let (opposite, neighbor) = (edge[2], edge[3]);
        if opposite == -1 && neighbor == -1 {
            continue;
        }
        require(
            opposite >= 0
                && (opposite as usize) < edges.len()
                && neighbor >= 0
                && (neighbor as usize) < faces.len(),
            "navigation adjacency outside mesh",
        )?;
        let reverse = &edges[opposite as usize];
        require(
            owners[opposite as usize] == Some(neighbor as usize)
                && reverse[2] == index as i64
                && Some(reverse[3] as usize) == owners[index],
            "navigation adjacency is not reciprocal",
        )?;
    }
    Ok(())
}
