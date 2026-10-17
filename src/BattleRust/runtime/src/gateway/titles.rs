use super::{
    connection,
    platform::{PlatformMailbox, read_identity},
    poison,
};
use crate::mailbox::{self, BudgetReceiver, BudgetSender};
use serde_json::Value;
use std::{
    collections::BTreeMap,
    io,
    sync::{Arc, Mutex},
    time::Duration,
};
use t7r_battle_core::protocol::Message;
use tokio::time::Instant;

const USER_ID: u32 = 10000;
const COMMAND: u16 = 31;

#[derive(Clone, Default, Debug, PartialEq, Eq)]
struct Catalogue {
    epoch: u64,
    owned: Vec<u32>,
    equipped: u32,
}

#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub struct TitleRequest {
    pub request_id: u64,
    pub epoch: u64,
    pub title_id: u32,
}

enum Event {
    Catalogue(Catalogue),
    Result(bool),
    Deadline(u64, Instant),
}
struct Pending {
    request: TitleRequest,
    connection: u64,
}

#[derive(Default)]
pub(super) struct Titles {
    catalogue: Catalogue,
    next_id: u64,
    clients: BTreeMap<u64, BudgetSender<Event>>,
    pending: Option<Pending>,
}

impl Titles {
    fn next(&mut self) -> io::Result<u64> {
        self.next_id = self.next_id.checked_add(1).ok_or_else(invalid)?;
        Ok(self.next_id)
    }

    fn subscribe(&mut self) -> io::Result<(u64, BudgetReceiver<Event>)> {
        let id = self.next()?;
        let (tx, rx) = mailbox::bounded(32, 32768).map_err(io::Error::other)?;
        self.clients.insert(id, tx);
        if self.catalogue.epoch != 0 {
            self.send(id, Event::Catalogue(self.catalogue.clone()));
        }
        Ok((id, rx))
    }

    fn disconnect(&mut self, id: u64) {
        self.clients.remove(&id);
        if self.pending.as_ref().is_some_and(|p| p.connection == id) {
            self.pending = None;
        }
    }

    fn send(&mut self, id: u64, event: Event) {
        if self
            .clients
            .get(&id)
            .is_some_and(|tx| tx.try_send(event, 512).is_err())
        {
            // A lagging connection loses its subscription, not another client's update.
            self.disconnect(id);
        }
    }

    pub(super) fn publish(
        &mut self,
        epoch: u64,
        json: &[u8],
        request_id: u64,
        success: bool,
    ) -> io::Result<()> {
        let current = parse(epoch, json)?;
        if epoch < self.catalogue.epoch {
            return Ok(());
        }
        if self
            .pending
            .as_ref()
            .is_some_and(|p| p.request.epoch != epoch || p.request.request_id == request_id)
        {
            let pending = self.pending.take().expect("pending was checked");
            self.send(
                pending.connection,
                Event::Result(pending.request.epoch == epoch && success),
            );
        }
        self.catalogue = current;
        for id in self.clients.keys().copied().collect::<Vec<_>>() {
            self.send(id, Event::Catalogue(self.catalogue.clone()));
        }
        Ok(())
    }

    pub(super) fn request(&self) -> TitleRequest {
        self.pending
            .as_ref()
            .map_or(TitleRequest::default(), |p| p.request)
    }

    fn equip(&mut self, connection: u64, title_id: u32) -> io::Result<()> {
        if self.catalogue.epoch == 0
            || self.pending.is_some()
            || title_id != 0 && !self.catalogue.owned.contains(&title_id)
        {
            self.send(connection, Event::Result(false));
            return Ok(());
        }
        let request = TitleRequest {
            request_id: self.next()?,
            epoch: self.catalogue.epoch,
            title_id,
        };
        self.pending = Some(Pending {
            request,
            connection,
        });
        self.send(
            connection,
            Event::Deadline(request.request_id, Instant::now() + Duration::from_secs(10)),
        );
        Ok(())
    }

    fn expire(&mut self, id: u64) {
        if self
            .pending
            .as_ref()
            .is_some_and(|p| p.request.request_id == id)
        {
            let pending = self.pending.take().expect("pending was checked");
            self.send(pending.connection, Event::Result(false));
        }
    }
}

fn parse(epoch: u64, json: &[u8]) -> io::Result<Catalogue> {
    if epoch == 0 || json.len() > 1024 * 1024 {
        return Err(invalid());
    }
    let value: Value = serde_json::from_slice(json)?;
    let items = value["titles"].as_array().ok_or_else(invalid)?;
    if items.len() > 64 {
        return Err(invalid());
    }
    let mut ids = std::collections::BTreeSet::new();
    let mut owned = Vec::new();
    for item in items {
        let id = item["titleId"].as_u64().ok_or_else(invalid)?;
        if id == 0 || id > i32::MAX as u64 || !ids.insert(id) {
            return Err(invalid());
        }
        let enabled = item["enabled"].as_bool().ok_or_else(invalid)?;
        let is_owned = item["owned"].as_bool().ok_or_else(invalid)?;
        if enabled && is_owned {
            owned.push(id as u32);
        }
    }
    let equipped = match value.get("equippedTitleId").ok_or_else(invalid)? {
        Value::Null => 0,
        value => u32::try_from(value.as_u64().ok_or_else(invalid)?).map_err(|_| invalid())?,
    };
    if equipped != 0 && !owned.contains(&equipped) {
        return Err(invalid());
    }
    Ok(Catalogue {
        epoch,
        owned,
        equipped,
    })
}

pub(super) struct Subscription {
    requests: Arc<Mutex<PlatformMailbox>>,
    pub(super) id: u64,
    events: BudgetReceiver<Event>,
}

impl Subscription {
    pub(super) fn new(requests: Arc<Mutex<PlatformMailbox>>) -> io::Result<Self> {
        let (id, events) = requests.lock().map_err(poison)?.titles.subscribe()?;
        Ok(Self {
            requests,
            id,
            events,
        })
    }

    pub(super) async fn run(mut self, outgoing: BudgetSender<Vec<u8>>) -> io::Result<()> {
        let mut identity = None;
        let mut deadline = None;
        loop {
            let event = if let Some((id, due)) = deadline {
                match tokio::time::timeout_at(due, self.events.recv()).await {
                    Ok(event) => event,
                    Err(_) => {
                        self.requests.lock().map_err(poison)?.titles.expire(id);
                        deadline = None;
                        continue;
                    }
                }
            } else {
                self.events.recv().await
            };
            match event {
                Some(Event::Catalogue(catalogue)) => {
                    if identity.is_none() {
                        identity = Some(read_identity(self.requests.clone()).await?);
                    }
                    let identity = identity.as_ref().expect("identity was loaded");
                    connection::send(
                        &outgoing,
                        COMMAND,
                        &user_info(&identity.name, USER_ID, &catalogue),
                    )
                    .await?;
                    connection::send(&outgoing, COMMAND, &stat_info(USER_ID, &catalogue)).await?;
                }
                Some(Event::Result(success)) => {
                    if deadline.is_some_and(|(id, _)| {
                        self.requests
                            .lock()
                            .unwrap_or_else(|e| e.into_inner())
                            .titles
                            .request()
                            .request_id
                            != id
                    }) {
                        deadline = None;
                    }
                    connection::send(&outgoing, COMMAND, &set_result(success)).await?;
                }
                Some(Event::Deadline(id, due)) => deadline = Some((id, due)),
                None => {
                    return Err(io::Error::new(
                        io::ErrorKind::BrokenPipe,
                        "title subscription closed",
                    ));
                }
            }
        }
    }
}

impl Drop for Subscription {
    fn drop(&mut self) {
        self.requests
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .titles
            .disconnect(self.id);
    }
}

pub(super) async fn reply(
    message: &Message,
    connection: u64,
    requests: Arc<Mutex<PlatformMailbox>>,
    outgoing: &BudgetSender<Vec<u8>>,
) -> io::Result<bool> {
    if message.command != COMMAND || message.body.len() < 2 {
        return Ok(false);
    }
    let selector = u16::from_be_bytes([message.body[0], message.body[1]]);
    if !matches!(selector, 1 | 3 | 5) {
        return Ok(false);
    }
    if message.body.len() != 6 {
        return Err(invalid());
    }
    let value = u32::from_be_bytes(message.body[2..6].try_into().map_err(|_| invalid())?);
    if selector == 5 {
        requests
            .lock()
            .map_err(poison)?
            .titles
            .equip(connection, value)?;
    } else {
        let catalogue = requests.lock().map_err(poison)?.titles.catalogue.clone();
        let body = if selector == 1 {
            let identity = read_identity(requests).await?;
            user_info(&identity.name, value, &catalogue)
        } else {
            stat_info(value, &catalogue)
        };
        connection::send(outgoing, COMMAND, &body).await?;
    }
    Ok(true)
}

fn basic(equipped: u32) -> Vec<u8> {
    let mut data = vec![0; 296];
    data[..4].copy_from_slice(&7_u32.to_be_bytes());
    data[4..8].copy_from_slice(&100_u32.to_be_bytes());
    data[64..68].copy_from_slice(&equipped.to_be_bytes());
    data
}
fn header(selector: u16, user: u32) -> Vec<u8> {
    let mut data = selector.to_be_bytes().to_vec();
    data.extend_from_slice(&u32::from(user != USER_ID).to_be_bytes());
    data.extend_from_slice(&user.to_be_bytes());
    data
}
fn user_info(name: &[u8], user: u32, catalogue: &Catalogue) -> Vec<u8> {
    let name = if user == USER_ID { name } else { &[] };
    let mut data = header(2, user);
    data.extend_from_slice(&((name.len() + 1) as u32).to_be_bytes());
    data.extend_from_slice(name);
    data.push(0);
    data.extend(basic(if user == USER_ID {
        catalogue.equipped
    } else {
        0
    }));
    data
}
fn stat_info(user: u32, catalogue: &Catalogue) -> Vec<u8> {
    let owned = if user == USER_ID {
        catalogue.owned.as_slice()
    } else {
        &[]
    };
    let mut data = header(4, user);
    data.extend(basic(if user == USER_ID {
        catalogue.equipped
    } else {
        0
    }));
    data.resize(data.len() + 4460, 0);
    data.extend_from_slice(&(owned.len() as u32).to_be_bytes());
    for id in owned {
        data.extend_from_slice(&id.to_be_bytes());
    }
    data.resize(data.len() + 90, 0);
    data
}
fn set_result(success: bool) -> Vec<u8> {
    let mut data = 6_u16.to_be_bytes().to_vec();
    data.extend_from_slice(&(if success { 0_u32 } else { 570005_u32 }).to_be_bytes());
    data
}
fn invalid() -> io::Error {
    io::Error::new(
        io::ErrorKind::InvalidData,
        "invalid account title state or request",
    )
}

#[cfg(test)]
#[path = "title_tests.rs"]
mod tests;
