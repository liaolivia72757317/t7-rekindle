use serde::{Deserialize, Serialize};

use crate::maps::LobbyMap;
use crate::player::PlayerProfile;
use crate::protocol::ErrorCode;

pub const MAP_ID: u32 = 1028;

pub const CAPACITY: usize = crate::limits::ROOM_CAPACITY;
pub const LOAD_TIMEOUT_MS: u64 = 60_000;
pub const COUNTDOWN_MS: u64 = 3_000;

/// A directory key for this server lifetime, distinct from an account or actor.
/// JSON uses a decimal string so gateways can preserve every u64 bit.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(try_from = "String", into = "String")]
pub struct RoomGuid(u64);

impl TryFrom<String> for RoomGuid {
    type Error = &'static str;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        let number = value.parse::<u64>().map_err(|_| "invalid room guid")?;
        if number == 0 || number.to_string() != value {
            return Err("invalid room guid");
        }
        Ok(Self(number))
    }
}

impl From<RoomGuid> for String {
    fn from(value: RoomGuid) -> Self {
        value.0.to_string()
    }
}

impl RoomGuid {
    pub fn value(self) -> u64 {
        self.0
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RoomAddress {
    pub room_guid: RoomGuid,
    pub room_number: u32,
    pub channel_id: u32,
}

impl RoomAddress {
    pub(crate) fn new(number: u32, channel_id: u32) -> Self {
        Self {
            room_guid: RoomGuid(u64::from(number)),
            room_number: number,
            channel_id,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum Phase {
    Waiting,
    Loading,
    Countdown,
    Running,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Member {
    pub account_id: String,
    #[serde(flatten)]
    pub profile: PlayerProfile,
    pub actor_id: u32,
    pub camp: u8,
    pub ready: bool,
    pub loaded: bool,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Match {
    pub match_id: String,
    pub load_deadline_ms: u64,
    pub starts_at_ms: Option<u64>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RoomSnapshot {
    pub room_id: String,
    #[serde(flatten)]
    pub address: RoomAddress,
    pub name: String,
    pub map_id: u32,
    pub capacity: usize,
    pub revision: u64,
    pub owner_account_id: String,
    pub phase: Phase,
    pub members: Vec<Member>,
    pub current_match: Option<Match>,
    pub last_abort_reason: Option<&'static str>,
}

#[derive(Debug)]
struct RoomData {
    room_id: String,
    map: LobbyMap,
    rules: crate::limits::RoomRules,
    address: RoomAddress,
    name: String,
    revision: u64,
    owner_account_id: String,
    members: Vec<Member>,
    next_actor_id: u32,
    last_abort_reason: Option<&'static str>,
}

#[derive(Debug)]
pub struct Waiting;

#[derive(Debug)]
pub struct Loading {
    current_match: Match,
}

#[derive(Debug)]
pub struct Countdown {
    current_match: Match,
    starts_at_ms: u64,
}

#[derive(Debug)]
pub struct Running {
    current_match: Match,
    battle: Box<crate::battle::Battle>,
    motion: Box<crate::motion::Motion>,
    combat: Box<crate::combat::Combat>,
}

/// The common data is composed with phase-specific data. Only a waiting room
/// exposes preparation operations; transitions consume the previous room.
///
/// ```compile_fail
/// use t7_game_server::room::{Loading, Room};
/// fn change_ready(room: &mut Room<Loading>) {
///     room.ready("player-a", true).unwrap();
/// }
/// ```
#[derive(Debug)]
pub struct Room<S> {
    data: Box<RoomData>,
    state: S,
}

impl<S> Room<S> {
    fn snapshot(&self, phase: Phase, current_match: Option<Match>) -> RoomSnapshot {
        RoomSnapshot {
            room_id: self.data.room_id.clone(),
            address: self.data.address,
            name: self.data.name.clone(),
            map_id: self.data.map.value(),
            capacity: self.data.rules.capacity(),
            revision: self.data.revision,
            owner_account_id: self.data.owner_account_id.clone(),
            phase,
            members: self.data.members.clone(),
            current_match,
            last_abort_reason: self.data.last_abort_reason,
        }
    }

    fn member_mut(&mut self, account: &str) -> Result<&mut Member, ErrorCode> {
        self.data
            .members
            .iter_mut()
            .find(|m| m.account_id == account)
            .ok_or(ErrorCode::NotInRoom)
    }

    fn into_waiting(mut self, reason: Option<&'static str>) -> Room<Waiting> {
        self.data.last_abort_reason = reason;
        for member in &mut self.data.members {
            member.ready = false;
            member.loaded = false;
        }
        self.data.revision += 1;
        Room {
            data: self.data,
            state: Waiting,
        }
    }
}

impl Room<Waiting> {
    pub(crate) fn new(
        name: String,
        account: &str,
        profile: PlayerProfile,
        address: RoomAddress,
        map: LobbyMap,
        rules: crate::limits::RoomRules,
    ) -> Self {
        Self {
            data: Box::new(RoomData {
                room_id: uuid::Uuid::new_v4().to_string(),
                address,
                map,
                rules,
                name,
                revision: 1,
                owner_account_id: account.to_owned(),
                members: vec![Member {
                    account_id: account.to_owned(),
                    profile,
                    actor_id: 1,
                    camp: 1,
                    ready: false,
                    loaded: false,
                }],
                next_actor_id: 2,
                last_abort_reason: None,
            }),
            state: Waiting,
        }
    }

    pub(crate) fn join(&mut self, account: &str, profile: PlayerProfile) -> Result<(), ErrorCode> {
        if self.data.members.iter().any(|m| m.account_id == account) {
            return Err(ErrorCode::AlreadyInRoom);
        }
        if self.data.members.len() >= self.data.rules.capacity() {
            return Err(ErrorCode::RoomFull);
        }
        let next = self
            .data
            .next_actor_id
            .checked_add(1)
            .ok_or(ErrorCode::RoomFull)?;
        let camp = if self.data.members.iter().filter(|m| m.camp == 1).count()
            > self.data.members.iter().filter(|m| m.camp == 2).count()
        {
            2
        } else {
            1
        };
        self.data.members.push(Member {
            account_id: account.to_owned(),
            profile,
            actor_id: self.data.next_actor_id,
            camp,
            ready: false,
            loaded: false,
        });
        self.data.next_actor_id = next;
        self.clear_ready();
        self.data.revision += 1;
        Ok(())
    }

    pub fn set_camp(&mut self, account: &str, camp: u8) -> Result<(), ErrorCode> {
        if !matches!(camp, 1 | 2) {
            return Err(ErrorCode::InvalidCamp);
        }
        if self
            .data
            .members
            .iter()
            .filter(|m| m.camp == camp && m.account_id != account)
            .count()
            >= self.data.rules.team_capacity()
        {
            return Err(ErrorCode::InvalidTeams);
        }
        let member = self.member_mut(account)?;
        if member.camp != camp {
            member.camp = camp;
            self.clear_ready();
            self.data.revision += 1;
        }
        Ok(())
    }

    pub fn ready(&mut self, account: &str, ready: bool) -> Result<(), ErrorCode> {
        let member = self.member_mut(account)?;
        if member.ready != ready {
            member.ready = ready;
            self.data.revision += 1;
        }
        Ok(())
    }

    pub fn start(mut self, account: &str, now_ms: u64) -> Result<Room<Loading>, (Self, ErrorCode)> {
        // Start confirms the owner's intent; only guests need a separate ready flag.
        let error = if self.data.owner_account_id != account {
            Some(ErrorCode::NotOwner)
        } else if !matches!(self.data.map.value(), 1003 | MAP_ID) {
            Some(ErrorCode::UnsupportedMap)
        } else if self
            .data
            .members
            .iter()
            .any(|member| member.account_id != account && !member.ready)
        {
            Some(ErrorCode::NotReady)
        } else if self.data.members.len() > 1
            && !self
                .data
                .members
                .iter()
                .any(|m| m.camp != self.data.members[0].camp)
        {
            Some(ErrorCode::InvalidTeams)
        } else {
            None
        };
        if let Some(error) = error {
            return Err((self, error));
        }
        self.data.last_abort_reason = None;
        self.data.revision += 1;
        Ok(Room {
            data: self.data,
            state: Loading {
                current_match: Match {
                    match_id: uuid::Uuid::new_v4().to_string(),
                    load_deadline_ms: now_ms.saturating_add(LOAD_TIMEOUT_MS),
                    starts_at_ms: None,
                },
            },
        })
    }

    fn clear_ready(&mut self) {
        for member in &mut self.data.members {
            member.ready = false;
            member.loaded = false;
        }
    }
}

impl Room<Loading> {
    pub fn loaded(&mut self, account: &str, match_id: &str, now_ms: u64) -> Result<(), ErrorCode> {
        if self.state.current_match.match_id != match_id {
            return Err(ErrorCode::StaleMatch);
        }
        if now_ms >= self.state.current_match.load_deadline_ms {
            return Err(ErrorCode::LoadTimeout);
        }
        let member = self.member_mut(account)?;
        if !member.loaded {
            member.loaded = true;
            self.data.revision += 1;
        }
        Ok(())
    }

    pub fn finish_loading(mut self, now_ms: u64) -> Result<Room<Countdown>, Self> {
        if now_ms >= self.state.current_match.load_deadline_ms
            || !self.data.members.iter().all(|m| m.loaded)
        {
            return Err(self);
        }
        let starts_at_ms = now_ms.saturating_add(COUNTDOWN_MS);
        self.state.current_match.starts_at_ms = Some(starts_at_ms);
        self.data.revision += 1;
        Ok(Room {
            data: self.data,
            state: Countdown {
                current_match: self.state.current_match,
                starts_at_ms,
            },
        })
    }
}

impl Room<Countdown> {
    pub fn begin(mut self, now_ms: u64) -> Result<Room<Running>, Self> {
        if now_ms < self.state.starts_at_ms {
            return Err(self);
        }
        self.data.revision += 1;
        let battle = crate::battle::Battle::new(
            self.data.room_id.clone(),
            self.state.current_match.match_id.clone(),
            self.data.members.iter().map(|m| m.actor_id),
        );
        let combat = crate::combat::Combat::new(
            self.data.room_id.clone(),
            self.state.current_match.match_id.clone(),
            self.data.members.iter().map(|m| (m.actor_id, m.camp)),
        );
        let motion = crate::motion::Motion::new(
            self.data.room_id.clone(),
            self.state.current_match.match_id.clone(),
            self.data.members.iter().map(|m| m.actor_id),
        );
        Ok(Room {
            data: self.data,
            state: Running {
                battle: Box::new(battle),
                motion: Box::new(motion),
                combat: Box::new(combat),
                current_match: self.state.current_match,
            },
        })
    }
}

// The directory must store rooms at different phases. This enum owns a typed
// room; it does not allow constructing a phase separately from its data.
pub(crate) enum RoomState {
    Waiting(Room<Waiting>),
    Loading(Room<Loading>),
    Countdown(Room<Countdown>),
    Running(Room<Running>),
}

impl RoomState {
    pub fn pose_input(
        &mut self,
        account: &str,
        match_id: &str,
        input_seq: u32,
        pose: crate::motion::Pose,
        now_ms: u64,
    ) -> Result<(), ErrorCode> {
        let Self::Running(room) = self else {
            return Err(ErrorCode::WrongPhase);
        };
        if room.state.current_match.match_id != match_id {
            return Err(ErrorCode::StaleMatch);
        }
        let actor = room
            .data
            .members
            .iter()
            .find(|m| m.account_id == account)
            .ok_or(ErrorCode::NotInRoom)?;
        if !room.state.combat.alive(actor.actor_id) {
            return Ok(());
        }
        room.state
            .motion
            .input(actor.actor_id, input_seq, pose, now_ms)
    }

    pub fn take_pose_events(&mut self) -> Vec<crate::motion::PoseEvent> {
        match self {
            Self::Running(room) => room.state.motion.take_events(),
            _ => Vec::new(),
        }
    }

    pub fn action_input(
        &mut self,
        account: &str,
        match_id: &str,
        input_seq: u32,
        intent: crate::battle::ActionIntent,
        now_ms: u64,
    ) -> Result<(), ErrorCode> {
        let Self::Running(room) = self else {
            return Err(ErrorCode::WrongPhase);
        };
        if room.state.current_match.match_id != match_id {
            return Err(ErrorCode::StaleMatch);
        }
        let actor = room
            .data
            .members
            .iter()
            .find(|m| m.account_id == account)
            .ok_or(ErrorCode::NotInRoom)?;
        room.state
            .battle
            .input(actor.actor_id, input_seq, intent, now_ms)
    }

    pub fn contact_input(
        &mut self,
        account: &str,
        match_id: &str,
        input_seq: u32,
        report: crate::combat::ContactReport,
        now_ms: u64,
    ) -> Result<bool, ErrorCode> {
        let Self::Running(room) = self else {
            return Err(ErrorCode::WrongPhase);
        };
        if room.state.current_match.match_id != match_id {
            return Err(ErrorCode::StaleMatch);
        }
        let actor = room
            .data
            .members
            .iter()
            .find(|m| m.account_id == account)
            .ok_or(ErrorCode::NotInRoom)?
            .actor_id;
        room.state.combat.input(
            actor,
            input_seq,
            report,
            now_ms,
            &mut room.state.battle,
            &room.state.motion,
        )
    }
    pub fn respawn(&mut self, account: &str, match_id: &str, now_ms: u64) -> Result<(), ErrorCode> {
        let Self::Running(room) = self else {
            return Err(ErrorCode::WrongPhase);
        };
        if room.state.current_match.match_id != match_id {
            return Err(ErrorCode::StaleMatch);
        }
        let actor = room
            .data
            .members
            .iter()
            .find(|m| m.account_id == account)
            .ok_or(ErrorCode::NotInRoom)?
            .actor_id;
        room.state.combat.respawn(
            actor,
            now_ms,
            &mut room.state.battle,
            &mut room.state.motion,
        )
    }
    pub fn take_combat_events(&mut self) -> Vec<crate::combat::CombatEvent> {
        match self {
            Self::Running(room) => room.state.combat.take_events(),
            _ => Vec::new(),
        }
    }
    pub fn take_action_events(&mut self) -> Vec<crate::battle::ActionEvent> {
        match self {
            Self::Running(room) => room.state.battle.take_events(),
            _ => Vec::new(),
        }
    }
    pub fn snapshot(&self) -> RoomSnapshot {
        match self {
            Self::Waiting(room) => room.snapshot(Phase::Waiting, None),
            Self::Loading(room) => {
                room.snapshot(Phase::Loading, Some(room.state.current_match.clone()))
            }
            Self::Countdown(room) => {
                room.snapshot(Phase::Countdown, Some(room.state.current_match.clone()))
            }
            Self::Running(room) => {
                room.snapshot(Phase::Running, Some(room.state.current_match.clone()))
            }
        }
    }

    pub fn waiting_mut(&mut self) -> Result<&mut Room<Waiting>, ErrorCode> {
        match self {
            Self::Waiting(room) => Ok(room),
            _ => Err(ErrorCode::WrongPhase),
        }
    }

    pub fn start(self, account: &str, now_ms: u64) -> Result<Self, (Self, ErrorCode)> {
        match self {
            Self::Waiting(room) => room
                .start(account, now_ms)
                .map(Self::Loading)
                .map_err(|(room, error)| (Self::Waiting(room), error)),
            _ => Err((self, ErrorCode::WrongPhase)),
        }
    }

    pub fn loaded(
        self,
        account: &str,
        match_id: &str,
        now_ms: u64,
    ) -> Result<Self, (Self, ErrorCode)> {
        if self
            .snapshot()
            .current_match
            .as_ref()
            .is_none_or(|m| m.match_id != match_id)
        {
            return Err((self, ErrorCode::StaleMatch));
        }
        match self {
            Self::Loading(mut room) => {
                if let Err(error) = room.loaded(account, match_id, now_ms) {
                    return Err((Self::Loading(room), error));
                }
                Ok(match room.finish_loading(now_ms) {
                    Ok(room) => Self::Countdown(room),
                    Err(room) => Self::Loading(room),
                })
            }
            _ => Err((self, ErrorCode::WrongPhase)),
        }
    }

    pub fn leave(self, account: &str, reason: &'static str) -> Option<Self> {
        let mut room = match self {
            Self::Waiting(room) => {
                let previous = room.data.last_abort_reason;
                room.into_waiting(previous)
            }
            Self::Loading(room) => room.into_waiting(Some(reason)),
            Self::Countdown(room) => room.into_waiting(Some(reason)),
            Self::Running(room) => room.into_waiting(Some(reason)),
        };
        room.data.members.retain(|m| m.account_id != account);
        let first = room.data.members.first()?;
        if room.data.owner_account_id == account {
            room.data.owner_account_id = first.account_id.clone();
        }
        Some(Self::Waiting(room))
    }

    pub fn advance(self, now_ms: u64) -> (Self, bool) {
        match self {
            Self::Loading(room) if now_ms >= room.state.current_match.load_deadline_ms => {
                (Self::Waiting(room.into_waiting(Some("loadTimeout"))), true)
            }
            Self::Countdown(room) => match room.begin(now_ms) {
                Ok(room) => (Self::Running(room), true),
                Err(room) => (Self::Countdown(room), false),
            },
            Self::Running(mut room) => {
                room.state.battle.advance(now_ms);
                room.state.motion.advance(now_ms);
                room.state.combat.advance(now_ms);
                (Self::Running(room), false)
            }
            _ => (self, false),
        }
    }
}
