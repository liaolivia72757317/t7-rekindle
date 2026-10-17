use crate::gbk;
use serde_json::{Value, json};
use std::collections::{HashMap, VecDeque};
use std::io::{self, ErrorKind};
use std::sync::{Arc, Mutex};
use tokio::sync::oneshot;

pub const MAX_PLATFORM_BYTES: usize = 1024 * 1024;
const MAX_REQUEST_BYTES: usize = 4096;
const MAX_PENDING: usize = 32;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Identity {
    pub name: Vec<u8>,
    pub portrait: i32,
    pub level: i32,
}

impl Identity {
    fn parse(value: &Value) -> io::Result<Self> {
        let name = value["displayName"].as_str().ok_or_else(invalid)?;
        let portrait = value["portrait"].as_i64().ok_or_else(invalid)?;
        let level = value["level"].as_i64().ok_or_else(invalid)?;
        if !(0..=i32::MAX as i64).contains(&portrait) || !(1..=i32::MAX as i64).contains(&level) {
            return Err(invalid());
        }
        Ok(Self {
            name: gbk::player_name(name)?,
            portrait: portrait as i32,
            level: level as i32,
        })
    }
}

struct Pending {
    operation: &'static str,
    bytes: Vec<u8>,
    sender: oneshot::Sender<io::Result<Value>>,
}

pub struct PlatformMailbox {
    pub(super) titles: super::titles::Titles,
    epoch: u64,
    next_id: u64,
    pending: HashMap<u64, Pending>,
    ready: VecDeque<u64>,
}

impl PlatformMailbox {
    pub fn new(epoch: u64) -> Self {
        Self {
            epoch,
            titles: super::titles::Titles::default(),
            next_id: 1,
            pending: HashMap::new(),
            ready: VecDeque::new(),
        }
    }

    fn request(
        &mut self,
        operation: &'static str,
        mut payload: Value,
    ) -> io::Result<(u64, oneshot::Receiver<io::Result<Value>>)> {
        if self.pending.len() >= MAX_PENDING {
            return Err(io::Error::new(
                ErrorKind::WouldBlock,
                "platform request queue full",
            ));
        }
        let id = self.next_id;
        self.next_id = id.checked_add(1).ok_or_else(invalid)?;
        let (sender, receiver) = oneshot::channel();
        let fields = payload.as_object_mut().ok_or_else(invalid)?;
        fields.insert("version".into(), json!(1));
        fields.insert("epoch".into(), json!(self.epoch));
        fields.insert("requestId".into(), json!(id));
        fields.insert("operation".into(), json!(operation));
        let bytes = serde_json::to_vec(&payload)?;
        if bytes.len() > MAX_REQUEST_BYTES {
            return Err(invalid());
        }
        self.pending.insert(
            id,
            Pending {
                operation,
                bytes,
                sender,
            },
        );
        self.ready.push_back(id);
        Ok((id, receiver))
    }

    // A size probe does not consume the request. Each delivered request remains
    // pending until completion, timeout or connection shutdown.
    pub fn read(&mut self, capacity: usize) -> Result<Option<Vec<u8>>, usize> {
        let Some(id) = self.ready.front() else {
            return Ok(None);
        };
        let request = self.pending.get(id).expect("queued request is pending");
        if capacity < request.bytes.len() {
            return Err(request.bytes.len());
        }
        let bytes = request.bytes.clone();
        self.ready.pop_front();
        Ok(Some(bytes))
    }

    pub fn complete(&mut self, bytes: &[u8]) -> io::Result<bool> {
        if bytes.len() > MAX_PLATFORM_BYTES {
            return Err(invalid());
        }
        let response: Value = serde_json::from_slice(bytes).map_err(|_| invalid())?;
        if response["version"].as_u64() != Some(1) {
            return Err(invalid());
        }
        let epoch = response["epoch"].as_u64().ok_or_else(invalid)?;
        let id = response["requestId"].as_u64().ok_or_else(invalid)?;
        if epoch != self.epoch || !self.pending.contains_key(&id) {
            return Ok(false);
        }
        let result = match response["success"].as_bool() {
            Some(true) => {
                if self.pending[&id].operation == "readIdentity" {
                    Identity::parse(&response["identity"])?;
                }
                Ok(response)
            }
            Some(false) => Ok(response),
            None => return Err(invalid()),
        };
        let pending = self.pending.remove(&id).expect("validated request");
        self.ready.retain(|queued| *queued != id);
        Ok(pending.sender.send(result).is_ok())
    }

    pub fn cancel(&mut self, id: u64) {
        self.pending.remove(&id);
        self.ready.retain(|queued| *queued != id);
    }

    pub fn clear(&mut self) {
        self.pending.clear();
        self.ready.clear();
    }
    pub fn pending_count(&self) -> usize {
        self.pending.len()
    }
}

pub async fn read_identity(mailbox: Arc<Mutex<PlatformMailbox>>) -> io::Result<Identity> {
    let response = profile_request(mailbox, "readIdentity", json!({})).await?;
    if response["success"] != true {
        return Err(io::Error::other("platform identity request failed"));
    }
    Identity::parse(&response["identity"])
}

pub(super) async fn profile_request(
    mailbox: Arc<Mutex<PlatformMailbox>>,
    operation: &'static str,
    payload: Value,
) -> io::Result<Value> {
    let (id, response) = mailbox
        .lock()
        .map_err(|_| invalid())?
        .request(operation, payload)?;
    let _pending = RequestGuard { mailbox, id };
    let seconds = if operation == "prepareMatch" { 60 } else { 5 };
    tokio::time::timeout(std::time::Duration::from_secs(seconds), response)
        .await
        .map_err(|_| io::Error::new(ErrorKind::TimedOut, "platform request timed out"))?
        .map_err(|_| io::Error::new(ErrorKind::Interrupted, "platform request cancelled"))?
}

struct RequestGuard {
    mailbox: Arc<Mutex<PlatformMailbox>>,
    id: u64,
}
impl Drop for RequestGuard {
    fn drop(&mut self) {
        // Poison is surfaced by the host API; cleanup still releases all waiters.
        self.mailbox
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .cancel(self.id);
    }
}

fn invalid() -> io::Error {
    io::Error::new(ErrorKind::InvalidData, "invalid platform response")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn platform_mailbox_bounds_probes_cancellation_and_reuse() {
        let mut mailbox = PlatformMailbox::new(7);
        let pending: Vec<_> = (0..MAX_PENDING)
            .map(|_| mailbox.request("readIdentity", json!({})).unwrap())
            .collect();
        assert_eq!(
            mailbox
                .request("readIdentity", json!({}))
                .unwrap_err()
                .kind(),
            ErrorKind::WouldBlock
        );
        let size = mailbox.read(0).unwrap_err();
        assert!(size < MAX_PLATFORM_BYTES);
        assert_eq!(mailbox.read(size - 1), Err(size));
        let request: Value = serde_json::from_slice(&mailbox.read(size).unwrap().unwrap()).unwrap();
        assert_eq!(request["requestId"], 1);
        assert_eq!(mailbox.pending_count(), MAX_PENDING);
        mailbox.cancel(pending[0].0);
        assert_eq!(mailbox.request("readIdentity", json!({})).unwrap().0, 33);
        mailbox.clear();
        assert_eq!(mailbox.pending_count(), 0);
        assert_eq!(mailbox.read(MAX_PLATFORM_BYTES).unwrap(), None);
    }

    #[test]
    fn malformed_responses_leave_pending_requests_intact() {
        let mut mailbox = PlatformMailbox::new(7);
        let (_id, _receiver) = mailbox.request("readIdentity", json!({})).unwrap();
        for bytes in [
            vec![],
            b"null".to_vec(),
            b"{\"version\":2}".to_vec(),
            vec![b' '; MAX_PLATFORM_BYTES + 1],
        ] {
            assert!(mailbox.complete(&bytes).is_err());
            assert_eq!(mailbox.pending_count(), 1);
        }
        for name in [
            "",
            "\n",
            "😀",
            "€",
            "\u{e001}",
            "abcdefghijklmnopqrstuvwxzy0123456789",
        ] {
            let response = json!({"version":1,"epoch":7,"requestId":1,"success":true,
                "identity":{"displayName":name,"portrait":7,"level":100}});
            assert!(
                mailbox
                    .complete(&serde_json::to_vec(&response).unwrap())
                    .is_err()
            );
            assert_eq!(mailbox.pending_count(), 1);
        }
        let response = json!({"version":1,"epoch":7,"requestId":1,"success":false});
        assert!(
            mailbox
                .complete(&serde_json::to_vec(&response).unwrap())
                .unwrap()
        );
        assert_eq!(mailbox.pending_count(), 0);
    }

    #[test]
    fn full_profile_responses_have_a_separate_bound_from_small_requests() {
        let mut mailbox = PlatformMailbox::new(1);
        let (id, mut receiver) = mailbox.request("readProfile", json!({})).unwrap();
        let weapons: serde_json::Map<String, Value> = (1101..1601)
            .map(|hero| (hero.to_string(), json!([{"slot":1,"group":0,"index":0}])))
            .collect();
        let response = json!({"version":1,"epoch":1,"requestId":id,"success":true,
            "profile":{"schemaVersion":1,"revision":9007199254740993_u64,
                "formation":[1101],"weapons":weapons}});
        let bytes = serde_json::to_vec(&response).unwrap();
        assert!(bytes.len() > MAX_REQUEST_BYTES && bytes.len() < MAX_PLATFORM_BYTES);
        assert!(mailbox.complete(&bytes).unwrap());
        assert_eq!(receiver.try_recv().unwrap().unwrap(), response);
        assert_eq!(mailbox.pending_count(), 0);
        assert!(
            mailbox
                .request(
                    "setFormation",
                    json!({"formation":vec![1101; MAX_REQUEST_BYTES]})
                )
                .is_err()
        );
        assert_eq!(mailbox.pending_count(), 0);
    }
}
