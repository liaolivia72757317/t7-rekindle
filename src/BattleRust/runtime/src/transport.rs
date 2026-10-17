use std::io::{self, ErrorKind};
use t7r_battle_core::protocol::{FrameDecoder, MAX_FRAME};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{
    TcpStream,
    tcp::{OwnedReadHalf, OwnedWriteHalf},
};

pub struct FramedReader {
    stream: OwnedReadHalf,
    decoder: FrameDecoder,
    finished: bool,
}

pub struct FramedWriter {
    stream: Option<OwnedWriteHalf>,
}

pub fn split(stream: TcpStream) -> io::Result<(FramedReader, FramedWriter)> {
    stream.set_nodelay(true)?;
    let (read, write) = stream.into_split();
    Ok((
        FramedReader {
            stream: read,
            decoder: FrameDecoder::default(),
            finished: false,
        },
        FramedWriter {
            stream: Some(write),
        },
    ))
}

impl FramedReader {
    pub async fn read_frame(&mut self) -> io::Result<Option<Vec<u8>>> {
        if self.finished {
            return Err(io::Error::from(ErrorKind::NotConnected));
        }
        let result = self.read_inner().await;
        if !matches!(result, Ok(Some(_))) {
            self.finished = true;
        }
        result
    }

    async fn read_inner(&mut self) -> io::Result<Option<Vec<u8>>> {
        let mut buffer = [0; 16 * 1024];
        loop {
            if let Some(frame) = self.decoder.next_frame().map_err(invalid_frame)? {
                return Ok(Some(frame));
            }
            let read = self.stream.read(&mut buffer).await?;
            if read == 0 {
                return if self.decoder.buffered_len() == 0 {
                    Ok(None)
                } else {
                    Err(io::Error::from(ErrorKind::UnexpectedEof))
                };
            }
            self.decoder.push(&buffer[..read]).map_err(invalid_frame)?;
        }
    }
}

impl FramedWriter {
    pub async fn send(&mut self, frame: &[u8]) -> io::Result<()> {
        if frame.len() < 12 || frame.len() > MAX_FRAME {
            return Err(io::Error::from(ErrorKind::InvalidInput));
        }
        // Cancellation drops the owned write half. A partial frame is never resumed
        // as if it were a new frame on the same connection.
        let mut stream = self.stream.take().ok_or(ErrorKind::NotConnected)?;
        stream.write_all(frame).await?;
        self.stream = Some(stream);
        Ok(())
    }
}

fn invalid_frame(error: impl Into<Box<dyn std::error::Error + Send + Sync>>) -> io::Error {
    io::Error::new(ErrorKind::InvalidData, error)
}
