use std::collections::{BTreeMap, HashMap};

use serde::Serialize;

use crate::{
    client_view::ClientView,
    maps::MapCatalog,
    player::{PlayerProfile, Players},
    protocol::ErrorCode,
    room::{Phase, Room, RoomAddress, RoomGuid, RoomSnapshot, RoomState, Waiting},
};

pub const MAX_ROOMS: usize = 64;
pub const DEFAULT_CHANNEL_ID: u32 = 1;
pub const MAX_PAGE_SIZE: u8 = 20;

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Channel {
    pub channel_id: u32,
    pub name: &'static str,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RoomListing {
    pub room_id: String,
    #[serde(flatten)]
    pub address: RoomAddress,
    pub name: String,
    pub map_id: u32,
    pub owner_account_id: String,
    pub owner_display_name: String,
    pub member_count: usize,
    pub capacity: usize,
    pub phase: Phase,
    pub revision: u64,
    pub joinable: bool,
}

impl From<RoomSnapshot> for RoomListing {
    fn from(room: RoomSnapshot) -> Self {
        let owner_display_name = room
            .members
            .iter()
            .find(|member| member.account_id == room.owner_account_id)
            .expect("room owner is a member")
            .profile
            .display_name
            .clone();
        Self {
            owner_display_name,
            joinable: room.phase == Phase::Waiting && room.members.len() < room.capacity,
            room_id: room.room_id,
            address: room.address,
            name: room.name,
            map_id: room.map_id,
            owner_account_id: room.owner_account_id,
            member_count: room.members.len(),
            capacity: room.capacity,
            phase: room.phase,
            revision: room.revision,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RoomPage {
    pub channel_id: u32,
    pub start: u32,
    pub total: usize,
    pub rooms: Vec<RoomListing>,
}

#[derive(Default)]
pub struct Lobby {
    rooms: BTreeMap<String, RoomState>,
    membership: HashMap<String, String>,
    addresses: HashMap<RoomGuid, String>,
    last_room_number: u32,
    players: Players,
    maps: MapCatalog,
    rules: crate::limits::RoomRules,
}

impl Lobby {
    pub fn with_maps(maps: MapCatalog) -> Self {
        Self {
            maps,
            ..Self::default()
        }
    }

    pub fn with_rules(maps: MapCatalog, rules: crate::limits::RoomRules) -> Self {
        Self {
            maps,
            rules,
            ..Self::default()
        }
    }

    pub fn identify(
        &mut self,
        account: &str,
        display_name: &str,
    ) -> Result<PlayerProfile, ErrorCode> {
        self.players.identify(account, display_name)
    }

    pub(crate) fn forget_guest(&mut self, account: &str) {
        self.players.forget(account);
    }

    pub fn list(&self) -> Vec<RoomSnapshot> {
        self.rooms.values().map(RoomState::snapshot).collect()
    }

    pub fn channels(&self) -> Vec<Channel> {
        vec![Channel {
            channel_id: DEFAULT_CHANNEL_ID,
            name: "自定义房间",
        }]
    }

    pub fn page(&self, channel_id: u32, start: u32, limit: u8) -> Result<RoomPage, ErrorCode> {
        Self::validate_channel(channel_id)?;
        if limit == 0 || limit > MAX_PAGE_SIZE {
            return Err(ErrorCode::InvalidRoomPage);
        }
        let start_index = usize::try_from(start).map_err(|_| ErrorCode::InvalidRoomPage)?;
        let mut rooms: Vec<_> = self.list().into_iter().map(RoomListing::from).collect();
        rooms.sort_by_key(|room| room.address.room_number);
        Ok(RoomPage {
            channel_id,
            start,
            total: rooms.len(),
            rooms: rooms
                .into_iter()
                .skip(start_index)
                .take(usize::from(limit))
                .collect(),
        })
    }

    pub fn listing(&self, channel_id: u32, room_number: u32) -> Result<RoomListing, ErrorCode> {
        Self::validate_channel(channel_id)?;
        self.rooms
            .values()
            .map(RoomState::snapshot)
            .find(|room| room.address.room_number == room_number)
            .map(RoomListing::from)
            .ok_or(ErrorCode::RoomNotFound)
    }

    pub fn create_custom(
        &mut self,
        account: &str,
        name: &str,
        map_id: u32,
        channel_id: u32,
        password: &str,
    ) -> Result<RoomSnapshot, ErrorCode> {
        Self::validate_channel(channel_id)?;
        Self::validate_password(password)?;
        self.create(account, name, map_id)
    }

    pub fn join_custom(
        &mut self,
        account: &str,
        room_guid: RoomGuid,
        password: &str,
    ) -> Result<RoomSnapshot, ErrorCode> {
        Self::validate_password(password)?;
        let room_id = self
            .addresses
            .get(&room_guid)
            .ok_or(ErrorCode::RoomNotFound)?
            .clone();
        self.join(account, &room_id)
    }

    pub fn create(
        &mut self,
        account: &str,
        name: &str,
        map_id: u32,
    ) -> Result<RoomSnapshot, ErrorCode> {
        if self.membership.contains_key(account) {
            return Err(ErrorCode::AlreadyInRoom);
        }
        let map = self.maps.resolve(map_id)?;
        let name = name.trim();
        if name.is_empty() || name.chars().count() > 32 || name.chars().any(char::is_control) {
            return Err(ErrorCode::InvalidRoomName);
        }
        if self.rooms.len() >= MAX_ROOMS {
            return Err(ErrorCode::RoomLimit);
        }
        let room_number = self
            .last_room_number
            .checked_add(1)
            .ok_or(ErrorCode::RoomLimit)?;
        let address = RoomAddress::new(room_number, DEFAULT_CHANNEL_ID);
        let profile = self.players.get_or_default(account)?;
        let room = RoomState::Waiting(Room::<Waiting>::new(
            name.to_owned(),
            account,
            profile,
            address,
            map,
            self.rules,
        ));
        let snapshot = room.snapshot();
        let room_id = snapshot.room_id.clone();
        self.rooms.insert(room_id.clone(), room);
        self.addresses.insert(address.room_guid, room_id.clone());
        self.membership.insert(account.to_owned(), room_id);
        self.last_room_number = room_number;
        Ok(snapshot)
    }

    pub fn join(&mut self, account: &str, room_id: &str) -> Result<RoomSnapshot, ErrorCode> {
        if self.membership.contains_key(account) {
            return Err(ErrorCode::AlreadyInRoom);
        }
        let room = self.rooms.get_mut(room_id).ok_or(ErrorCode::RoomNotFound)?;
        let profile = self.players.get_or_default(account)?;
        room.waiting_mut()?.join(account, profile)?;
        self.membership
            .insert(account.to_owned(), room_id.to_owned());
        Ok(room.snapshot())
    }

    pub fn leave(&mut self, account: &str, disconnected: bool) -> Result<String, ErrorCode> {
        let room_id = self
            .membership
            .remove(account)
            .ok_or(ErrorCode::NotInRoom)?;
        let room = self.rooms.remove(&room_id).ok_or(ErrorCode::RoomNotFound)?;
        let address = room.snapshot().address;
        let room = room.leave(
            account,
            if disconnected {
                "disconnected"
            } else {
                "memberLeft"
            },
        );
        if let Some(room) = room {
            self.rooms.insert(room_id.clone(), room);
        } else {
            self.addresses.remove(&address.room_guid);
        }
        Ok(room_id)
    }

    pub fn snapshot(&self, room_id: &str) -> Option<RoomSnapshot> {
        self.rooms.get(room_id).map(RoomState::snapshot)
    }

    pub fn client_view(&self, account: &str, match_id: &str) -> Result<ClientView, ErrorCode> {
        let room_id = self.membership.get(account).ok_or(ErrorCode::NotInRoom)?;
        let room = self.rooms.get(room_id).ok_or(ErrorCode::RoomNotFound)?;
        let snapshot = room.snapshot();
        let current_match = snapshot
            .current_match
            .as_ref()
            .ok_or(ErrorCode::WrongPhase)?;
        if current_match.match_id != match_id {
            return Err(ErrorCode::StaleMatch);
        }
        ClientView::from_room(&snapshot, account)
    }

    pub fn set_camp(&mut self, account: &str, camp: u8) -> Result<RoomSnapshot, ErrorCode> {
        let room = self.member_room(account)?;
        room.waiting_mut()?.set_camp(account, camp)?;
        Ok(room.snapshot())
    }

    pub fn ready(&mut self, account: &str, ready: bool) -> Result<RoomSnapshot, ErrorCode> {
        let room = self.member_room(account)?;
        room.waiting_mut()?.ready(account, ready)?;
        Ok(room.snapshot())
    }

    pub fn start(&mut self, account: &str, now_ms: u64) -> Result<RoomSnapshot, ErrorCode> {
        self.transition(account, |room| room.start(account, now_ms))
    }

    pub fn loaded(
        &mut self,
        account: &str,
        match_id: &str,
        now_ms: u64,
    ) -> Result<RoomSnapshot, ErrorCode> {
        self.transition(account, |room| room.loaded(account, match_id, now_ms))
    }

    pub fn advance(&mut self, now_ms: u64) -> Vec<RoomSnapshot> {
        let mut changed = Vec::new();
        self.rooms = std::mem::take(&mut self.rooms)
            .into_iter()
            .map(|(id, room)| {
                let (room, updated) = room.advance(now_ms);
                if updated {
                    changed.push(room.snapshot());
                }
                (id, room)
            })
            .collect();
        changed
    }

    pub fn action_input(
        &mut self,
        account: &str,
        match_id: &str,
        input_seq: u32,
        intent: crate::battle::ActionIntent,
        now_ms: u64,
    ) -> Result<(), ErrorCode> {
        self.member_room(account)?
            .action_input(account, match_id, input_seq, intent, now_ms)
    }

    pub fn contact_input(
        &mut self,
        account: &str,
        match_id: &str,
        input_seq: u32,
        report: crate::combat::ContactReport,
        now_ms: u64,
    ) -> Result<bool, ErrorCode> {
        self.member_room(account)?
            .contact_input(account, match_id, input_seq, report, now_ms)
    }
    pub fn respawn(&mut self, account: &str, match_id: &str, now_ms: u64) -> Result<(), ErrorCode> {
        self.member_room(account)?
            .respawn(account, match_id, now_ms)
    }
    pub fn take_combat_events(&mut self) -> Vec<crate::combat::CombatEvent> {
        self.rooms
            .values_mut()
            .flat_map(RoomState::take_combat_events)
            .collect()
    }
    pub fn take_action_events(&mut self) -> Vec<crate::battle::ActionEvent> {
        self.rooms
            .values_mut()
            .flat_map(RoomState::take_action_events)
            .collect()
    }

    pub fn pose_input(
        &mut self,
        account: &str,
        match_id: &str,
        input_seq: u32,
        pose: crate::motion::Pose,
        now_ms: u64,
    ) -> Result<(), ErrorCode> {
        self.member_room(account)?
            .pose_input(account, match_id, input_seq, pose, now_ms)
    }

    pub fn take_pose_events(&mut self) -> Vec<crate::motion::PoseEvent> {
        self.rooms
            .values_mut()
            .flat_map(RoomState::take_pose_events)
            .collect()
    }

    fn member_room(&mut self, account: &str) -> Result<&mut RoomState, ErrorCode> {
        let room_id = self.membership.get(account).ok_or(ErrorCode::NotInRoom)?;
        self.rooms.get_mut(room_id).ok_or(ErrorCode::RoomNotFound)
    }

    fn validate_channel(channel_id: u32) -> Result<(), ErrorCode> {
        if channel_id != DEFAULT_CHANNEL_ID {
            return Err(ErrorCode::UnsupportedChannel);
        }
        Ok(())
    }

    fn validate_password(password: &str) -> Result<(), ErrorCode> {
        if !password.is_empty() {
            return Err(ErrorCode::PasswordUnsupported);
        }
        Ok(())
    }

    fn transition(
        &mut self,
        account: &str,
        apply: impl FnOnce(RoomState) -> Result<RoomState, (RoomState, ErrorCode)>,
    ) -> Result<RoomSnapshot, ErrorCode> {
        let room_id = self
            .membership
            .get(account)
            .ok_or(ErrorCode::NotInRoom)?
            .clone();
        let room = self.rooms.remove(&room_id).ok_or(ErrorCode::RoomNotFound)?;
        match apply(room) {
            Ok(room) => {
                let snapshot = room.snapshot();
                self.rooms.insert(room_id, room);
                Ok(snapshot)
            }
            Err((room, error)) => {
                self.rooms.insert(room_id, room);
                Err(error)
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::room::MAP_ID;

    #[test]
    fn exhausted_numbers_do_not_wrap_or_leave_a_partial_room() {
        let mut lobby = Lobby {
            last_room_number: u32::MAX,
            ..Lobby::default()
        };
        assert_eq!(
            lobby.create("player-a", "room", MAP_ID),
            Err(ErrorCode::RoomLimit)
        );
        assert!(lobby.rooms.is_empty());
        assert!(lobby.addresses.is_empty());
        assert!(lobby.membership.is_empty());
    }
}
