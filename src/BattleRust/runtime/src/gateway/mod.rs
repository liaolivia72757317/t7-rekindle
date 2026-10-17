mod battle;
mod compat;
mod connection;
mod instance;
mod leave_wire;
mod loadout;
mod match_selection;
mod movement_report;
mod output;
pub mod platform;
mod refresh;
mod room;
mod scene_actor_wire;
mod scene_entry_wire;
mod scene_vision_wire;
#[allow(unsafe_code)]
mod scene_wire;
#[cfg(all(test, windows))]
mod selection_tests;
mod squad_wire;
pub mod titles;
mod trace;
mod training_wire;

use crate::battle_resources::BattleResources;
use crate::battle_session::{Config, MemberKind, Output};
use crate::client_battle_wire::ClientBattleMessage;
use crate::semantic_adapter::{ContactSample, MotionSample, SourceBinding};
use battle::BoundBattle;
use instance::InstanceIngress;
use platform::PlatformMailbox;
use scene_actor_wire::{LocalSceneActor, SceneRoster};
use std::io;
use std::net::{Ipv4Addr, TcpListener};
use std::sync::{Arc, Mutex, mpsc};
use std::thread::{self, JoinHandle};
use t7r_battle_resources::ResourceBundle;
use tokio::sync::{oneshot, watch};
use tokio::task::{JoinSet, LocalSet};
use trace::GatewayTrace;

#[derive(Default, Debug, Clone, Copy)]
pub struct Snapshot {
    pub epoch: u64,
    pub running: bool,
    pub failed: bool,
    pub ports: [u16; 3],
    pub active_connections: u32,
    pub pending_requests: u32,
    pub connection_errors: u32,
    pub rules_version: u32,
    pub resource_version: u32,
    pub map_id: u32,
}

#[derive(Default)]
struct Diagnostics {
    active: u32,
    errors: u32,
    failed: bool,
    error: String,
    first_failure: Option<String>,
}

impl Diagnostics {
    fn record(&mut self, error: impl std::fmt::Display) {
        self.errors = self.errors.saturating_add(1);
        self.remember(error);
    }

    fn remember(&mut self, error: impl std::fmt::Display) {
        self.error = error.to_string();
        self.first_failure.get_or_insert_with(|| self.error.clone());
    }

    fn describe(&self) -> String {
        match &self.first_failure {
            Some(first) if first != &self.error => {
                format!("{}; first gateway failure: {first}", self.error)
            }
            _ => self.error.clone(),
        }
    }
}

struct Worker {
    stop: oneshot::Sender<()>,
    thread: JoinHandle<io::Result<()>>,
}

pub struct Gateway {
    resources: Arc<ResourceBundle>,
    prepared_resources: Option<Arc<BattleResources>>,
    epoch: u64,
    ports: [u16; 3],
    mailbox: Arc<Mutex<PlatformMailbox>>,
    diagnostics: Arc<Mutex<Diagnostics>>,
    worker: Option<Worker>,
    battle: Option<BoundBattle>,
    last_battle_epoch: u64,
    instance_tx: watch::Sender<Option<Arc<InstanceIngress>>>,
}

impl Gateway {
    pub fn new(resources: Arc<ResourceBundle>) -> Self {
        let (instance_tx, _) = watch::channel(None);
        Self {
            resources,
            prepared_resources: None,
            epoch: 0,
            ports: [0; 3],
            mailbox: Arc::new(Mutex::new(PlatformMailbox::new(0))),
            diagnostics: Arc::new(Mutex::new(Diagnostics::default())),
            worker: None,
            battle: None,
            last_battle_epoch: 0,
            instance_tx,
        }
    }

    pub fn publish_titles(
        &self,
        epoch: u64,
        json: &[u8],
        request_id: u64,
        success: bool,
    ) -> io::Result<()> {
        self.mailbox
            .lock()
            .map_err(poison)?
            .titles
            .publish(epoch, json, request_id, success)
    }

    pub fn title_request(&self) -> io::Result<titles::TitleRequest> {
        Ok(self.mailbox.lock().map_err(poison)?.titles.request())
    }

    pub fn resource_bundle(&self) -> &ResourceBundle {
        &self.resources
    }

    pub fn select_match_map(&mut self, map_id: u32) -> io::Result<()> {
        if self.battle.is_some() || self.worker.is_none() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "map selection requires a stopped battle and running gateway",
            ));
        }
        if self.resources.map_id() == map_id {
            return Ok(());
        }
        let resources = Arc::new(t7r_battle_resources::match_maps::select(
            &self.resources,
            map_id,
        )?);
        let prepared = Arc::new(BattleResources::prepare(&resources)?);
        self.resources = resources;
        self.prepared_resources = Some(prepared);
        Ok(())
    }

    pub fn start(&mut self, epoch: u64) -> io::Result<Snapshot> {
        let result = self.start_inner(epoch);
        if let Err(error) = &result {
            self.diagnostics.lock().map_err(poison)?.error = error.to_string();
        }
        result
    }

    fn start_inner(&mut self, epoch: u64) -> io::Result<Snapshot> {
        if self.worker.is_some() || epoch <= self.epoch {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "start requires a new epoch and stopped gateway",
            ));
        }
        let prepared_resources = Arc::new(BattleResources::prepare(&self.resources)?);
        let catalogue = Arc::new(loadout::Catalogue::from_bundle(&self.resources)?);
        let trace = GatewayTrace::open()?;
        let listeners: Vec<_> = (0..3)
            .map(|_| {
                let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))?;
                listener.set_nonblocking(true)?;
                Ok(listener)
            })
            .collect::<io::Result<_>>()?;
        let mut ports = [0; 3];
        for (index, listener) in listeners.iter().enumerate() {
            ports[index] = listener.local_addr()?.port();
        }
        let mailbox = Arc::new(Mutex::new(PlatformMailbox::new(epoch)));
        let diagnostics = Arc::new(Mutex::new(Diagnostics::default()));
        let (stop, stopped) = oneshot::channel();
        let (ready, started) = mpsc::sync_channel(1);
        let signals = RunSignals { stopped, ready };
        let work_mailbox = mailbox.clone();
        let work_diagnostics = diagnostics.clone();
        let instance_rx = self.instance_tx.subscribe();
        let thread = thread::Builder::new()
            .name("t7r-client-gateway".into())
            .spawn(move || {
                let result = run(
                    listeners,
                    catalogue,
                    work_mailbox,
                    instance_rx,
                    trace,
                    &work_diagnostics,
                    signals,
                );
                if let Err(error) = &result {
                    let mut state = work_diagnostics
                        .lock()
                        .unwrap_or_else(|error| error.into_inner());
                    state.failed = true;
                    state.record(error);
                }
                result
            })?;
        if started.recv().is_err() {
            let _ = stop.send(());
            return thread
                .join()
                .unwrap_or_else(|_| Err(io::Error::other("gateway worker panicked")))
                .and(Err(io::Error::other("gateway stopped before ready")));
        }
        self.epoch = epoch;
        self.prepared_resources = Some(prepared_resources);
        self.ports = ports;
        self.mailbox = mailbox;
        self.diagnostics = diagnostics;
        self.worker = Some(Worker { stop, thread });
        self.snapshot()
    }

    pub fn stop(&mut self) -> io::Result<()> {
        let battle_result = self.stop_battle();
        let result = if let Some(worker) = self.worker.take() {
            let _ = worker.stop.send(());
            worker
                .thread
                .join()
                .unwrap_or_else(|_| Err(io::Error::other("gateway worker panicked")))
        } else {
            Ok(())
        };
        self.ports = [0; 3];
        self.prepared_resources = None;
        self.mailbox.lock().map_err(poison)?.clear();
        if let Err(error) = &result {
            let mut state = self.diagnostics.lock().map_err(poison)?;
            state.failed = true;
            state.record(error);
        }
        if let Err(error) = &battle_result {
            let mut state = self.diagnostics.lock().map_err(poison)?;
            state.failed = true;
            state.record(error);
        }
        battle_result.and(result)
    }

    pub fn start_battle(&mut self, config: Config, binding: SourceBinding) -> io::Result<()> {
        self.start_battle_inner(config, binding, false)
    }

    pub fn start_battle_staged(
        &mut self,
        config: Config,
        binding: SourceBinding,
    ) -> io::Result<()> {
        self.start_battle_inner(config, binding, true)
    }

    fn start_battle_inner(
        &mut self,
        config: Config,
        binding: SourceBinding,
        staged: bool,
    ) -> io::Result<()> {
        if self.battle.is_some()
            || config.epoch <= self.last_battle_epoch
            || !self.snapshot()?.running
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "battle start requires a newer epoch and running gateway",
            ));
        }
        let resources = self.prepared_resources.as_ref().ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "battle resources are not prepared",
            )
        })?;
        let humans = config
            .members
            .iter()
            .filter(|member| member.kind == MemberKind::Human)
            .collect::<Vec<_>>();
        let local_player = if humans.len() == 1 {
            binding
                .actor_rids
                .get(&humans[0].actor_id)
                .copied()
                .map(|rid| (humans[0].clone(), rid))
        } else {
            None
        };
        let bot_members = config
            .members
            .iter()
            .filter(|member| member.kind == MemberKind::Bot)
            .cloned()
            .collect::<Vec<_>>();
        let bot_names = self.resources.data()["botNames"]
            .as_array()
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "missing bot name table"))?
            .iter()
            .map(|value| {
                value.as_str().map(str::to_owned).ok_or_else(|| {
                    io::Error::new(io::ErrorKind::InvalidData, "invalid bot name table")
                })
            })
            .collect::<io::Result<Vec<_>>>()?;
        let client_binding = binding.clone();
        let duration_ms = config.duration_ms;
        let kill_target = config.kill_target;
        let battle =
            BoundBattle::start(&self.resources, resources.clone(), config, binding, staged)?;
        let ingress = local_player
            .map(|(member, client_rid)| {
                let initial = battle.initial_actor(member.actor_id).ok_or_else(|| {
                    io::Error::new(
                        io::ErrorKind::InvalidData,
                        "local actor is absent from battle",
                    )
                })?;
                let spawn = resources.camps()[usize::from(member.camp - 1)]
                    .spawns()
                    .first()
                    .ok_or_else(|| {
                        io::Error::new(io::ErrorKind::InvalidData, "local camp has no spawn")
                    })?;
                let scene_actor = LocalSceneActor {
                    actor_id: member.actor_id,
                    client_rid,
                    camp: member.camp,
                    hero_id: member.hero_id,
                    max_health: initial.health,
                    position: resources.training_entry().unwrap_or(*spawn).position,
                    heading: resources.training_entry().unwrap_or(*spawn).heading,
                    equipment: member.equipment,
                    weapon_id: initial.action.weapon_id,
                    offhand_id: initial.action.offhand_id,
                };
                let mut next_spawn = [0_usize; 2];
                next_spawn[usize::from(member.camp - 1)] = 1;
                let mut bots = bot_members;
                bots.sort_by_key(|bot| bot.actor_id);
                let bot_scenes = bots
                    .into_iter()
                    .map(|bot| {
                        let camp = usize::from(bot.camp - 1);
                        let spawn = resources.camps()[camp]
                            .spawns()
                            .get(next_spawn[camp])
                            .ok_or_else(|| {
                                io::Error::new(io::ErrorKind::InvalidData, "bot camp has no spawn")
                            })?;
                        next_spawn[camp] += 1;
                        let initial = battle.initial_actor(bot.actor_id).ok_or_else(|| {
                            io::Error::new(io::ErrorKind::InvalidData, "bot is absent from battle")
                        })?;
                        let client_rid =
                            *client_binding
                                .actor_rids
                                .get(&bot.actor_id)
                                .ok_or_else(|| {
                                    io::Error::new(
                                        io::ErrorKind::InvalidData,
                                        "bot has no client RID",
                                    )
                                })?;
                        Ok(LocalSceneActor {
                            actor_id: bot.actor_id,
                            client_rid,
                            camp: bot.camp,
                            hero_id: bot.hero_id,
                            max_health: initial.health,
                            position: spawn.position,
                            heading: spawn.heading,
                            equipment: bot.equipment,
                            weapon_id: initial.action.weapon_id,
                            offhand_id: initial.action.offhand_id,
                        })
                    })
                    .collect::<io::Result<Vec<_>>>()?;
                InstanceIngress::new(
                    client_binding,
                    SceneRoster {
                        local: scene_actor,
                        bots: bot_scenes,
                        bot_names,
                        kill_target,
                        guard_max: resources.guard_rules().maximum,
                        local_shields: battle
                            .initial_shields(member.actor_id)
                            .ok_or_else(|| {
                                io::Error::new(
                                    io::ErrorKind::InvalidData,
                                    "local shields are absent from battle",
                                )
                            })?
                            .to_vec(),
                    },
                    battle.sender(),
                    battle.activator(),
                    battle.player_cursor(),
                    self.resources.map_id(),
                    duration_ms,
                    self.resources.clone(),
                )
                .map(Arc::new)
            })
            .transpose()?;
        self.last_battle_epoch = battle.epoch();
        self.battle = Some(battle);
        self.instance_tx.send_replace(ingress);
        Ok(())
    }

    pub fn activate_battle(&mut self, epoch: u64) -> io::Result<()> {
        let battle = self.battle.as_mut().ok_or_else(battle_not_started)?;
        if battle.epoch() != epoch {
            return Err(battle_not_started());
        }
        battle.activate()
    }

    pub fn submit_battle_world(&mut self, payload: &[u8]) -> io::Result<()> {
        let result = self
            .battle
            .as_mut()
            .ok_or_else(battle_not_started)?
            .submit_world(payload);
        self.battle_result(result)
    }

    pub fn submit_battle_player(&mut self, payload: &[u8]) -> io::Result<()> {
        self.battle
            .as_mut()
            .ok_or_else(battle_not_started)?
            .submit_player(payload)
    }

    pub fn submit_battle_motion(&mut self, sample: MotionSample) -> io::Result<()> {
        let result = self
            .battle
            .as_mut()
            .ok_or_else(battle_not_started)?
            .submit_motion(sample);
        self.battle_result(result)
    }

    pub fn submit_battle_contacts(&mut self, sample: ContactSample) -> io::Result<()> {
        let result = self
            .battle
            .as_mut()
            .ok_or_else(battle_not_started)?
            .submit_contacts(sample);
        self.battle_result(result)
    }

    pub fn submit_battle_contact_payload(&mut self, payload: &[u8]) -> io::Result<()> {
        let sample = ContactSample::decode(payload).map_err(|error| {
            io::Error::new(
                error.kind(),
                format!("contact payload decode failed: {error}"),
            )
        });
        if let Err(error) = &sample {
            self.diagnostics.lock().map_err(poison)?.remember(error);
        }
        self.submit_battle_contacts(sample?)
    }

    pub fn read_battle_outputs(&mut self) -> io::Result<Option<Vec<Output>>> {
        let result = self
            .battle
            .as_mut()
            .ok_or_else(battle_not_started)?
            .try_recv();
        self.battle_result(result)
    }

    pub fn bind_battle_client_clock(&mut self, epoch: u64, offset_ms: i64) -> io::Result<()> {
        self.battle
            .as_mut()
            .ok_or_else(battle_not_started)?
            .bind_client_clock(epoch, offset_ms)
    }

    pub fn bind_battle_server_clock(&mut self, epoch: u64, offset_ms: i64) -> io::Result<()> {
        self.battle
            .as_mut()
            .ok_or_else(battle_not_started)?
            .bind_server_clock(epoch, offset_ms)
    }

    /// Stage one complete worker batch, enqueue supported original-client events,
    /// then return the unchanged batch for the remaining output consumers.
    /// Queue or codec failure keeps that batch pending for an explicit retry.
    pub fn dispatch_next_battle_output(&mut self) -> io::Result<Option<Vec<Output>>> {
        let result = self.dispatch_battle_output();
        self.battle_result(result)
    }

    fn battle_result<T>(&self, result: io::Result<T>) -> io::Result<T> {
        if let Err(error) = &result {
            self.diagnostics.lock().map_err(poison)?.error = error.to_string();
        }
        result
    }

    fn dispatch_battle_output(&mut self) -> io::Result<Option<Vec<Output>>> {
        let battle = self.battle.as_mut().ok_or_else(battle_not_started)?;
        if battle.waiting_for_client_clocks() {
            return Ok(None);
        }
        let epoch = battle.epoch();
        let offset_ms = battle.client_time_offset_ms();
        let server_offset_ms = battle.server_time_offset_ms();
        let actor_rids = battle.actor_rids().clone();
        let current_output = battle.client_output().clone();
        let Some(batch) = battle.pending_published()? else {
            return Ok(None);
        };
        let (local_actor_id, scene) = {
            let active = self.instance_tx.borrow();
            if let Some(client) = active.as_ref() {
                (
                    Some(client.actor_id()),
                    client
                        .identity()?
                        .map(|identity| {
                            client
                                .roster_for_outputs(&batch.outputs)
                                .map(|roster| (roster, identity))
                        })
                        .transpose()?,
                )
            } else {
                (None, None)
            }
        };
        let (messages, client_output) = if let Some((roster, identity)) = &scene {
            output::project_client_messages_with_scene(
                &current_output,
                &batch.outputs,
                &batch.context,
                offset_ms,
                server_offset_ms,
                local_actor_id,
                &actor_rids,
                Some(output::SceneProjection { roster, identity }),
            )?
        } else {
            output::project_client_messages(
                &current_output,
                &batch.outputs,
                &batch.context,
                offset_ms,
                server_offset_ms,
                local_actor_id,
                &actor_rids,
            )?
        };
        let refresh = scene
            .as_ref()
            .map_or_else(refresh::RefreshUpdate::default, |(roster, _)| {
                refresh::RefreshUpdate::project(
                    &batch.outputs,
                    &batch.context,
                    Some(roster),
                    offset_ms,
                    server_offset_ms,
                )
            });
        if !messages.is_empty()
            || !refresh.is_empty()
            || batch
                .outputs
                .iter()
                .any(|output| matches!(output, Output::Ended(_)))
        {
            let settlement = batch.outputs.iter().find_map(|output| match output {
                Output::Ended(settlement) => Some(settlement),
                _ => None,
            });
            let local_life = batch.outputs.iter().rev().find_map(|output| match output {
                Output::Life { actor_id, state } if Some(*actor_id) == local_actor_id => {
                    Some(*state)
                }
                _ => None,
            });
            let settlement = settlement
                .map(|value| {
                    let offset = server_offset_ms.ok_or_else(|| {
                        io::Error::new(
                            io::ErrorKind::InvalidData,
                            "battle server clock is not bound",
                        )
                    })?;
                    let mut wire = value.clone();
                    wire.started_at_ms =
                        crate::client_battle_wire::wire_time(value.started_at_ms, offset)?;
                    wire.ended_at_ms =
                        crate::client_battle_wire::wire_time(value.ended_at_ms, offset)?;
                    Ok::<_, io::Error>(wire)
                })
                .transpose()?;
            let ingress = self.instance_tx.borrow();
            let ingress = ingress.as_ref().ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::Unsupported,
                    "instance output has no local player",
                )
            })?;
            if self
                .battle
                .as_ref()
                .is_none_or(|current| current.epoch() != epoch)
            {
                return Err(battle_not_started());
            }
            ingress.send_projected(messages, settlement, local_life, refresh)?;
        }
        if let Some(battle) = self.battle.as_mut() {
            battle.set_client_output(client_output);
        }
        Ok(self
            .battle
            .as_mut()
            .and_then(BoundBattle::take_pending_output))
    }

    pub fn send_battle_client_messages(
        &self,
        epoch: u64,
        messages: Vec<ClientBattleMessage>,
    ) -> io::Result<()> {
        if self
            .battle
            .as_ref()
            .is_none_or(|battle| battle.epoch() != epoch)
        {
            return Err(battle_not_started());
        }
        self.instance_tx
            .borrow()
            .as_ref()
            .ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::Unsupported,
                    "instance output has no local player",
                )
            })?
            .send(messages)
    }

    pub fn battle_clock_ms(&self) -> io::Result<u64> {
        self.battle
            .as_ref()
            .ok_or_else(battle_not_started)?
            .clock_ms()
    }

    pub fn battle_preparation_open(&self) -> io::Result<bool> {
        let battle = self.battle.as_ref().ok_or_else(battle_not_started)?;
        let active = self.instance_tx.borrow();
        if !active
            .as_ref()
            .is_some_and(|ingress| ingress.preparation_open())
        {
            return Ok(false);
        }
        match battle.clock_ms() {
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => Ok(true),
            Ok(_) => Ok(false),
            Err(error) => Err(error),
        }
    }

    pub fn reserve_battle_rearm(&self) -> io::Result<bool> {
        let battle = self.battle.as_ref().ok_or_else(battle_not_started)?;
        let active = self.instance_tx.borrow();
        let Some(ingress) = active.as_ref() else {
            return Ok(false);
        };
        if !ingress.preparation_open() {
            return Ok(false);
        }
        match battle.clock_ms() {
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => Ok(ingress.reserve_rearm()),
            Ok(_) => Ok(false),
            Err(error) => Err(error),
        }
    }

    pub fn battle_returned_to_room(&self) -> io::Result<bool> {
        if self.battle.is_none() {
            return Err(battle_not_started());
        }
        Ok(self
            .instance_tx
            .borrow()
            .as_ref()
            .is_some_and(|ingress| ingress.returned_to_room()))
    }

    pub fn stop_battle(&mut self) -> io::Result<()> {
        self.instance_tx.send_replace(None);
        if let Some(mut battle) = self.battle.take() {
            battle.stop()
        } else {
            Ok(())
        }
    }

    pub fn prepared_resources(&self) -> Option<&Arc<BattleResources>> {
        self.prepared_resources.as_ref()
    }

    pub fn snapshot(&self) -> io::Result<Snapshot> {
        let state = self.diagnostics.lock().map_err(poison)?;
        Ok(Snapshot {
            epoch: self.epoch,
            running: self
                .worker
                .as_ref()
                .is_some_and(|w| !w.thread.is_finished())
                && !state.failed,
            failed: state.failed,
            ports: self.ports,
            active_connections: state.active,
            pending_requests: self.mailbox.lock().map_err(poison)?.pending_count() as u32,
            connection_errors: state.errors,
            rules_version: self.resources.rules_version(),
            resource_version: self.resources.resource_version(),
            map_id: self.resources.map_id(),
        })
    }

    pub fn mailbox(&self) -> &Mutex<PlatformMailbox> {
        &self.mailbox
    }
    pub fn last_error(&self) -> io::Result<String> {
        Ok(self.diagnostics.lock().map_err(poison)?.describe())
    }
}

impl Drop for Gateway {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}

struct RunSignals {
    stopped: oneshot::Receiver<()>,
    ready: mpsc::SyncSender<()>,
}

fn run(
    listeners: Vec<TcpListener>,
    catalogue: Arc<loadout::Catalogue>,
    mailbox: Arc<Mutex<PlatformMailbox>>,
    instance_rx: watch::Receiver<Option<Arc<InstanceIngress>>>,
    trace: Option<Arc<GatewayTrace>>,
    diagnostics: &Arc<Mutex<Diagnostics>>,
    signals: RunSignals,
) -> io::Result<()> {
    let RunSignals { stopped, ready } = signals;
    let instance_port = listeners
        .get(2)
        .ok_or_else(|| io::Error::other("missing instance listener"))?
        .local_addr()?
        .port();
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;
    LocalSet::new().block_on(&runtime, async {
        let mut tasks = JoinSet::new();
        for (role, listener) in listeners.into_iter().enumerate() {
            let listener = tokio::net::TcpListener::from_std(listener)?;
            tasks.spawn_local(connection::accept(
                listener,
                connection::Channel {
                    role,
                    instance_port,
                    trace: trace.clone(),
                },
                catalogue.clone(),
                mailbox.clone(),
                instance_rx.clone(),
                diagnostics.clone(),
            ));
        }
        tasks.spawn_local(async {
            let _ = stopped.await;
            Ok(())
        });
        ready
            .send(())
            .map_err(|_| io::Error::other("gateway owner closed during startup"))?;
        let result = tasks
            .join_next()
            .await
            .ok_or_else(|| io::Error::other("missing gateway tasks"))?
            .map_err(io::Error::other)?;
        tasks.abort_all();
        while tasks.join_next().await.is_some() {}
        result
    })
}

fn poison<T>(_: std::sync::PoisonError<T>) -> io::Error {
    io::Error::other("gateway state poisoned")
}

fn battle_not_started() -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, "battle is not started")
}
