#![deny(unsafe_code)]

pub mod battle_resources;
pub mod battle_session;
pub mod battle_worker;
#[cfg(windows)]
pub mod client_battle_wire;
#[cfg(windows)]
pub mod client_codec;
#[cfg(windows)]
pub mod gateway;
#[cfg(windows)]
#[allow(unsafe_code)]
mod gbk;
pub mod mailbox;
#[cfg(windows)]
#[allow(unsafe_code)]
mod method3;
pub mod semantic_adapter;
pub mod transport;

pub const INPUT_EVENT_LIMIT: usize = 1024;
pub const INPUT_BYTE_LIMIT: usize = 8 * 1024 * 1024;
pub const SEND_BYTE_LIMIT: usize = 4 * 1024 * 1024;

pub use t7r_battle_core::MAX_BATTLE_ACTORS;
