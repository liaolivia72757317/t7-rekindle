use crate::method3::{Cipher, CryptoError};
use std::fmt;
use t7r_battle_core::protocol::{
    CHANGE_KEY_FRAME, Message, ProtocolError, frame_command, method3_ciphertext,
    strip_method3_padding,
};

#[derive(Debug)]
pub enum ReceiveError {
    Protocol(ProtocolError),
    Crypto(CryptoError),
    Closed,
}

impl fmt::Display for ReceiveError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Protocol(error) => error.fmt(formatter),
            Self::Crypto(error) => error.fmt(formatter),
            Self::Closed => formatter.write_str("connection codec is closed"),
        }
    }
}

impl std::error::Error for ReceiveError {}

impl From<ProtocolError> for ReceiveError {
    fn from(error: ProtocolError) -> Self {
        Self::Protocol(error)
    }
}

impl From<CryptoError> for ReceiveError {
    fn from(error: CryptoError) -> Self {
        Self::Crypto(error)
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum ClientEvent {
    Authenticated { reply: [u8; 48] },
    Message(Message),
    Closed,
}

#[derive(Default)]
pub struct ClientCodec {
    authenticated: bool,
    closed: bool,
    cipher: Option<Cipher>,
}

impl ClientCodec {
    pub fn receive(&mut self, frame: &[u8]) -> Result<ClientEvent, ReceiveError> {
        if self.closed {
            return Err(ReceiveError::Closed);
        }
        let result = self.receive_inner(frame);
        if result.is_err() || matches!(result, Ok(ClientEvent::Closed)) {
            self.closed = true;
            self.cipher = None;
        }
        result
    }

    fn receive_inner(&mut self, frame: &[u8]) -> Result<ClientEvent, ReceiveError> {
        let command = frame_command(frame)?;
        if !self.authenticated {
            if command != 3 {
                return Err(ProtocolError::InvalidAuthentication.into());
            }
            self.authenticated = true;
            return Ok(ClientEvent::Authenticated {
                reply: CHANGE_KEY_FRAME,
            });
        }
        if command == 13 {
            return Ok(ClientEvent::Closed);
        }
        let ciphertext = method3_ciphertext(frame)?;
        if self.cipher.is_none() {
            self.cipher = Some(Cipher::new()?);
        }
        let mut clear = self
            .cipher
            .as_mut()
            .expect("initialized cipher")
            .decrypt(ciphertext)?;
        strip_method3_padding(&mut clear)?;
        Ok(ClientEvent::Message(Message::from_plaintext(&clear)?))
    }
}
