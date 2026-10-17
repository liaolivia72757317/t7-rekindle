mod instance;
use instance::serve_instance;

use super::{
    Diagnostics, compat,
    instance::{InstanceIngress, InstanceOutputBatch},
    leave_wire, loadout, movement_report,
    platform::{Identity, PlatformMailbox, read_identity},
    poison,
    refresh::SceneRefresh,
    room::LobbyRoom,
    scene_actor_wire, scene_entry_wire, scene_wire, squad_wire,
    trace::{GatewayTrace, UnhandledCommands},
};
use crate::{
    battle_session::{EndReason, Life, Settlement},
    client_battle_wire::{self, ClientBattleMessage},
    client_codec::{ClientCodec, ClientEvent},
    mailbox, transport,
};
use std::{
    future::{Future, poll_fn},
    io,
    pin::pin,
    sync::{Arc, Mutex},
    task::Poll,
    time::{Duration, Instant},
};
use t7r_battle_core::protocol::{Message, plain_frame};
use tokio::{
    net::{TcpListener, TcpStream},
    sync::watch,
    task::JoinSet,
};

#[cfg(test)]
#[path = "connection_tests.rs"]
mod tests;

#[derive(Clone)]
pub(super) struct Channel {
    pub(super) role: usize,
    pub(super) instance_port: u16,
    pub(super) trace: Option<Arc<GatewayTrace>>,
}

pub(super) async fn accept(
    listener: TcpListener,
    channel: Channel,
    catalogue: Arc<loadout::Catalogue>,
    mailbox: Arc<Mutex<PlatformMailbox>>,
    instance_rx: watch::Receiver<Option<Arc<InstanceIngress>>>,
    diagnostics: Arc<Mutex<Diagnostics>>,
) -> io::Result<()> {
    let mut tasks = JoinSet::new();
    loop {
        let (stream, _) = listener.accept().await?;
        while let Some(result) = tasks.try_join_next() {
            result.map_err(io::Error::other)?;
        }
        {
            let mut state = diagnostics.lock().map_err(poison)?;
            if state.active >= 32 {
                state.record("client connection limit reached");
                continue;
            }
            state.active += 1;
        }
        let owner = ConnectionGuard(diagnostics.clone());
        let requests = mailbox.clone();
        let catalogue = catalogue.clone();
        let instance_rx = instance_rx.clone();
        let connection_channel = channel.clone();
        let channel_trace = channel.trace.clone();
        let role = channel.role;
        tasks.spawn_local(async move {
            let result = serve(stream, connection_channel, catalogue, requests, instance_rx).await;
            if let Err(error) = &result {
                owner
                    .0
                    .lock()
                    .unwrap_or_else(|error| error.into_inner())
                    .record(error);
            }
            if let Some(trace) = &channel_trace
                && let Err(error) = trace.closed(role, result.as_ref().err())
            {
                owner
                    .0
                    .lock()
                    .unwrap_or_else(|error| error.into_inner())
                    .record(error);
            }
        });
    }
}

struct ConnectionGuard(Arc<Mutex<Diagnostics>>);
impl Drop for ConnectionGuard {
    fn drop(&mut self) {
        self.0
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .active -= 1;
    }
}

async fn serve(
    stream: TcpStream,
    channel: Channel,
    catalogue: Arc<loadout::Catalogue>,
    requests: Arc<Mutex<PlatformMailbox>>,
    instance_rx: watch::Receiver<Option<Arc<InstanceIngress>>>,
) -> io::Result<()> {
    let Channel {
        role,
        instance_port,
        trace,
    } = channel;
    if let Some(trace) = &trace {
        trace.event(role, "connected", 0, &[])?;
    }
    let (mut reader, mut writer) = transport::split(stream)?;
    let mut codec = ClientCodec::default();
    let auth = tokio::time::timeout(Duration::from_secs(5), reader.read_frame())
        .await
        .map_err(|_| {
            io::Error::new(io::ErrorKind::TimedOut, "client authentication timed out")
        })??;
    let Some(auth) = auth else { return Ok(()) };
    let ClientEvent::Authenticated { reply } = codec.receive(&auth).map_err(io::Error::other)?
    else {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "expected client authentication",
        ));
    };
    writer.send(&reply).await?;
    if let Some(trace) = &trace {
        trace.event(role, "authenticated", 0, &[])?;
    }
    if role == 2 {
        return serve_instance(
            reader,
            writer,
            codec,
            instance_rx,
            requests,
            catalogue,
            trace,
        )
        .await;
    }
    let (outgoing, mut frames) =
        mailbox::bounded::<Vec<u8>>(16, 4 * 1024 * 1024).map_err(io::Error::other)?;
    let mut tasks = JoinSet::new();
    tasks.spawn_local(async move {
        while let Some(frame) = frames.recv().await {
            tokio::time::timeout(Duration::from_secs(5), writer.send(&frame))
                .await
                .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "client write timed out"))??;
        }
        Ok(())
    });
    let (titles_ready, titles_started) = tokio::sync::oneshot::channel();
    let startup = outgoing.clone();
    let identity_requests = requests.clone();
    tasks.spawn_local(async move {
        tokio::time::sleep(Duration::from_secs(1)).await;
        if role == 0 {
            send(&startup, 1, &[0, 0x65, 0, 0, 0, 0]).await?;
        } else {
            let identity = read_identity(identity_requests).await?;
            send(&startup, 1, &compat::login(&identity)).await?;
            tokio::time::sleep(Duration::from_secs(1)).await;
            send(&startup, 0x2c, &compat::sync_login()).await?;
        }
        let _ = titles_ready.send(());
        // Startup completion must not close a live client connection.
        std::future::pending::<io::Result<()>>().await
    });
    let (incoming, mut messages) =
        mailbox::bounded::<Message>(32, 1024 * 1024).map_err(io::Error::other)?;
    tasks.spawn_local(async move {
        while let Some(frame) = reader.read_frame().await? {
            match codec.receive(&frame).map_err(io::Error::other)? {
                ClientEvent::Message(message) => {
                    let bytes = message.body.len() + 14;
                    send_queued(&incoming, message, bytes, "client receive queue").await?;
                }
                ClientEvent::Closed => return Ok(()),
                ClientEvent::Authenticated { .. } => {
                    return Err(io::Error::other("duplicate authentication"));
                }
            }
        }
        Ok(())
    });
    let title_connection = if role == 1 {
        let subscription = super::titles::Subscription::new(requests.clone())?;
        let id = subscription.id;
        let title_outgoing = outgoing.clone();
        tasks.spawn_local(async move {
            titles_started.await.map_err(io::Error::other)?;
            subscription.run(title_outgoing).await
        });
        id
    } else {
        0
    };
    tasks.spawn_local(async move {
        let mut unify_at = None;
        let mut room = LobbyRoom::new();
        let mut unhandled = UnhandledCommands::default();
        loop {
            let next = if let Some(due) = room.next_due() {
                let mut receive = pin!(messages.recv());
                let mut timer = pin!(tokio::time::sleep_until(due.into()));
                poll_fn(|context| {
                    if timer.as_mut().poll(context).is_ready() {
                        Poll::Ready(None)
                    } else {
                        receive.as_mut().poll(context).map(Some)
                    }
                })
                .await
            } else {
                Some(messages.recv().await)
            };
            let Some(message) = next else {
                if let Some((command, body)) =
                    room.due(Instant::now(), instance_rx.borrow().as_ref())?
                {
                    if let Some(trace) = &trace {
                        trace.event(role, "notify", command, &body)?;
                    }
                    send(&outgoing, command, &body).await?;
                }
                continue;
            };
            let Some(message) = message else {
                return Ok(());
            };
            if let Some(trace) = &trace {
                trace.event(role, "uplink", message.command, &message.body)?;
            }
            let now = scene_wire::native_uptime_ms();
            if role == 1 && message.command == 0x37 && message.body.starts_with(&[0, 1]) {
                let reply = leave_wire::user_group_quit_response(&message.body)?;
                send(&outgoing, reply.command, &reply.body).await?;
                room.acknowledge_quit(instance_rx.borrow().as_ref());
            } else if let Some(body) = compat::reply(&message, role == 1, now, &mut unify_at)? {
                send(&outgoing, message.command, &body).await?;
            } else if message.command == 8 && message.body.starts_with(&[0, 9]) {
                // A validated, throttled clock request requires no response.
            } else if role == 1 {
                if super::titles::reply(&message, title_connection, requests.clone(), &outgoing)
                    .await?
                {
                    continue;
                }
                if let Some(replies) =
                    loadout::reply(&message, &catalogue, requests.clone()).await?
                {
                    for (command, body) in replies {
                        send(&outgoing, command, &body).await?;
                    }
                } else {
                    if message.command == 0x1e && message.body.starts_with(&[0, 1]) {
                        super::match_selection::prepare_training(
                            &message.body,
                            requests.clone(),
                            &instance_rx,
                        )
                        .await?;
                    }
                    if message.command == 0x20
                        && message.body.starts_with(&[0, 1])
                        && let Some(body) = super::match_selection::prepare(
                            &message.body,
                            requests.clone(),
                            &instance_rx,
                        )
                        .await?
                    {
                        send(&outgoing, 0x20, &body).await?;
                        continue;
                    }
                    if let Some(replies) = room.reply(
                        message.command,
                        &message.body,
                        instance_rx.borrow().as_ref(),
                        instance_port,
                        Instant::now(),
                    )? {
                        for (command, body) in replies {
                            if let Some(trace) = &trace {
                                trace.event(role, "room-reply", command, &body)?;
                            }
                            send(&outgoing, command, &body).await?;
                        }
                    } else {
                        unhandled.record(trace.as_deref(), role, &message)?;
                    }
                }
            } else {
                unhandled.record(trace.as_deref(), role, &message)?;
            }
        }
    });
    let result = tasks
        .join_next()
        .await
        .expect("connection tasks")
        .map_err(io::Error::other)?;
    tasks.abort_all();
    while tasks.join_next().await.is_some() {}
    result
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum RespawnPhase {
    Playing,
    Dead,
    Selecting,
    Waiting,
    Spawning,
}

async fn send_instance_messages(
    writer: &mut transport::FramedWriter,
    replies: Vec<ClientBattleMessage>,
) -> io::Result<()> {
    for reply in replies {
        let response = downlink_frame(reply.command, &reply.body)?;
        tokio::time::timeout(Duration::from_secs(5), writer.send(&response))
            .await
            .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "instance reply timed out"))??;
    }
    Ok(())
}

enum InstanceEvent {
    Changed(Result<(), watch::error::RecvError>),
    Output(Option<InstanceOutputBatch>),
    Input(io::Result<Option<Vec<u8>>>),
}

async fn next_instance_event(
    active: &mut watch::Receiver<Option<Arc<InstanceIngress>>>,
    outgoing: &mut mailbox::BudgetReceiver<InstanceOutputBatch>,
    reader: &mut transport::FramedReader,
    prefer_output: bool,
) -> InstanceEvent {
    let mut changed = pin!(active.changed());
    let mut output = pin!(outgoing.recv());
    let mut input = pin!(reader.read_frame());
    poll_fn(|context| {
        if let Poll::Ready(result) = changed.as_mut().poll(context) {
            return Poll::Ready(InstanceEvent::Changed(result));
        }
        if prefer_output {
            if let Poll::Ready(batch) = output.as_mut().poll(context) {
                return Poll::Ready(InstanceEvent::Output(batch));
            }
            input.as_mut().poll(context).map(InstanceEvent::Input)
        } else {
            if let Poll::Ready(frame) = input.as_mut().poll(context) {
                return Poll::Ready(InstanceEvent::Input(frame));
            }
            output.as_mut().poll(context).map(InstanceEvent::Output)
        }
    })
    .await
}

pub(super) async fn send(
    sender: &mailbox::BudgetSender<Vec<u8>>,
    command: u16,
    body: &[u8],
) -> io::Result<()> {
    let frame = downlink_frame(command, body)?;
    let bytes = frame.len();
    send_queued(sender, frame, bytes, "client send queue").await
}

fn downlink_frame(command: u16, body: &[u8]) -> io::Result<Vec<u8>> {
    // Matches NativeBridge Server::commit: the outer server_time is zero;
    // native and match clocks belong only to the corresponding body fields.
    plain_frame(command, 0, body).map_err(io::Error::other)
}

async fn send_queued<T>(
    sender: &mailbox::BudgetSender<T>,
    value: T,
    bytes: usize,
    queue: &'static str,
) -> io::Result<()> {
    // A TCP burst waits for bounded capacity; a stalled peer still closes the
    // connection instead of accumulating work or silently dropping messages.
    tokio::time::timeout(Duration::from_secs(5), sender.send(value, bytes))
        .await
        .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, format!("{queue} timed out")))?
        .map_err(|error| {
            let (kind, reason) = match error {
                mailbox::QueueError::Full(_) => (io::ErrorKind::WouldBlock, "full"),
                mailbox::QueueError::Closed(_) => (io::ErrorKind::BrokenPipe, "closed"),
                mailbox::QueueError::Oversized(_) => {
                    (io::ErrorKind::InvalidData, "message exceeds byte limit")
                }
            };
            io::Error::new(kind, format!("{queue} {reason}"))
        })
}
