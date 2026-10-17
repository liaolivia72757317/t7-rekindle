use crate::gbk;
use crate::table::invalid;
use crate::vfs::{ClientResources, MAX_ENTRY_BYTES, ResourceError};
use crate::xml::Element;
use serde::Serialize;
use std::collections::BTreeMap;
use std::io;

pub const SPAWN_PATH: &str = "data/scene/map/lysd/dactorspawngroup.dat";
pub type Placement = ([f64; 3], i32);

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(transparent)]
pub struct Placements(BTreeMap<u8, Vec<Placement>>);

impl Placements {
    pub fn camps(&self) -> &BTreeMap<u8, Vec<Placement>> {
        &self.0
    }
    pub fn to_json(&self) -> serde_json::Value {
        serde_json::json!(self)
    }
}

pub fn parse_spawns(data: &[u8]) -> io::Result<Placements> {
    parse_points(data, "is_born", [10, 10])
}
pub fn parse_respawns(data: &[u8]) -> io::Result<Placements> {
    parse_points(data, "is_revive", [14, 12])
}

/// Read-only export input. Runtime placements continue to come from the frozen map bundle.
pub fn read_placements(
    resources: &mut ClientResources,
) -> Result<(Placements, Placements), ResourceError> {
    let data = resources.read("map.vfs", SPAWN_PATH)?;
    let parse = || Ok((parse_spawns(&data)?, parse_respawns(&data)?));
    parse().map_err(|error: io::Error| {
        ResourceError::new("content_mismatch", "map.vfs", SPAWN_PATH, error)
    })
}

fn flag(group: &Element, name: &str) -> io::Result<bool> {
    match group.find(name)?.value()? {
        "0" => Ok(false),
        "1" => Ok(true),
        _ => Err(invalid(format!("invalid spawn flag: {name}"))),
    }
}

fn vector(point: &Element, name: &str) -> io::Result<[f64; 3]> {
    let values = point
        .find(name)?
        .value()?
        .split(',')
        .map(|part| {
            part.trim()
                .parse::<f64>()
                .map_err(|_| invalid("invalid spawn vector"))
        })
        .collect::<io::Result<Vec<_>>>()?;
    let values: [f64; 3] = values
        .try_into()
        .map_err(|_| invalid("invalid spawn vector"))?;
    if values
        .iter()
        .any(|value| !value.is_finite() || value.abs() >= 100000.)
    {
        return Err(invalid("invalid spawn vector"));
    }
    Ok(values)
}

fn parse_points(data: &[u8], flag_name: &str, counts: [usize; 2]) -> io::Result<Placements> {
    if data.len() > MAX_ENTRY_BYTES {
        return Err(invalid("spawn resource exceeds size limit"));
    }
    let root = crate::xml::parse(&gbk::decode(data)?)?;
    if root.name != "SH_RES_ACTOR_SPAWN_GROUP_TAB" {
        return Err(invalid("unexpected spawn resource root"));
    }
    let mut camps = BTreeMap::from([(1, Vec::new()), (2, Vec::new())]);
    let mut occupied = Vec::new();
    for group in &root.children {
        if !(flag(group, "is_enable")?
            && flag(group, flag_name)?
            && flag(group, "is_infantry")?
            && !flag(group, "is_cavalry")?)
        {
            continue;
        }
        let (blue, red) = (flag(group, "is_blue")?, flag(group, "is_red")?);
        if blue == red {
            return Err(invalid("infantry spawn requires one camp"));
        }
        let count = group
            .find("point_num")?
            .value()?
            .trim()
            .parse::<usize>()
            .map_err(|_| invalid("invalid spawn point count"))?;
        let points: Vec<_> = group
            .children
            .iter()
            .filter(|point| point.name == "points")
            .collect();
        if points.len() != count {
            return Err(invalid("spawn point count mismatch"));
        }
        let camp = if blue { 1 } else { 2 };
        for point in points {
            let (position, direction) = (vector(point, "pos")?, vector(point, "dir")?);
            if direction[0].hypot(direction[1]) < 0.001 {
                return Err(invalid("invalid spawn direction"));
            }
            let heading = (-direction[1])
                .atan2(direction[0])
                .to_degrees()
                .round_ties_even() as i32;
            if occupied.contains(&position) {
                return Err(invalid("overlapping infantry spawns"));
            }
            occupied.push(position);
            camps
                .get_mut(&camp)
                .expect("known camp")
                .push((position, heading));
        }
    }
    if camps[&1].len() != counts[0] || camps[&2].len() != counts[1] {
        return Err(invalid("unsupported infantry spawn counts"));
    }
    Ok(Placements(camps))
}
