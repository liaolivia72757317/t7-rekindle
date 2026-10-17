//! Public battle transport, independent of the original client's TPDU protocol.
use std::fmt;

pub const PROTOCOL_VERSION: u8 = 1;
pub const CONTRACT_VERSION: u16 = 1;
pub const HEADER_SIZE: usize = 36;
pub const MAX_FRAME_SIZE: usize = 4 * 1024 * 1024;
pub const MAX_TICKET_SIZE: usize = 256;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ContractError {
    Length,
    Header,
    Requirements,
    Ticket,
}

impl fmt::Display for ContractError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::Length => "invalid battle frame length",
            Self::Header => "unsupported battle frame header",
            Self::Requirements => "invalid battle requirements",
            Self::Ticket => "invalid admission ticket length",
        })
    }
}
impl std::error::Error for ContractError {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BattleFrame {
    pub kind: u8,
    pub channel: u8,
    pub room_id: u64,
    pub match_id: u64,
    pub player_id: u64,
    pub sequence: u64,
    pub payload: Vec<u8>,
}

fn valid_header(kind: u8, channel: u8) -> bool {
    (1..=14).contains(&kind) && channel <= 3 && ((3..=5).contains(&kind) == (channel != 0))
}

pub fn decode_length(prefix: &[u8]) -> Result<usize, ContractError> {
    let bytes: [u8; 4] = prefix.try_into().map_err(|_| ContractError::Length)?;
    let size = u32::from_be_bytes(bytes) as usize;
    if !(HEADER_SIZE..=MAX_FRAME_SIZE).contains(&size) {
        return Err(ContractError::Length);
    }
    Ok(size)
}

impl BattleFrame {
    pub fn encode(&self) -> Result<Vec<u8>, ContractError> {
        if self.payload.len() > MAX_FRAME_SIZE - HEADER_SIZE {
            return Err(ContractError::Length);
        }
        if !valid_header(self.kind, self.channel) {
            return Err(ContractError::Header);
        }
        let size = HEADER_SIZE + self.payload.len();
        let mut bytes = Vec::with_capacity(4 + size);
        bytes.extend_from_slice(&(size as u32).to_be_bytes());
        bytes.extend_from_slice(&[PROTOCOL_VERSION, self.kind, self.channel, 0]);
        for value in [self.room_id, self.match_id, self.player_id, self.sequence] {
            bytes.extend_from_slice(&value.to_be_bytes());
        }
        bytes.extend_from_slice(&self.payload);
        Ok(bytes)
    }

    /// Decode a body after validating and reading its bounded length prefix.
    pub fn decode(body: &[u8]) -> Result<Self, ContractError> {
        if !(HEADER_SIZE..=MAX_FRAME_SIZE).contains(&body.len()) {
            return Err(ContractError::Length);
        }
        if body[0] != PROTOCOL_VERSION || body[3] != 0 || !valid_header(body[1], body[2]) {
            return Err(ContractError::Header);
        }
        let read = |offset| {
            u64::from_be_bytes(body[offset..offset + 8].try_into().expect("bounded header"))
        };
        Ok(Self {
            kind: body[1],
            channel: body[2],
            room_id: read(4),
            match_id: read(12),
            player_id: read(20),
            sequence: read(28),
            payload: body[HEADER_SIZE..].to_vec(),
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum AuthorityMode {
    ClientSampled = 1,
    ServerSimulated = 2,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Requirements {
    pub contract_version: u16,
    pub authority_mode: AuthorityMode,
    pub rules_version: u32,
    pub resource_version: u32,
    pub map_id: u32,
}

impl Requirements {
    fn validate(&self) -> Result<(), ContractError> {
        if self.contract_version != CONTRACT_VERSION
            || self.rules_version == 0
            || self.resource_version == 0
            || self.map_id == 0
        {
            return Err(ContractError::Requirements);
        }
        Ok(())
    }

    fn encode(&self, bytes: &mut Vec<u8>) -> Result<(), ContractError> {
        self.validate()?;
        bytes.extend_from_slice(&self.contract_version.to_be_bytes());
        bytes.extend_from_slice(&[self.authority_mode as u8, 0]);
        for value in [self.rules_version, self.resource_version, self.map_id] {
            bytes.extend_from_slice(&value.to_be_bytes());
        }
        Ok(())
    }

    fn decode(bytes: &[u8]) -> Result<Self, ContractError> {
        if bytes.len() != 16 || bytes[3] != 0 {
            return Err(ContractError::Requirements);
        }
        let authority_mode = match bytes[2] {
            1 => AuthorityMode::ClientSampled,
            2 => AuthorityMode::ServerSimulated,
            _ => return Err(ContractError::Requirements),
        };
        let read = |offset| {
            u32::from_be_bytes(
                bytes[offset..offset + 4]
                    .try_into()
                    .expect("bounded requirements"),
            )
        };
        let value = Self {
            contract_version: u16::from_be_bytes([bytes[0], bytes[1]]),
            authority_mode,
            rules_version: read(4),
            resource_version: read(8),
            map_id: read(12),
        };
        value.validate()?;
        Ok(value)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Admission {
    pub requirements: Requirements,
    pub ticket: Vec<u8>,
}

impl Admission {
    pub fn encode(&self) -> Result<Vec<u8>, ContractError> {
        if self.ticket.is_empty() || self.ticket.len() > MAX_TICKET_SIZE {
            return Err(ContractError::Ticket);
        }
        let mut bytes = Vec::with_capacity(18 + self.ticket.len());
        self.requirements.encode(&mut bytes)?;
        bytes.extend_from_slice(&(self.ticket.len() as u16).to_be_bytes());
        bytes.extend_from_slice(&self.ticket);
        Ok(bytes)
    }

    pub fn decode(bytes: &[u8]) -> Result<Self, ContractError> {
        if bytes.len() < 18 {
            return Err(ContractError::Length);
        }
        let length = u16::from_be_bytes([bytes[16], bytes[17]]) as usize;
        if !(1..=MAX_TICKET_SIZE).contains(&length) || bytes.len() != 18 + length {
            return Err(ContractError::Ticket);
        }
        Ok(Self {
            requirements: Requirements::decode(&bytes[..16])?,
            ticket: bytes[18..].to_vec(),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Capabilities {
    requirements: Requirements,
}

impl Capabilities {
    /// This implementation advertises no server-side movement or collision simulation.
    pub fn client_sampled(
        rules_version: u32,
        resource_version: u32,
        map_id: u32,
    ) -> Result<Self, ContractError> {
        let requirements = Requirements {
            contract_version: CONTRACT_VERSION,
            authority_mode: AuthorityMode::ClientSampled,
            rules_version,
            resource_version,
            map_id,
        };
        requirements.validate()?;
        Ok(Self { requirements })
    }

    pub fn supports(&self, requested: &Requirements) -> bool {
        self.requirements == *requested
    }
}
