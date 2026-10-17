#![forbid(unsafe_code)]

/// Largest native roster in the supported map catalogue (30 per camp).
pub const MAX_BATTLE_ACTORS: usize = 60;

pub mod ai;

pub mod action;
pub mod battle_protocol;
pub mod battle_semantics;
pub mod damage;
pub mod guard;
pub mod guard_meter;
pub mod navigation;
pub mod protocol;
pub mod schedule;
