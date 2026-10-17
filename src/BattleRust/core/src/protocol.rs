use std::fmt;

pub const MAX_FRAME: usize = 1024 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProtocolError {
    InvalidHeader,
    InvalidLength,
    InvalidAuthentication,
    InvalidHeartbeat,
    InvalidMethod3,
    InvalidPadding,
    InvalidMessage,
    BufferLimit,
}

impl fmt::Display for ProtocolError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::InvalidHeader => "invalid TPDU magic/version",
            Self::InvalidLength => "invalid TPDU length",
            Self::InvalidAuthentication => "unsupported zero-head authentication",
            Self::InvalidHeartbeat => "heartbeat requires selector 1 and sixteen bytes",
            Self::InvalidMethod3 => "unsupported method-3 frame",
            Self::InvalidPadding => "method-3 marker/padding invalid",
            Self::InvalidMessage => "short SH_PACKAGE",
            Self::BufferLimit => "receive buffer exceeds two maximum frames",
        })
    }
}

impl std::error::Error for ProtocolError {}

#[derive(Default)]
pub struct FrameDecoder {
    buffer: Vec<u8>,
    consumed: usize,
}

impl FrameDecoder {
    pub fn buffered_len(&self) -> usize {
        self.buffer.len() - self.consumed
    }

    pub fn push(&mut self, bytes: &[u8]) -> Result<(), ProtocolError> {
        if bytes.len() > MAX_FRAME * 2 - self.buffered_len() {
            return Err(ProtocolError::BufferLimit);
        }
        if self.consumed > 32768 || self.buffer.len() + bytes.len() > MAX_FRAME * 2 {
            self.buffer.drain(..self.consumed);
            self.consumed = 0;
        }
        self.buffer.extend_from_slice(bytes);
        Ok(())
    }

    pub fn next_frame(&mut self) -> Result<Option<Vec<u8>>, ProtocolError> {
        let data = &self.buffer[self.consumed..];
        let Some(length) = frame_length(data)? else {
            return Ok(None);
        };
        if data.len() < length {
            return Ok(None);
        }
        let frame = data[..length].to_vec();
        self.consumed += length;
        if self.consumed == self.buffer.len() {
            self.buffer.clear();
            self.consumed = 0;
        }
        Ok(Some(frame))
    }
}

fn frame_length(data: &[u8]) -> Result<Option<usize>, ProtocolError> {
    if data.len() < 12 {
        return Ok(None);
    }
    if data[..2] != [0x55, 0x0e] {
        return Err(ProtocolError::InvalidHeader);
    }
    let mut header = u32::from_be_bytes(data[4..8].try_into().expect("fixed header")) as u64;
    let body = u32::from_be_bytes(data[8..12].try_into().expect("fixed header")) as u64;
    if header == 0 {
        if data[2] != 3 {
            return Err(ProtocolError::InvalidAuthentication);
        }
        if data.len() < 29 {
            return Ok(None);
        }
        if data[20..24] != [0, 0, 0, 3] {
            return Err(ProtocolError::InvalidAuthentication);
        }
        header = 29 + u64::from(data[28]);
    }
    if header < 12 || header + body > MAX_FRAME as u64 {
        return Err(ProtocolError::InvalidLength);
    }
    Ok(Some((header + body) as usize))
}

pub fn frame_command(frame: &[u8]) -> Result<u8, ProtocolError> {
    if frame_length(frame)? != Some(frame.len()) {
        return Err(ProtocolError::InvalidLength);
    }
    Ok(frame[2])
}

pub fn method3_ciphertext(frame: &[u8]) -> Result<&[u8], ProtocolError> {
    if frame_command(frame)? != 0
        || frame[3..8] != [4, 0, 0, 0, 12]
        || frame.len() < 28
        || !(frame.len() - 12).is_multiple_of(16)
    {
        return Err(ProtocolError::InvalidMethod3);
    }
    Ok(&frame[12..])
}

pub fn strip_method3_padding(data: &mut Vec<u8>) -> Result<(), ProtocolError> {
    if data.len() < 16 || !data.len().is_multiple_of(16) {
        return Err(ProtocolError::InvalidPadding);
    }
    let padding = usize::from(data[data.len() - 1]);
    if padding < 6 || padding >= data.len() || &data[data.len() - 6..data.len() - 1] != b"tsf4g" {
        return Err(ProtocolError::InvalidPadding);
    }
    let payload = data.len() - padding;
    let remainder = payload % 16;
    if padding != (if remainder <= 10 { 16 } else { 32 }) - remainder {
        return Err(ProtocolError::InvalidPadding);
    }
    data.truncate(payload);
    Ok(())
}

#[derive(Debug, PartialEq, Eq)]
pub struct Message {
    pub sequence: u32,
    pub command: u16,
    pub server_time_ms: u64,
    pub body: Vec<u8>,
}

impl Message {
    pub fn from_plaintext(data: &[u8]) -> Result<Self, ProtocolError> {
        if data.len() < 14 {
            return Err(ProtocolError::InvalidMessage);
        }
        Ok(Self {
            sequence: u32::from_be_bytes(data[..4].try_into().expect("sequence")),
            command: u16::from_be_bytes(data[4..6].try_into().expect("command")),
            server_time_ms: u64::from_be_bytes(data[6..14].try_into().expect("timestamp")),
            body: data[14..].to_vec(),
        })
    }
}

// Existing local client handshake. This is fixture protocol material, not an account key.
pub const CHANGE_KEY_FRAME: [u8; 48] = [
    0x55, 0x0e, 1, 0, 0, 0, 0, 0x30, 0, 0, 0, 0, 0, 3, 0, 0x20, 0x7a, 0xca, 0x0f, 0xd9, 0xbc, 0xd6,
    0xec, 0x7c, 0x9f, 0x97, 0x46, 0x66, 0x16, 0xe6, 0xa2, 0x82, 0x6f, 0x42, 0x20, 0xbc, 0x6f, 0xb8,
    0x6f, 0x56, 0x96, 0x0c, 0x05, 0xcd, 0x30, 0xdb, 0xe3, 0xf8,
];

pub fn plain_frame(command: u16, now: u64, body: &[u8]) -> Result<Vec<u8>, ProtocolError> {
    if body.len() > MAX_FRAME - 23 {
        return Err(ProtocolError::InvalidLength);
    }
    let mut result = Vec::with_capacity(23 + body.len());
    result.extend_from_slice(&[0x55, 0x0e, 5, 0]);
    result.extend_from_slice(&12_u32.to_be_bytes());
    result.extend_from_slice(&((body.len() + 11) as u32).to_be_bytes());
    result.push(0);
    result.extend_from_slice(&command.to_be_bytes());
    result.extend_from_slice(&now.to_be_bytes());
    result.extend_from_slice(body);
    Ok(result)
}

pub fn heartbeat(body: &[u8]) -> Result<[u8; 14], ProtocolError> {
    if body.len() != 16 || body[..2] != [0, 1] {
        return Err(ProtocolError::InvalidHeartbeat);
    }
    let mut result = [0; 14];
    result[1] = 2;
    result[2..].copy_from_slice(&body[2..14]);
    Ok(result)
}
