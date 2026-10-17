//! Portable resource preflight and read-only access to the original client resource tables.
#![forbid(unsafe_code)]

pub mod bot_names;
pub mod cache;
pub mod catalogue;
pub mod frozen;
mod gbk;
pub mod match_maps;
pub mod navmesh;
pub mod spawn;
mod table;
pub mod training_maps;
mod validate;
pub mod vfs;
mod xml;

use serde_json::Value;
use std::io::{self, Read};
use std::path::Path;

pub const SCHEMA_VERSION: u32 = 1;
pub const MAX_BYTES: usize = 64 * 1024 * 1024;

#[derive(Debug)]
pub struct ResourceBundle {
    data: Value,
}

impl ResourceBundle {
    pub fn load(path: &Path) -> io::Result<Self> {
        Self::from_reader(std::fs::File::open(path)?)
    }

    pub fn from_reader(reader: impl Read) -> io::Result<Self> {
        let mut bytes = Vec::new();
        reader
            .take((MAX_BYTES + 1) as u64)
            .read_to_end(&mut bytes)?;
        Self::from_slice(&bytes)
    }

    pub fn from_slice(bytes: &[u8]) -> io::Result<Self> {
        validate::require(bytes.len() <= MAX_BYTES, "bundle exceeds size limit")?;
        let data: Value = serde_json::from_slice(bytes)
            .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
        validate::bundle(&data)?;
        Ok(Self { data })
    }

    pub fn data(&self) -> &Value {
        &self.data
    }
    pub fn rules_version(&self) -> u32 {
        self.data["rulesVersion"]
            .as_u64()
            .expect("validated version") as u32
    }
    pub fn resource_version(&self) -> u32 {
        self.data["resourceVersion"]
            .as_u64()
            .expect("validated version") as u32
    }
    pub fn map_id(&self) -> u32 {
        self.data["map"]["resourceId"]
            .as_u64()
            .expect("validated map") as u32
    }
}
