mod tag;
mod values;

pub use tag::TagReader;

use crate::table::invalid;
use crate::vfs::{ClientResources, ResourceError};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::io;

pub const NAV_PATH: &str = "data/scene/map/lysd/anavmesh.dat";
pub const NAV_HASH: &str = "b92da3edc697ada355a95359d94ff3025841bf2de57833297901361452c539a3";
const NAV_DIGEST: [u8; 32] = [
    0xb9, 0x2d, 0xa3, 0xed, 0xc6, 0x97, 0xad, 0xa3, 0x55, 0xa9, 0x53, 0x59, 0xd9, 0x4f, 0xf3, 0x02,
    0x58, 0x41, 0xbf, 0x2d, 0xe5, 0x78, 0x33, 0x29, 0x79, 0x01, 0x36, 0x14, 0x52, 0xc5, 0x39, 0xa3,
];

#[derive(Debug)]
pub struct NavigationResource {
    data: Value,
}

impl NavigationResource {
    pub fn data(&self) -> &Value {
        &self.data
    }
}

/// Parse the known Havok v5 representation for development-time inspection/export.
pub fn parse_navigation(data: &[u8]) -> io::Result<NavigationResource> {
    let raw = TagReader::new(data)?.document()?;
    if raw["streamingSets"]
        .as_array()
        .is_none_or(|values| !values.is_empty())
    {
        return Err(invalid("navigation requires a single nonempty mesh"));
    }
    let vertices = raw["vertices"]
        .as_array()
        .ok_or_else(|| invalid("missing navigation vertices"))?;
    let vertices = vertices
        .iter()
        .map(|row| {
            let row = row
                .as_array()
                .ok_or_else(|| invalid("invalid navigation vertex"))?;
            if row.len() < 3 {
                return Err(invalid("invalid navigation vertex"));
            }
            Ok(Value::Array(row[..3].to_vec()))
        })
        .collect::<io::Result<Vec<_>>>()?;
    let convert = |name: &str, fields: &[&str]| -> io::Result<Value> {
        let rows = raw[name]
            .as_array()
            .ok_or_else(|| invalid(format!("missing navigation {name}")))?;
        Ok(Value::Array(
            rows.iter()
                .map(|row| Value::Array(fields.iter().map(|field| row[*field].clone()).collect()))
                .collect(),
        ))
    };
    let data = serde_json::json!({"vertices": vertices,
        "edges": convert("edges", &["a", "b", "oppositeEdge", "oppositeFace"] )?,
        "faces": convert("faces", &["startEdgeIndex", "numEdges", "numUserEdges"])?});
    crate::validate::navigation(&data)?;
    validate_face_extents(&data)?;
    Ok(NavigationResource { data })
}

/// Check the supported original topology; runtime navigation remains the frozen bundle data.
pub fn read_navigation(
    resources: &mut ClientResources,
) -> Result<NavigationResource, ResourceError> {
    let data = resources.read("map.vfs", NAV_PATH)?;
    read_checked(&data, &NAV_DIGEST)
        .map_err(|error| ResourceError::new("content_mismatch", "map.vfs", NAV_PATH, error))
}

fn read_checked(data: &[u8], digest: &[u8; 32]) -> io::Result<NavigationResource> {
    if Sha256::digest(data)[..] != *digest {
        return Err(invalid(
            "navigation resource does not match the supported client",
        ));
    }
    parse_navigation(data)
}

fn validate_face_extents(data: &Value) -> io::Result<()> {
    let vertices = data["vertices"].as_array().expect("validated vertices");
    let edges = data["edges"].as_array().expect("validated edges");
    for face in data["faces"].as_array().expect("validated faces") {
        let start = face[0].as_u64().expect("validated face") as usize;
        let count = face[1].as_u64().expect("validated face") as usize;
        let polygon: Vec<[f64; 2]> = edges[start..start + count]
            .iter()
            .map(|edge| {
                let point = &vertices[edge[0].as_u64().expect("validated edge") as usize];
                [
                    point[0].as_f64().expect("validated vertex"),
                    point[1].as_f64().expect("validated vertex"),
                ]
            })
            .collect();
        let area: f64 = polygon
            .iter()
            .zip(polygon.iter().cycle().skip(1))
            .map(|(a, b)| a[0] * b[1] - b[0] * a[1])
            .sum();
        if area.abs() <= 1e-5 {
            continue;
        }
        let convex = (0..count).all(|index| {
            let a = polygon[(index + count - 1) % count];
            let b = polygon[index];
            let c = polygon[(index + 1) % count];
            let cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
            cross * area.signum() >= -1e-5
        });
        if !convex {
            continue;
        }
        let mut cells = 1.;
        for dimension in 0..2 {
            let min = polygon
                .iter()
                .map(|point| point[dimension])
                .fold(f64::INFINITY, f64::min);
            let max = polygon
                .iter()
                .map(|point| point[dimension])
                .fold(f64::NEG_INFINITY, f64::max);
            cells *= (max / 8.).floor() - (min / 8.).floor() + 1.;
        }
        if cells > 10000. {
            return Err(invalid("navigation face extent exceeds limit"));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    #[test]
    fn supported_hash_is_fixed_and_compared_before_parsing() {
        let data = include_bytes!("../../../../../tests/fixtures/resources/navigation-v1.bin");
        assert!(super::read_checked(data, &super::NAV_DIGEST).is_err());
        use sha2::Digest;
        let digest = sha2::Sha256::digest(data);
        assert!(super::read_checked(data, &digest.into()).is_ok());
        assert_eq!(
            super::NAV_HASH,
            super::NAV_DIGEST
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect::<String>()
        );
    }
}
