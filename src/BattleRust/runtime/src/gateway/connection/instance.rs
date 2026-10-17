use super::*;
use crate::gateway::training_wire;
#[path = "instance_commands.rs"]
mod commands;
#[path = "instance_timers.rs"]
mod timers;

#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum EntryPhase {
    AwaitingCamp,
    CampSelected,
    Loaded,
    Entered,
}

struct InstanceSession {
    writer: transport::FramedWriter,
    ingress: Arc<InstanceIngress>,
    requests: Arc<Mutex<PlatformMailbox>>,
    catalogue: Arc<loadout::Catalogue>,
    trace: Option<Arc<GatewayTrace>>,
    started_at_ms: u64,
    last_sequence: Option<u32>,
    unhandled: UnhandledCommands,
    unify_at: Option<u64>,
    startup_phase: usize,
    scene_identity: Option<Identity>,
    hero_chosen: bool,
    selected_hero_position: Option<i32>,
    refresh: SceneRefresh,
    entry_phase: EntryPhase,
    respawn_phase: RespawnPhase,
    startup_due: Option<tokio::time::Instant>,
    actor_select_due: Option<tokio::time::Instant>,
    round_game_due: Option<tokio::time::Instant>,
    game_started_at_ms: Option<u64>,
    pending_finish: Option<(tokio::time::Instant, Settlement)>,
    settlement_seen: bool,
    leave_back_data: Option<u8>,
    leave_due: Option<tokio::time::Instant>,
    time_sync_due: tokio::time::Instant,
}

impl InstanceSession {
    fn is_training(&self) -> bool {
        t7r_battle_resources::training_maps::contains(self.ingress.map_id())
    }
    fn new(
        writer: transport::FramedWriter,
        ingress: Arc<InstanceIngress>,
        requests: Arc<Mutex<PlatformMailbox>>,
        catalogue: Arc<loadout::Catalogue>,
        trace: Option<Arc<GatewayTrace>>,
        started_at_ms: u64,
    ) -> Self {
        let refresh = SceneRefresh::new(ingress.roster().clone(), started_at_ms);
        let training = t7r_battle_resources::training_maps::contains(ingress.map_id());
        Self {
            writer,
            ingress,
            requests,
            catalogue,
            trace,
            started_at_ms,
            refresh,
            last_sequence: None,
            unhandled: UnhandledCommands::default(),
            unify_at: None,
            startup_phase: 0,
            scene_identity: None,
            hero_chosen: false,
            selected_hero_position: None,
            entry_phase: EntryPhase::AwaitingCamp,
            respawn_phase: RespawnPhase::Playing,
            startup_due: (!training)
                .then(|| tokio::time::Instant::now() + Duration::from_millis(200)),
            actor_select_due: None,
            round_game_due: None,
            game_started_at_ms: None,
            pending_finish: None,
            settlement_seen: false,
            leave_back_data: None,
            leave_due: None,
            time_sync_due: tokio::time::Instant::now() + Duration::from_secs(2),
        }
    }
    fn next_due(&self) -> tokio::time::Instant {
        self.startup_due
            .into_iter()
            .chain(self.actor_select_due)
            .chain(self.round_game_due)
            .chain(self.pending_finish.as_ref().map(|(due, _)| *due))
            .chain(self.leave_due)
            .chain(Some(self.time_sync_due))
            .min()
            .expect("periodic sync is always scheduled")
    }
    async fn handle_output(&mut self, batch: InstanceOutputBatch) -> io::Result<()> {
        let local_life = batch.local_life;
        if batch.settlement.is_some() && self.settlement_seen {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "duplicate settlement output",
            ));
        }
        for message in batch.messages {
            let response = downlink_frame(message.command, &message.body)?;
            tokio::time::timeout(Duration::from_secs(5), self.writer.send(&response))
                .await
                .map_err(|_| {
                    io::Error::new(io::ErrorKind::TimedOut, "instance write timed out")
                })??;
            self.refresh.record_message(&message);
        }
        self.refresh.apply(*batch.refresh);
        if let Some(life) = local_life {
            self.respawn_phase = match life {
                Life::Alive => RespawnPhase::Playing,
                Life::Dead { .. } => {
                    self.hero_chosen = false;
                    RespawnPhase::Dead
                }
                Life::Selecting { .. } => RespawnPhase::Selecting,
                Life::Waiting { .. } => RespawnPhase::Waiting,
                Life::Spawning { .. } => RespawnPhase::Spawning,
            };
        }
        if let Some(settlement) = batch.settlement {
            if let Some(trace) = &self.trace {
                trace.settlement_sent(false, &settlement)?;
            }
            self.settlement_seen = true;
            self.startup_due = None;
            self.actor_select_due = None;
            self.round_game_due = None;
            let hold = if settlement.reason == EndReason::Quit || self.leave_back_data.is_some() {
                Duration::ZERO
            } else {
                Duration::from_secs(5)
            };
            self.pending_finish = Some((tokio::time::Instant::now() + hold, settlement));
        }
        Ok(())
    }
}

pub(super) async fn serve_instance(
    mut reader: transport::FramedReader,
    mut writer: transport::FramedWriter,
    mut codec: ClientCodec,
    mut active: watch::Receiver<Option<Arc<InstanceIngress>>>,
    requests: Arc<Mutex<PlatformMailbox>>,
    catalogue: Arc<loadout::Catalogue>,
    trace: Option<Arc<GatewayTrace>>,
) -> io::Result<()> {
    let ingress = active.borrow().clone().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::Unsupported,
            "battle backend is not connected",
        )
    })?;
    ingress.claim()?;
    if let Some(trace) = &trace {
        trace.event(2, "instance-claimed", 0, &[])?;
    }
    let mut outgoing = ingress.take_output_receiver()?;
    let started_at_ms = scene_wire::native_uptime_ms();
    if let Some(trace) = &trace {
        trace.match_phase("match-bound", ingress.epoch(), started_at_ms)?;
    }
    for message in
        scene_wire::instance_start(started_at_ms, ingress.map_id(), ingress.client_rid())?
    {
        let message = if message.command == 10
            && !t7r_battle_resources::training_maps::contains(ingress.map_id())
        {
            let squads = crate::battle_session::SquadRoster::with_local_player(
                ingress
                    .roster()
                    .bots
                    .iter()
                    .map(|bot| (bot.actor_id, bot.camp)),
                (ingress.roster().local.actor_id, ingress.roster().local.camp),
            );
            squad_wire::enable(message, &squads, ingress.actor_rids())?
        } else {
            message
        };
        let response = downlink_frame(message.command, &message.body)?;
        tokio::time::timeout(Duration::from_secs(5), writer.send(&response))
            .await
            .map_err(|_| {
                io::Error::new(io::ErrorKind::TimedOut, "instance start write timed out")
            })??;
        if let Some(trace) = &trace {
            trace.event(2, "startup-sent", message.command, &message.body)?;
        }
    }
    if t7r_battle_resources::training_maps::contains(ingress.map_id()) {
        send_instance_messages(
            &mut writer,
            training_wire::startup(started_at_ms, ingress.duration_ms())?,
        )
        .await?;
    }
    let mut session =
        InstanceSession::new(writer, ingress, requests, catalogue, trace, started_at_ms);
    let mut prefer_output = true;
    loop {
        let event = {
            let mut next = pin!(next_instance_event(
                &mut active,
                &mut outgoing,
                &mut reader,
                prefer_output
            ));
            let mut timer = pin!(tokio::time::sleep_until(session.next_due()));
            poll_fn(|context| {
                if timer.as_mut().poll(context).is_ready() {
                    Poll::Ready(None)
                } else {
                    next.as_mut().poll(context).map(Some)
                }
            })
            .await
        };
        if !active
            .borrow()
            .as_ref()
            .is_some_and(|next| Arc::ptr_eq(next, &session.ingress))
        {
            return Ok(());
        }
        let Some(event) = event else {
            session.handle_timer().await?;
            continue;
        };
        let frame = match event {
            InstanceEvent::Changed(result) => {
                active.borrow_and_update();
                if result.is_err() {
                    return Ok(());
                }
                continue;
            }
            InstanceEvent::Output(batch) => {
                prefer_output = false;
                let batch = batch.ok_or_else(|| {
                    io::Error::new(io::ErrorKind::BrokenPipe, "instance output queue closed")
                })?;
                session.handle_output(batch).await?;
                continue;
            }
            InstanceEvent::Input(frame) => {
                prefer_output = true;
                frame?
            }
        };
        let Some(frame) = frame else {
            session.ingress.mark_instance_disconnected();
            return Ok(());
        };
        match codec.receive(&frame).map_err(io::Error::other)? {
            ClientEvent::Message(message) => session.handle_message(message).await?,
            ClientEvent::Closed => {
                session.ingress.mark_instance_disconnected();
                return Ok(());
            }
            ClientEvent::Authenticated { .. } => {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "duplicate instance authentication",
                ));
            }
        }
    }
}
