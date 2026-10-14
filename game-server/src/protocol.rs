use serde::{Deserialize, Serialize};

use crate::{
    battle::{ActionEvent, ActionIntent},
    client_view::ClientView,
    lobby::{Channel, RoomListing, RoomPage},
    motion::{Pose, PoseEvent},
    player::PlayerProfile,
    room::{RoomGuid, RoomSnapshot},
};

pub const VERSION: u16 = 1;
pub const MAX_REQUEST_BYTES: usize = 4096;

#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ClientRequest {
    pub protocol_version: u16,
    pub request_id: String,
    pub command: ClientCommand,
}

impl ClientRequest {
    pub fn sequence(&self) -> Result<u64, ErrorCode> {
        let value = self
            .request_id
            .parse::<u64>()
            .map_err(|_| ErrorCode::InvalidRequestId)?;
        if value == 0 || value.to_string() != self.request_id {
            return Err(ErrorCode::InvalidRequestId);
        }
        Ok(value)
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(
    tag = "type",
    content = "payload",
    rename_all = "camelCase",
    deny_unknown_fields
)]
pub enum ClientCommand {
    Hello {
        #[serde(rename = "accountId")]
        account_id: String,
    },
    GatewayHello {
        #[serde(rename = "accountId")]
        account_id: String,
        #[serde(rename = "displayName")]
        display_name: String,
    },
    Ping,
    ListRooms,
    ListChannels,
    ListRoomPage {
        #[serde(rename = "channelId")]
        channel_id: u32,
        start: u32,
        limit: u8,
    },
    GetRoomListing {
        #[serde(rename = "channelId")]
        channel_id: u32,
        #[serde(rename = "roomNumber")]
        room_number: u32,
    },
    CreateCustomRoom {
        name: String,
        #[serde(rename = "mapId")]
        map_id: u32,
        #[serde(rename = "channelId")]
        channel_id: u32,
        #[serde(default)]
        password: String,
    },
    JoinCustomRoom {
        #[serde(rename = "roomGuid")]
        room_guid: RoomGuid,
        #[serde(default)]
        password: String,
    },
    CreateRoom {
        name: String,
        #[serde(rename = "mapId")]
        map_id: u32,
    },
    JoinRoom {
        #[serde(rename = "roomId")]
        room_id: String,
    },
    LeaveRoom,
    SetCamp {
        camp: u8,
    },
    Ready {
        ready: bool,
    },
    Start,
    Loaded {
        #[serde(rename = "matchId")]
        match_id: String,
    },
    GetClientView {
        #[serde(rename = "matchId")]
        match_id: String,
    },
    ActionInput {
        #[serde(rename = "matchId")]
        match_id: String,
        #[serde(rename = "inputSeq")]
        input_seq: u32,
        intent: ActionIntent,
    },
    ContactInput {
        #[serde(rename = "matchId")]
        match_id: String,
        #[serde(rename = "inputSeq")]
        input_seq: u32,
        contact: crate::combat::ContactReport,
    },
    Respawn {
        #[serde(rename = "matchId")]
        match_id: String,
    },
    PoseInput {
        #[serde(rename = "matchId")]
        match_id: String,
        #[serde(rename = "inputSeq")]
        input_seq: u32,
        pose: Pose,
    },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum ErrorCode {
    BadRequest,
    InvalidRequestId,
    UnsupportedVersion,
    HelloRequired,
    AlreadyIdentified,
    InvalidAccountId,
    InvalidDisplayName,
    IdentityLimit,
    AccountConnected,
    RequestConflict,
    StaleRequest,
    AlreadyInRoom,
    NotInRoom,
    RoomNotFound,
    RoomFull,
    RoomLimit,
    InvalidRoomName,
    UnsupportedMap,
    UnsupportedChannel,
    InvalidRoomPage,
    PasswordUnsupported,
    InvalidCamp,
    NotOwner,
    NotReady,
    InvalidTeams,
    WrongPhase,
    StaleMatch,
    LoadTimeout,
    UnknownActor,
    StaleInput,
    InvalidPose,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ServerEvent {
    pub protocol_version: u16,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
    pub event: ServerMessage,
}

impl ServerEvent {
    pub fn reply(request_id: String, event: ServerMessage) -> Self {
        Self {
            protocol_version: VERSION,
            request_id: Some(request_id),
            event,
        }
    }

    pub fn changed(room: RoomSnapshot) -> Self {
        Self {
            protocol_version: VERSION,
            request_id: None,
            event: ServerMessage::RoomChanged { room },
        }
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "type", content = "payload", rename_all = "camelCase")]
pub enum ServerMessage {
    Welcome {
        #[serde(rename = "accountId")]
        account_id: String,
        #[serde(rename = "connectionId")]
        connection_id: String,
        #[serde(flatten)]
        profile: PlayerProfile,
    },
    Pong {
        #[serde(rename = "serverTimeMs")]
        server_time_ms: u64,
    },
    RoomList {
        rooms: Vec<RoomSnapshot>,
    },
    ChannelList {
        channels: Vec<Channel>,
    },
    RoomPage {
        page: RoomPage,
    },
    RoomListing {
        room: RoomListing,
    },
    Room {
        room: RoomSnapshot,
    },
    RoomChanged {
        room: RoomSnapshot,
    },
    ClientView {
        view: ClientView,
    },
    ActionAccepted {
        #[serde(rename = "matchId")]
        match_id: String,
        #[serde(rename = "inputSeq")]
        input_seq: u32,
    },
    ActionChanged {
        action: ActionEvent,
    },
    ContactAccepted {
        #[serde(rename = "matchId")]
        match_id: String,
        #[serde(rename = "inputSeq")]
        input_seq: u32,
        accepted: bool,
    },
    RespawnAccepted {
        #[serde(rename = "matchId")]
        match_id: String,
    },
    CombatChanged {
        update: crate::combat::CombatEvent,
    },
    PoseAccepted {
        #[serde(rename = "matchId")]
        match_id: String,
        #[serde(rename = "inputSeq")]
        input_seq: u32,
    },
    PoseChanged {
        update: PoseEvent,
    },
    Left {
        #[serde(rename = "roomId")]
        room_id: String,
    },
    Error {
        code: ErrorCode,
    },
}
