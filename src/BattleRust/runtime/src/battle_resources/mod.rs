//! Frozen rules and navigation, prepared before opening a local gameplay session.
mod rules;

use serde_json::Value;
use std::io;
use t7r_battle_core::{
    damage, guard,
    navigation::{
        NavMesh, NavigationEdge, NavigationFace, NavigationResource, Point, SpawnCandidates,
        SpawnReference,
    },
};
use t7r_battle_resources::ResourceBundle;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct DamageBuffRule {
    pub multiplier: f64,
    pub duration_ms: u32,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct DamageBuffRules {
    pub riposte: DamageBuffRule,
    pub combo: DamageBuffRule,
    pub rage: DamageBuffRule,
}

pub struct CampNavigation {
    spawns: Vec<SpawnReference>,
    respawns: Vec<SpawnReference>,
    candidates: SpawnCandidates,
}

impl CampNavigation {
    pub fn spawns(&self) -> &[SpawnReference] {
        &self.spawns
    }

    pub fn respawns(&self) -> &[SpawnReference] {
        &self.respawns
    }

    pub fn candidates(&self) -> &SpawnCandidates {
        &self.candidates
    }
}

pub struct BattleResources {
    rules_version: u32,
    resource_version: u32,
    map_id: u32,
    damage: damage::Rules,
    guard: guard::Rules,
    damage_buffs: DamageBuffRules,
    navigation: NavMesh,
    camps: [CampNavigation; 2],
    training_entry: Option<SpawnReference>,
    training_return: Option<SpawnReference>,
}

impl BattleResources {
    pub fn prepare(bundle: &ResourceBundle) -> io::Result<Self> {
        let data = bundle.data();
        let damage = rules::damage(&data["rules"]["damage"])?;
        let guard = rules::guard(&data["rules"]["guard"])?;
        let damage_buffs = rules::damage_buffs(&data["rules"]["damageBuffs"])?;
        let raw = &data["map"]["navigation"];
        let resource = NavigationResource {
            vertices: array(&raw["vertices"])?
                .iter()
                .map(point)
                .collect::<io::Result<_>>()?,
            edges: array(&raw["edges"])?
                .iter()
                .map(|row| {
                    Ok(NavigationEdge {
                        a: index(&row[0])?,
                        b: index(&row[1])?,
                        opposite_edge: index(&row[2])?,
                        opposite_face: index(&row[3])?,
                    })
                })
                .collect::<io::Result<_>>()?,
            faces: array(&raw["faces"])?
                .iter()
                .map(|row| {
                    Ok(NavigationFace {
                        start_edge_index: index(&row[0])?,
                        num_edges: index(&row[1])?,
                        num_user_edges: index(&row[2])?,
                    })
                })
                .collect::<io::Result<_>>()?,
            streaming_sets: 0,
        };
        let navigation = NavMesh::from_resource(&resource).map_err(invalid)?;
        let camp = |key: &str| -> io::Result<CampNavigation> {
            let spawns = references(&data["map"]["spawns"][key])?;
            let respawns = references(&data["map"]["respawns"][key])?;
            let anchor = spawns
                .first()
                .ok_or_else(|| invalid("empty camp spawn set"))?
                .position;
            let candidates =
                SpawnCandidates::prewarm(&navigation, &respawns, anchor).map_err(invalid)?;
            Ok(CampNavigation {
                spawns,
                respawns,
                candidates,
            })
        };
        let camps = [camp("1")?, camp("2")?];
        let mode = t7r_battle_resources::training_maps::mode(bundle.map_id())?;
        let training_entry = mode
            .map(|mode| reference(&mode["entrySpawn"]))
            .transpose()?;
        let training_return = mode
            .map(|mode| reference(&mode["returnSpawn"]))
            .transpose()?;
        Ok(Self {
            rules_version: bundle.rules_version(),
            resource_version: bundle.resource_version(),
            map_id: bundle.map_id(),
            damage,
            guard,
            damage_buffs,
            navigation,
            camps,
            training_entry,
            training_return,
        })
    }

    pub fn rules_version(&self) -> u32 {
        self.rules_version
    }
    pub fn resource_version(&self) -> u32 {
        self.resource_version
    }
    pub fn map_id(&self) -> u32 {
        self.map_id
    }
    pub fn damage_rules(&self) -> &damage::Rules {
        &self.damage
    }
    pub fn guard_rules(&self) -> &guard::Rules {
        &self.guard
    }
    pub fn damage_buff_rules(&self) -> &DamageBuffRules {
        &self.damage_buffs
    }
    pub fn navigation(&self) -> &NavMesh {
        &self.navigation
    }
    /// Index zero is camp 1, index one is camp 2; no client-specific RID is stored.
    pub fn camps(&self) -> &[CampNavigation; 2] {
        &self.camps
    }

    pub fn training_entry(&self) -> Option<SpawnReference> {
        self.training_entry
    }
    pub fn training_return(&self) -> Option<SpawnReference> {
        self.training_return
    }
}

fn reference(row: &Value) -> io::Result<SpawnReference> {
    Ok(SpawnReference {
        position: point(&row[0])?,
        heading: number(&row[1])?,
    })
}

fn references(value: &Value) -> io::Result<Vec<SpawnReference>> {
    array(value)?.iter().map(reference).collect()
}

fn point(value: &Value) -> io::Result<Point> {
    let values = array(value)?;
    if values.len() != 3 {
        return Err(invalid("expected three-dimensional resource point"));
    }
    Ok([
        number(&values[0])?,
        number(&values[1])?,
        number(&values[2])?,
    ])
}

fn invalid(error: impl std::fmt::Display) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, error.to_string())
}

fn array(value: &Value) -> io::Result<&[Value]> {
    value
        .as_array()
        .map(Vec::as_slice)
        .ok_or_else(|| invalid("expected resource array"))
}

fn number(value: &Value) -> io::Result<f64> {
    value
        .as_f64()
        .filter(|v| v.is_finite())
        .ok_or_else(|| invalid("expected finite resource number"))
}

fn index(value: &Value) -> io::Result<i32> {
    value
        .as_i64()
        .and_then(|v| i32::try_from(v).ok())
        .ok_or_else(|| invalid("expected i32 resource index"))
}
