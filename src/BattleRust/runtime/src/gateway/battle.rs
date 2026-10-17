use crate::{
    battle_resources::BattleResources,
    battle_session::{ActorSnapshot, BattleSession, Config, Output},
    battle_worker::{BattleActivator, BattleSender, BattleWorker, PublishedBatch},
    gateway::output::ClientOutputState,
    semantic_adapter::{
        BoundInput, ContactSample, MotionSample, SourceBinding, contacts_input, motion_input,
        player_input, world_input,
    },
};
use std::{
    collections::{BTreeMap, BTreeSet},
    io,
    sync::Arc,
    time::Duration,
};
use t7r_battle_resources::ResourceBundle;

const SUBMIT_TIMEOUT: Duration = Duration::from_secs(5);

pub struct BoundBattle {
    binding: SourceBinding,
    staged: bool,
    world_batch: u64,
    player_cursor: Arc<tokio::sync::Mutex<u64>>,
    motion_batch: u64,
    contact_batch: u64,
    client_time_offset_ms: Option<i64>,
    server_time_offset_ms: Option<i64>,
    runtime: tokio::runtime::Runtime,
    worker: BattleWorker,
    pending_output: Option<PublishedBatch>,
    client_output: ClientOutputState,
    initial_actors: BTreeMap<u32, ActorSnapshot>,
    initial_shields: BTreeMap<u32, Vec<(u32, u32)>>,
}

impl BoundBattle {
    pub fn start(
        bundle: &ResourceBundle,
        resources: Arc<BattleResources>,
        config: Config,
        binding: SourceBinding,
        staged: bool,
    ) -> io::Result<Self> {
        let distinct_rids = binding
            .actor_rids
            .values()
            .copied()
            .collect::<BTreeSet<_>>();
        if config.epoch != binding.epoch
            || binding.source_id == 0
            || config.members.len() != binding.actor_rids.len()
            || binding.actor_rids.values().any(|&rid| rid == 0)
            || distinct_rids.len() != binding.actor_rids.len()
            || config.members.iter().any(|member| {
                member.sample_source_id != binding.source_id
                    || !binding.actor_rids.contains_key(&member.actor_id)
            })
        {
            return Err(invalid("battle member source or RID binding mismatch"));
        }
        let mut client_output = ClientOutputState::new(&config);
        client_output.training = t7r_battle_resources::training_maps::contains(bundle.map_id());
        let actor_ids = config
            .members
            .iter()
            .map(|member| member.actor_id)
            .collect::<Vec<_>>();
        let session = BattleSession::new(bundle, resources, config)?;
        let initial_shields = actor_ids
            .iter()
            .map(|&id| {
                (
                    id,
                    session.actor_shields(id).expect("validated battle member"),
                )
            })
            .collect();
        let initial_actors = actor_ids
            .into_iter()
            .map(|id| (id, session.actor(id).expect("validated battle member")))
            .collect();
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_time()
            .build()?;
        let worker = if staged {
            BattleWorker::start_paused(session)?
        } else {
            BattleWorker::start(session)?
        };
        Ok(Self {
            binding,
            staged,
            world_batch: 0,
            player_cursor: Arc::new(tokio::sync::Mutex::new(0)),
            motion_batch: 0,
            contact_batch: 0,
            client_time_offset_ms: None,
            server_time_offset_ms: None,
            runtime,
            worker,
            pending_output: None,
            client_output,
            initial_actors,
            initial_shields,
        })
    }

    pub fn epoch(&self) -> u64 {
        self.binding.epoch
    }

    pub fn actor_rids(&self) -> &BTreeMap<u32, u16> {
        &self.binding.actor_rids
    }

    pub(super) fn initial_actor(&self, actor_id: u32) -> Option<&ActorSnapshot> {
        self.initial_actors.get(&actor_id)
    }

    pub(super) fn initial_shields(&self, actor_id: u32) -> Option<&[(u32, u32)]> {
        self.initial_shields.get(&actor_id).map(Vec::as_slice)
    }

    pub(super) fn client_output(&self) -> &ClientOutputState {
        &self.client_output
    }

    pub(super) fn set_client_output(&mut self, output: ClientOutputState) {
        self.client_output = output;
    }

    pub fn client_time_offset_ms(&self) -> Option<i64> {
        self.client_time_offset_ms
    }

    pub fn server_time_offset_ms(&self) -> Option<i64> {
        self.server_time_offset_ms
    }

    pub(super) fn waiting_for_client_clocks(&self) -> bool {
        self.staged
            && (self.client_time_offset_ms.is_none() || self.server_time_offset_ms.is_none())
    }

    pub fn bind_client_clock(&mut self, epoch: u64, offset_ms: i64) -> io::Result<()> {
        if epoch != self.epoch()
            || self
                .client_time_offset_ms
                .is_some_and(|value| value != offset_ms)
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "battle client clock binding changed",
            ));
        }
        self.client_time_offset_ms = Some(offset_ms);
        Ok(())
    }

    pub fn bind_server_clock(&mut self, epoch: u64, offset_ms: i64) -> io::Result<()> {
        if epoch != self.epoch()
            || self
                .server_time_offset_ms
                .is_some_and(|value| value != offset_ms)
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "battle server clock binding changed",
            ));
        }
        self.server_time_offset_ms = Some(offset_ms);
        Ok(())
    }

    pub fn clock_ms(&self) -> io::Result<u64> {
        if !self.worker.is_running() {
            return Err(io::Error::new(
                io::ErrorKind::BrokenPipe,
                "battle owner stopped",
            ));
        }
        self.worker.sender().clock_ms()
    }

    pub fn sender(&self) -> BattleSender {
        self.worker.sender()
    }

    pub(super) fn activator(&self) -> Option<BattleActivator> {
        self.worker.activator()
    }

    pub(super) fn activate(&mut self) -> io::Result<()> {
        self.worker.activate()
    }

    pub fn player_cursor(&self) -> Arc<tokio::sync::Mutex<u64>> {
        self.player_cursor.clone()
    }

    pub fn submit_world(&mut self, payload: &[u8]) -> io::Result<()> {
        let bound = world_input(payload, &self.binding, self.world_batch)?;
        let batch_id = bound.stamp.batch_id;
        self.submit(bound)?;
        self.world_batch = batch_id;
        Ok(())
    }

    pub fn submit_player(&mut self, payload: &[u8]) -> io::Result<()> {
        let cursor = self.player_cursor.clone();
        let sender = self.worker.sender();
        self.runtime.block_on(async {
            let mut last_batch = cursor.lock().await;
            let bound = player_input(payload, &self.binding, *last_batch)?;
            let batch_id = bound.stamp.batch_id;
            tokio::time::timeout(
                SUBMIT_TIMEOUT,
                sender.submit(bound.stamp.epoch, bound.input),
            )
            .await
            .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "battle input timed out"))??;
            *last_batch = batch_id;
            Ok(())
        })
    }

    pub fn submit_motion(&mut self, sample: MotionSample) -> io::Result<()> {
        let bound = motion_input(sample, &self.binding, self.motion_batch, self.world_batch)?;
        let batch_id = bound.stamp.batch_id;
        self.submit(bound)?;
        self.motion_batch = batch_id;
        Ok(())
    }

    pub fn submit_contacts(&mut self, sample: ContactSample) -> io::Result<()> {
        let bound = contacts_input(sample, &self.binding, self.contact_batch, self.world_batch)?;
        let batch_id = bound.stamp.batch_id;
        self.submit(bound)?;
        self.contact_batch = batch_id;
        Ok(())
    }

    fn submit(&mut self, bound: BoundInput) -> io::Result<()> {
        self.runtime.block_on(async {
            tokio::time::timeout(
                SUBMIT_TIMEOUT,
                self.worker.sender().submit(bound.stamp.epoch, bound.input),
            )
            .await
            .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "battle input timed out"))?
        })
    }

    pub(super) fn pending_published(&mut self) -> io::Result<Option<&PublishedBatch>> {
        if self.pending_output.is_none() {
            self.pending_output = match self.worker.try_recv_contextual() {
                Ok(batch) => Some(batch),
                Err(tokio::sync::mpsc::error::TryRecvError::Empty) => None,
                Err(tokio::sync::mpsc::error::TryRecvError::Disconnected) => {
                    // Join before reporting closure so timer/publication errors
                    // retain their original cause instead of becoming BrokenPipe.
                    self.worker.stop()?;
                    return Err(io::Error::new(
                        io::ErrorKind::BrokenPipe,
                        "battle owner stopped",
                    ));
                }
            };
        }
        Ok(self.pending_output.as_ref())
    }

    pub fn pending_output(&mut self) -> io::Result<Option<&[Output]>> {
        Ok(self
            .pending_published()?
            .map(|batch| batch.outputs.as_slice()))
    }

    pub fn take_pending_output(&mut self) -> Option<Vec<Output>> {
        self.pending_output.take().map(|batch| batch.outputs)
    }

    pub fn try_recv(&mut self) -> io::Result<Option<Vec<Output>>> {
        self.pending_output()?;
        Ok(self.take_pending_output())
    }

    pub fn stop(&mut self) -> io::Result<()> {
        self.worker.stop()
    }
}

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}
