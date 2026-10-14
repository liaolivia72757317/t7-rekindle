use std::{collections::HashMap, collections::VecDeque, io, sync::Arc, time::Duration};

use axum::{
    Json, Router,
    extract::{State, WebSocketUpgrade, ws::Message, ws::WebSocket},
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use serde_json::json;
use tokio::{
    sync::{Semaphore, mpsc, oneshot, watch},
    task::JoinHandle,
    time::{Instant, MissedTickBehavior},
};

use crate::{
    lobby::Lobby,
    maps::MapCatalog,
    protocol::{
        ClientCommand, ClientRequest, ErrorCode, MAX_REQUEST_BYTES, ServerEvent, ServerMessage,
        VERSION,
    },
    room::RoomSnapshot,
};

use crate::auth::{Admission, GuestIdentity, GuestRequest};
use crate::limits::{COMMAND_QUEUE_SIZE, MAX_CONNECTIONS, OUTPUT_QUEUE_SIZE};
const CACHE_SIZE: usize = 32;
const REQUESTS_PER_SECOND: usize = 64;

#[derive(Clone)]
struct AppState {
    commands: mpsc::Sender<Command>,
    shutdown: watch::Receiver<bool>,
    dev_lobby: bool,
    admission: Option<Arc<Admission>>,
    slots: Arc<Semaphore>,
}

pub struct GameServer {
    router: Router,
    shutdown: watch::Sender<bool>,
    task: Option<JoinHandle<()>>,
}

impl GameServer {
    /// Development identity is available only when explicitly enabled by the caller.
    /// The executable additionally requires a loopback listener.
    pub fn new(dev_lobby: bool) -> Self {
        Self::with_maps(dev_lobby, MapCatalog::default())
    }

    pub fn with_maps(dev_lobby: bool, maps: MapCatalog) -> Self {
        Self::with_admission(dev_lobby, maps, None)
    }

    pub fn with_admission(
        dev_lobby: bool,
        maps: MapCatalog,
        admission: Option<Arc<Admission>>,
    ) -> Self {
        Self::with_configuration(
            dev_lobby,
            maps,
            admission,
            crate::limits::RoomRules::default(),
        )
    }

    pub fn with_configuration(
        dev_lobby: bool,
        maps: MapCatalog,
        admission: Option<Arc<Admission>>,
        rules: crate::limits::RoomRules,
    ) -> Self {
        let (commands, receiver) = mpsc::channel(COMMAND_QUEUE_SIZE);
        let (shutdown, shutdown_rx) = watch::channel(false);
        let state = AppState {
            commands,
            shutdown: shutdown_rx.clone(),
            dev_lobby,
            admission,
            slots: Arc::new(Semaphore::new(MAX_CONNECTIONS)),
        };
        let router = Router::new()
            .route("/health", get(health))
            .route("/ready", get(health))
            .route("/ws", get(upgrade))
            .route(
                "/auth/guest",
                post(guest).layer(axum::extract::DefaultBodyLimit::max(4096)),
            )
            .route("/dev", get(dev_page))
            .route("/dev/lobby.js", get(dev_script))
            .route("/dev/lobby.css", get(dev_style))
            .with_state(state);
        let coordinator = Coordinator {
            lobby: Lobby::with_rules(maps, rules),
            ..Coordinator::default()
        };
        let task = tokio::spawn(coordinator.run(receiver, shutdown_rx));
        Self {
            router,
            shutdown,
            task: Some(task),
        }
    }

    pub fn router(&self) -> Router {
        self.router.clone()
    }

    pub fn shutdown_receiver(&self) -> watch::Receiver<bool> {
        self.shutdown.subscribe()
    }

    pub fn shutdown_handle(&self) -> watch::Sender<bool> {
        self.shutdown.clone()
    }

    pub fn shutdown(&self) {
        self.shutdown.send_replace(true);
    }

    pub async fn stopped(mut self) -> io::Result<()> {
        self.shutdown();
        if let Some(task) = self.task.take() {
            task.await.map_err(io::Error::other)?;
        }
        Ok(())
    }
}

impl Drop for GameServer {
    fn drop(&mut self) {
        self.shutdown();
        if let Some(task) = self.task.take() {
            task.abort();
        }
    }
}

pub async fn wait_for_shutdown(mut shutdown: watch::Receiver<bool>) {
    while !*shutdown.borrow_and_update() {
        if shutdown.changed().await.is_err() {
            break;
        }
    }
}

async fn health(State(state): State<AppState>) -> impl IntoResponse {
    let ready = !*state.shutdown.borrow() && !state.commands.is_closed();
    (
        if ready {
            StatusCode::OK
        } else {
            StatusCode::SERVICE_UNAVAILABLE
        },
        Json(json!({
            "ready": ready,
            "protocolVersion": VERSION,
            "mode": if state.admission.is_some() { "guestLobby" } else if state.dev_lobby { "developmentLobby" } else { "healthOnly" },
            "productionAdmission": false,
            "guestAdmission": state.admission.is_some(),
        })),
    )
}

async fn dev_asset(state: AppState, content_type: &'static str, body: &'static str) -> Response {
    if !state.dev_lobby || *state.shutdown.borrow() || state.commands.is_closed() {
        return StatusCode::NOT_FOUND.into_response();
    }
    (
        [
            (header::CONTENT_TYPE, content_type),
            (header::CACHE_CONTROL, "no-store"),
            (header::X_CONTENT_TYPE_OPTIONS, "nosniff"),
            (
                header::CONTENT_SECURITY_POLICY,
                "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
            ),
        ],
        body,
    ).into_response()
}

async fn dev_page(State(state): State<AppState>) -> Response {
    dev_asset(
        state,
        "text/html; charset=utf-8",
        include_str!("../dev/index.html"),
    )
    .await
}

async fn dev_script(State(state): State<AppState>) -> Response {
    dev_asset(
        state,
        "text/javascript; charset=utf-8",
        include_str!("../dev/lobby.js"),
    )
    .await
}

async fn dev_style(State(state): State<AppState>) -> Response {
    dev_asset(
        state,
        "text/css; charset=utf-8",
        include_str!("../dev/lobby.css"),
    )
    .await
}

async fn guest(
    State(state): State<AppState>,
    axum::extract::ConnectInfo(peer): axum::extract::ConnectInfo<std::net::SocketAddr>,
    Json(request): Json<GuestRequest>,
) -> Response {
    let Some(admission) = state.admission.clone() else {
        return StatusCode::NOT_FOUND.into_response();
    };
    if !admission.allow_attempt(&peer.ip().to_string()) {
        return StatusCode::TOO_MANY_REQUESTS.into_response();
    }
    match admission.issue(request) {
        Some(reply) => ([(header::CACHE_CONTROL, "no-store")], Json(reply)).into_response(),
        None => StatusCode::BAD_REQUEST.into_response(),
    }
}

async fn upgrade(
    State(state): State<AppState>,
    headers: HeaderMap,
    ws: WebSocketUpgrade,
) -> Response {
    if (!state.dev_lobby && state.admission.is_none())
        || *state.shutdown.borrow()
        || state.commands.is_closed()
    {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    }
    // Browser clients must originate from this service. Native gateways do not
    // send Origin; this check does not replace their future ticket admission.
    if let Some(origin) = headers.get(header::ORIGIN) {
        let host = headers
            .get(header::HOST)
            .and_then(|value| value.to_str().ok());
        if !host.is_some_and(|host| {
            origin.to_str().ok().is_some_and(|origin| {
                origin == format!("http://{host}") || origin == format!("https://{host}")
            })
        }) {
            return StatusCode::FORBIDDEN.into_response();
        }
    }
    let authorized = if let Some(admission) = &state.admission {
        let token = headers
            .get(header::AUTHORIZATION)
            .and_then(|h| h.to_str().ok())
            .and_then(|h| h.strip_prefix("Bearer "));
        let Some(account) = token.and_then(|token| admission.redeem(token)) else {
            return StatusCode::UNAUTHORIZED.into_response();
        };
        Some(account)
    } else {
        None
    };
    let Ok(permit) = state.slots.clone().try_acquire_owned() else {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    };
    ws.max_message_size(MAX_REQUEST_BYTES)
        .max_frame_size(MAX_REQUEST_BYTES)
        .on_upgrade(move |socket| async move {
            let _permit = permit;
            connection(socket, state, authorized).await;
        })
}

enum Command {
    Connected {
        id: String,
        authorized: Option<GuestIdentity>,
        outgoing: mpsc::Sender<ServerEvent>,
        closing: watch::Sender<bool>,
        accepted: oneshot::Sender<()>,
    },
    Request {
        id: String,
        request: ClientRequest,
    },
    Closed {
        id: String,
    },
}

async fn send_event(socket: &mut WebSocket, event: &ServerEvent) -> bool {
    let Ok(text) = serde_json::to_string(event) else {
        return false;
    };
    matches!(
        tokio::time::timeout(
            Duration::from_secs(2),
            socket.send(Message::Text(text.into()))
        )
        .await,
        Ok(Ok(()))
    )
}

async fn connection(mut socket: WebSocket, state: AppState, authorized: Option<GuestIdentity>) {
    let id = uuid::Uuid::new_v4().to_string();
    let (outgoing, mut output) = mpsc::channel(OUTPUT_QUEUE_SIZE);
    let (closing, closing_rx) = watch::channel(false);
    let (accepted, acceptance) = oneshot::channel();
    if state
        .commands
        .try_send(Command::Connected {
            id: id.clone(),
            authorized,
            outgoing,
            closing,
            accepted,
        })
        .is_err()
    {
        return;
    }
    if acceptance.await.is_err() {
        return;
    }

    let mut clock = tokio::time::interval(Duration::from_secs(1));
    clock.set_missed_tick_behavior(MissedTickBehavior::Skip);
    let mut last_input = Instant::now();
    let mut window = Instant::now();
    let mut count = 0;
    let global_shutdown = wait_for_shutdown(state.shutdown.clone());
    let close_connection = wait_for_shutdown(closing_rx);
    tokio::pin!(global_shutdown, close_connection);

    loop {
        tokio::select! {
            () = &mut global_shutdown => break,
            () = &mut close_connection => break,
            _ = clock.tick() => {
                if last_input.elapsed() >= Duration::from_secs(30) {
                    break;
                }
            }
            event = output.recv() => {
                match event {
                    Some(event) if send_event(&mut socket, &event).await => {},
                    _ => break,
                }
            }
            incoming = socket.recv() => {
                let Some(Ok(message)) = incoming else { break; };
                if window.elapsed() >= Duration::from_secs(1) {
                    window = Instant::now();
                    count = 0;
                }
                count += 1;
                if count > REQUESTS_PER_SECOND {
                    break;
                }
                last_input = Instant::now();
                match message {
                    Message::Text(text) => {
                        let request = serde_json::from_str::<ClientRequest>(&text);
                        match request {
                            Ok(request) => {
                                if state.commands.try_send(Command::Request { id: id.clone(), request }).is_err() {
                                    break;
                                }
                            }
                            Err(_) => {
                                let event = ServerEvent {
                                    protocol_version: VERSION,
                                    request_id: None,
                                    event: ServerMessage::Error { code: ErrorCode::BadRequest },
                                };
                                if !send_event(&mut socket, &event).await { break; }
                            }
                        }
                    }
                    Message::Ping(_) | Message::Pong(_) => {},
                    Message::Close(_) | Message::Binary(_) => break,
                }
            }
        }
    }
    // Cleanup must survive a temporarily full command queue.
    let _ = state.commands.send(Command::Closed { id }).await;
}

struct Connection {
    authorized: Option<GuestIdentity>,
    account: Option<String>,
    outgoing: mpsc::Sender<ServerEvent>,
    closing: watch::Sender<bool>,
    connected_at_ms: u64,
    last_sequence: u64,
    cache: VecDeque<(ClientRequest, ServerEvent)>,
}

#[derive(Default)]
struct Coordinator {
    lobby: Lobby,
    connections: HashMap<String, Connection>,
    accounts: HashMap<String, String>,
    failed: Vec<String>,
}

impl Coordinator {
    async fn run(mut self, mut commands: mpsc::Receiver<Command>, shutdown: watch::Receiver<bool>) {
        let started = Instant::now();
        let mut tick = tokio::time::interval(Duration::from_millis(50));
        tick.set_missed_tick_behavior(MissedTickBehavior::Skip);
        let stop = wait_for_shutdown(shutdown);
        tokio::pin!(stop);
        loop {
            tokio::select! {
                () = &mut stop => break,
                _ = tick.tick() => {
                    let now_ms = started.elapsed().as_millis() as u64;
                    self.advance(now_ms);
                    let unidentified: Vec<_> = self.connections.iter()
                        .filter(|(_, c)| c.account.is_none() && now_ms.saturating_sub(c.connected_at_ms) >= 10_000)
                        .map(|(id, _)| id.clone()).collect();
                    self.failed.extend(unidentified);
                }
                command = commands.recv() => {
                    let Some(command) = command else { break; };
                    let now_ms = started.elapsed().as_millis() as u64;
                    match command {
                        Command::Connected { id, authorized, outgoing, closing, accepted } => {
                            self.connections.insert(id.clone(), Connection {
                                authorized, account: None, outgoing, closing, connected_at_ms: now_ms,
                                last_sequence: 0, cache: VecDeque::new(),
                            });
                            if accepted.send(()).is_err() { self.failed.push(id); }
                        }
                        Command::Request { id, request } => self.request(&id, request, now_ms),
                        Command::Closed { id } => self.disconnect(&id),
                    }
                }
            }
            while let Some(id) = self.failed.pop() {
                self.disconnect(&id);
            }
        }
        for connection in self.connections.values() {
            connection.closing.send_replace(true);
        }
    }

    fn advance(&mut self, now_ms: u64) {
        for snapshot in self.lobby.advance(now_ms) {
            self.broadcast(snapshot);
        }
        self.broadcast_combat();
        self.broadcast_actions();
        self.broadcast_poses();
    }

    fn request(&mut self, id: &str, request: ClientRequest, now_ms: u64) {
        let Some(connection) = self.connections.get(id) else {
            return;
        };
        let sequence = match request.sequence() {
            Ok(sequence) => sequence,
            Err(code) => {
                self.send(
                    id,
                    ServerEvent::reply(request.request_id, ServerMessage::Error { code }),
                );
                return;
            }
        };
        if let Some((original, reply)) = connection
            .cache
            .iter()
            .find(|(r, _)| r.request_id == request.request_id)
        {
            let reply = if original == &request {
                reply.clone()
            } else {
                ServerEvent::reply(
                    request.request_id,
                    ServerMessage::Error {
                        code: ErrorCode::RequestConflict,
                    },
                )
            };
            self.send(id, reply);
            return;
        }
        if sequence <= connection.last_sequence {
            self.send(
                id,
                ServerEvent::reply(
                    request.request_id,
                    ServerMessage::Error {
                        code: ErrorCode::StaleRequest,
                    },
                ),
            );
            return;
        }

        self.advance(now_ms);
        let (message, changed) = if request.protocol_version != VERSION {
            (
                ServerMessage::Error {
                    code: ErrorCode::UnsupportedVersion,
                },
                None,
            )
        } else {
            match self.execute(id, &request.command, now_ms) {
                Ok(result) => result,
                Err(code) => {
                    if let ClientCommand::CreateCustomRoom {
                        map_id, channel_id, ..
                    } = &request.command
                    {
                        tracing::warn!(map_id, channel_id, ?code, "custom room creation rejected");
                    }
                    (ServerMessage::Error { code }, None)
                }
            }
        };
        let respawning = matches!(request.command, ClientCommand::Respawn { .. });
        let reply = ServerEvent::reply(request.request_id.clone(), message);
        if let Some(connection) = self.connections.get_mut(id) {
            connection.last_sequence = sequence;
            connection.cache.push_back((request, reply.clone()));
            if connection.cache.len() > CACHE_SIZE {
                connection.cache.pop_front();
            }
        }
        self.send(id, reply);
        if let Some(snapshot) = changed {
            self.broadcast(snapshot);
        }
        if respawning {
            self.broadcast_combat();
        }
        self.broadcast_actions();
        self.broadcast_combat();
    }

    fn execute(
        &mut self,
        id: &str,
        command: &ClientCommand,
        now_ms: u64,
    ) -> Result<(ServerMessage, Option<RoomSnapshot>), ErrorCode> {
        let account = self.connections.get(id).and_then(|c| c.account.clone());
        let identity = match command {
            ClientCommand::Hello { account_id } => {
                Some((account_id, account_id.chars().take(31).collect::<String>()))
            }
            ClientCommand::GatewayHello {
                account_id,
                display_name,
            } => Some((account_id, display_name.clone())),
            _ => None,
        };
        if let Some((account_id, display_name)) = identity {
            if self
                .connections
                .get(id)
                .and_then(|c| c.authorized.as_ref())
                .is_some_and(|a| &a.account_id != account_id || a.display_name != display_name)
            {
                return Err(ErrorCode::InvalidAccountId);
            }
            if account.is_some() {
                return Err(ErrorCode::AlreadyIdentified);
            }
            if account_id.is_empty()
                || account_id.len() > 36
                || !account_id
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
            {
                return Err(ErrorCode::InvalidAccountId);
            }
            if self.accounts.contains_key(account_id) {
                return Err(ErrorCode::AccountConnected);
            }
            let profile = self.lobby.identify(account_id, &display_name)?;
            if let Some(connection) = self.connections.get_mut(id) {
                connection.account = Some(account_id.clone());
            }
            self.accounts.insert(account_id.clone(), id.to_owned());
            return Ok((
                ServerMessage::Welcome {
                    account_id: account_id.clone(),
                    connection_id: id.to_owned(),
                    profile,
                },
                None,
            ));
        }
        let account = account.ok_or(ErrorCode::HelloRequired)?;
        let room = match command {
            ClientCommand::Ping => {
                return Ok((
                    ServerMessage::Pong {
                        server_time_ms: now_ms,
                    },
                    None,
                ));
            }
            ClientCommand::ListRooms => {
                return Ok((
                    ServerMessage::RoomList {
                        rooms: self.lobby.list(),
                    },
                    None,
                ));
            }
            ClientCommand::ListChannels => {
                return Ok((
                    ServerMessage::ChannelList {
                        channels: self.lobby.channels(),
                    },
                    None,
                ));
            }
            ClientCommand::GetRoomListing {
                channel_id,
                room_number,
            } => {
                return Ok((
                    ServerMessage::RoomListing {
                        room: self.lobby.listing(*channel_id, *room_number)?,
                    },
                    None,
                ));
            }
            ClientCommand::ListRoomPage {
                channel_id,
                start,
                limit,
            } => {
                return Ok((
                    ServerMessage::RoomPage {
                        page: self.lobby.page(*channel_id, *start, *limit)?,
                    },
                    None,
                ));
            }
            ClientCommand::CreateCustomRoom {
                name,
                map_id,
                channel_id,
                password,
            } => self
                .lobby
                .create_custom(&account, name, *map_id, *channel_id, password)?,
            ClientCommand::JoinCustomRoom {
                room_guid,
                password,
            } => self.lobby.join_custom(&account, *room_guid, password)?,
            ClientCommand::CreateRoom { name, map_id } => {
                self.lobby.create(&account, name, *map_id)?
            }
            ClientCommand::JoinRoom { room_id } => self.lobby.join(&account, room_id)?,
            ClientCommand::LeaveRoom => {
                let room_id = self.lobby.leave(&account, false)?;
                let changed = self.lobby.snapshot(&room_id);
                return Ok((ServerMessage::Left { room_id }, changed));
            }
            ClientCommand::SetCamp { camp } => self.lobby.set_camp(&account, *camp)?,
            ClientCommand::Ready { ready } => self.lobby.ready(&account, *ready)?,
            ClientCommand::Start => self.lobby.start(&account, now_ms)?,
            ClientCommand::Loaded { match_id } => self.lobby.loaded(&account, match_id, now_ms)?,
            ClientCommand::GetClientView { match_id } => {
                return Ok((
                    ServerMessage::ClientView {
                        view: self.lobby.client_view(&account, match_id)?,
                    },
                    None,
                ));
            }
            ClientCommand::ActionInput {
                match_id,
                input_seq,
                intent,
            } => {
                self.lobby
                    .action_input(&account, match_id, *input_seq, *intent, now_ms)?;
                return Ok((
                    ServerMessage::ActionAccepted {
                        match_id: match_id.clone(),
                        input_seq: *input_seq,
                    },
                    None,
                ));
            }
            ClientCommand::ContactInput {
                match_id,
                input_seq,
                contact,
            } => {
                let accepted = self.lobby.contact_input(
                    &account,
                    match_id,
                    *input_seq,
                    contact.clone(),
                    now_ms,
                )?;
                return Ok((
                    ServerMessage::ContactAccepted {
                        match_id: match_id.clone(),
                        input_seq: *input_seq,
                        accepted,
                    },
                    None,
                ));
            }
            ClientCommand::Respawn { match_id } => {
                self.lobby.respawn(&account, match_id, now_ms)?;
                return Ok((
                    ServerMessage::RespawnAccepted {
                        match_id: match_id.clone(),
                    },
                    None,
                ));
            }
            ClientCommand::PoseInput {
                match_id,
                input_seq,
                pose,
            } => {
                self.lobby
                    .pose_input(&account, match_id, *input_seq, pose.clone(), now_ms)?;
                return Ok((
                    ServerMessage::PoseAccepted {
                        match_id: match_id.clone(),
                        input_seq: *input_seq,
                    },
                    None,
                ));
            }
            ClientCommand::Hello { .. } | ClientCommand::GatewayHello { .. } => {
                unreachable!("hello is handled above")
            }
        };
        Ok((ServerMessage::Room { room: room.clone() }, Some(room)))
    }

    fn send(&mut self, id: &str, event: ServerEvent) {
        if let Some(connection) = self.connections.get(id)
            && connection.outgoing.try_send(event).is_err()
        {
            self.failed.push(id.to_owned());
        }
    }

    fn broadcast(&mut self, snapshot: RoomSnapshot) {
        for member in &snapshot.members {
            if let Some(id) = self.accounts.get(&member.account_id).cloned() {
                self.send(&id, ServerEvent::changed(snapshot.clone()));
            }
        }
    }

    fn disconnect(&mut self, id: &str) {
        if let Some(connection) = self.connections.remove(id) {
            connection.closing.send_replace(true);
            if let Some(account) = connection.account {
                self.accounts.remove(&account);
                if connection.authorized.is_some() {
                    self.lobby.forget_guest(&account);
                }
                if let Ok(room_id) = self.lobby.leave(&account, true)
                    && let Some(snapshot) = self.lobby.snapshot(&room_id)
                {
                    self.broadcast(snapshot);
                }
            }
        }
    }

    fn broadcast_combat(&mut self) {
        for update in self.lobby.take_combat_events() {
            if let Some(room) = self.lobby.snapshot(&update.room_id) {
                for member in room.members {
                    if let Some(id) = self.accounts.get(&member.account_id).cloned() {
                        self.send(
                            &id,
                            ServerEvent {
                                protocol_version: VERSION,
                                request_id: None,
                                event: ServerMessage::CombatChanged {
                                    update: update.clone(),
                                },
                            },
                        );
                    }
                }
            }
        }
    }
    fn broadcast_actions(&mut self) {
        for action in self.lobby.take_action_events() {
            if let Some(room) = self.lobby.snapshot(&action.room_id) {
                for member in room.members {
                    if let Some(id) = self.accounts.get(&member.account_id).cloned() {
                        self.send(
                            &id,
                            ServerEvent {
                                protocol_version: VERSION,
                                request_id: None,
                                event: ServerMessage::ActionChanged {
                                    action: action.clone(),
                                },
                            },
                        );
                    }
                }
            }
        }
    }

    fn broadcast_poses(&mut self) {
        for update in self.lobby.take_pose_events() {
            if let Some(room) = self.lobby.snapshot(&update.room_id) {
                for member in room.members {
                    if let Some(id) = self.accounts.get(&member.account_id).cloned() {
                        self.send(
                            &id,
                            ServerEvent {
                                protocol_version: VERSION,
                                request_id: None,
                                event: ServerMessage::PoseChanged {
                                    update: update.clone(),
                                },
                            },
                        );
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slow_output_disconnects_and_releases_the_room_and_account() {
        let mut coordinator = Coordinator::default();
        let (outgoing, mut output) = mpsc::channel(1);
        let (closing, closed) = watch::channel(false);
        coordinator.connections.insert(
            "connection-a".into(),
            Connection {
                authorized: None,
                account: None,
                outgoing,
                closing,
                connected_at_ms: 0,
                last_sequence: 0,
                cache: VecDeque::new(),
            },
        );
        coordinator.request(
            "connection-a",
            ClientRequest {
                protocol_version: VERSION,
                request_id: "1".into(),
                command: ClientCommand::Hello {
                    account_id: "player-a".into(),
                },
            },
            0,
        );
        assert!(matches!(
            output.try_recv().unwrap().event,
            ServerMessage::Welcome { .. }
        ));
        coordinator.request(
            "connection-a",
            ClientRequest {
                protocol_version: VERSION,
                request_id: "2".into(),
                command: ClientCommand::CreateRoom {
                    name: "room".into(),
                    map_id: 1028,
                },
            },
            1,
        );
        // The reply occupies the only slot; the room notification cannot be
        // delivered. Closing the connection must also release its membership.
        assert!(!coordinator.failed.is_empty());
        while let Some(id) = coordinator.failed.pop() {
            coordinator.disconnect(&id);
        }
        assert!(*closed.borrow());
        assert!(coordinator.connections.is_empty());
        assert!(coordinator.accounts.is_empty());
        assert!(coordinator.lobby.list().is_empty());
        assert!(coordinator.lobby.create("player-a", "again", 1028).is_ok());
    }
}
