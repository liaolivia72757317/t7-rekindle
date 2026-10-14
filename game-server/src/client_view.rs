use serde::Serialize;

use crate::{
    protocol::ErrorCode,
    room::{Phase, RoomSnapshot},
};

/// Identity in the shared room, independent of any client's object table.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(transparent)]
pub struct ActorId(pub u32);

/// Identity in one original client's object table; RID 1 is always its owner.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(transparent)]
pub struct LocalRid(pub u32);

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ActorBinding {
    pub actor_id: ActorId,
    pub local_rid: LocalRid,
    pub camp: u8,
    pub is_local: bool,
}

/// A recipient-specific mapping for one frozen match roster. This describes
/// the adapter contract; it does not assert that native spawning is integrated.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ClientView {
    room_id: String,
    match_id: String,
    map_id: u32,
    revision: u64,
    phase: Phase,
    load_deadline_ms: u64,
    starts_at_ms: Option<u64>,
    local_actor_id: ActorId,
    actors: Vec<ActorBinding>,
}

impl ClientView {
    pub(crate) fn from_room(room: &RoomSnapshot, account: &str) -> Result<Self, ErrorCode> {
        let local = room
            .members
            .iter()
            .find(|member| member.account_id == account)
            .ok_or(ErrorCode::NotInRoom)?;
        let current_match = room.current_match.as_ref().ok_or(ErrorCode::WrongPhase)?;
        let mut actors = vec![ActorBinding {
            actor_id: ActorId(local.actor_id),
            local_rid: LocalRid(1),
            camp: local.camp,
            is_local: true,
        }];
        let mut enemy = crate::limits::ENEMY_FIRST_RID;
        let mut friendly = crate::limits::FRIENDLY_FIRST_RID;
        for member in room.members.iter().filter(|m| m.account_id != account) {
            let slot = if member.camp == local.camp {
                &mut friendly
            } else {
                &mut enemy
            };
            actors.push(ActorBinding {
                actor_id: ActorId(member.actor_id),
                local_rid: LocalRid(*slot),
                camp: member.camp,
                is_local: false,
            });
            *slot += 1;
        }
        Ok(Self {
            room_id: room.room_id.clone(),
            match_id: current_match.match_id.clone(),
            map_id: room.map_id,
            revision: room.revision,
            phase: room.phase,
            load_deadline_ms: current_match.load_deadline_ms,
            starts_at_ms: current_match.starts_at_ms,
            local_actor_id: ActorId(local.actor_id),
            actors,
        })
    }

    pub fn actors(&self) -> &[ActorBinding] {
        &self.actors
    }

    pub fn to_actor(&self, match_id: &str, rid: LocalRid) -> Result<ActorId, ErrorCode> {
        self.check_match(match_id)?;
        self.actors
            .iter()
            .find(|binding| binding.local_rid == rid)
            .map(|binding| binding.actor_id)
            .ok_or(ErrorCode::UnknownActor)
    }

    pub fn to_local(&self, match_id: &str, actor: ActorId) -> Result<LocalRid, ErrorCode> {
        self.check_match(match_id)?;
        self.actors
            .iter()
            .find(|binding| binding.actor_id == actor)
            .map(|binding| binding.local_rid)
            .ok_or(ErrorCode::UnknownActor)
    }

    fn check_match(&self, match_id: &str) -> Result<(), ErrorCode> {
        if match_id == self.match_id {
            Ok(())
        } else {
            Err(ErrorCode::StaleMatch)
        }
    }
}
