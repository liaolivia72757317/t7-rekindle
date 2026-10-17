use super::{platform::Identity, scene_actor_wire::SceneRoster};
use crate::{
    battle_session::{Input, Life, Settlement},
    battle_worker::{BattleActivator, BattleSender},
    client_battle_wire::ClientBattleMessage,
    mailbox::{self, BudgetReceiver, BudgetSender, QueueError},
    semantic_adapter::{SourceBinding, player_input},
};
use std::{
    io,
    mem::size_of,
    sync::atomic::{AtomicU8, Ordering},
    sync::{Arc, Mutex, OnceLock},
    time::Duration,
};
use t7r_battle_core::{
    action::{Command, Direction, Intent},
    battle_semantics::{PlayerIntent, SourceStamp},
};

const SUBMIT_TIMEOUT: Duration = Duration::from_secs(5);
const OUTPUT_BATCH_LIMIT: usize = 32;
const OUTPUT_BYTE_LIMIT: usize = 4 * 1024 * 1024;
const LOGOUT_SENT: u8 = 1;
const ROOM_QUIT_SENT: u8 = 2;
const INSTANCE_DISCONNECTED: u8 = 4;
const PREPARING: u8 = 0;
const MATCH_REQUESTED: u8 = 1;
const INSTANCE_CLAIMED: u8 = 2;
const REARMING: u8 = 3;

pub(super) struct InstanceOutputBatch {
    pub(super) messages: Vec<ClientBattleMessage>,
    pub(super) settlement: Option<Settlement>,
    pub(super) local_life: Option<Life>,
    pub(super) refresh: Box<super::refresh::RefreshUpdate>,
}

pub(super) struct InstanceIngress {
    binding: SourceBinding,
    roster: SceneRoster,
    map_id: u32,
    duration_ms: u64,
    sender: BattleSender,
    activator: Option<BattleActivator>,
    player_cursor: Arc<tokio::sync::Mutex<u64>>,
    scene_identity: Mutex<Option<Identity>>,
    preparation_state: AtomicU8,
    return_state: AtomicU8,
    outgoing: BudgetSender<InstanceOutputBatch>,
    output_receiver: Mutex<Option<BudgetReceiver<InstanceOutputBatch>>>,
    resources: Arc<t7r_battle_resources::ResourceBundle>,
    formation: OnceLock<Arc<super::loadout::RoundFormation>>,
}

impl InstanceIngress {
    pub(super) fn epoch(&self) -> u64 {
        self.binding.epoch
    }

    pub(super) fn actor_id(&self) -> u32 {
        self.roster.local.actor_id
    }

    pub(super) fn client_rid(&self) -> u16 {
        self.roster.local.client_rid
    }

    pub(super) fn map_id(&self) -> u32 {
        self.map_id
    }

    pub(super) fn camp(&self) -> u8 {
        self.roster.local.camp
    }

    pub(super) fn roster(&self) -> &SceneRoster {
        &self.roster
    }

    pub(super) fn bind_identity(&self, identity: Identity) -> io::Result<()> {
        let mut bound = self
            .scene_identity
            .lock()
            .map_err(|_| io::Error::other("instance identity state poisoned"))?;
        if bound.as_ref().is_some_and(|old| old != &identity) {
            return Err(invalid("instance identity changed during battle"));
        }
        *bound = Some(identity);
        Ok(())
    }

    pub(super) fn identity(&self) -> io::Result<Option<Identity>> {
        Ok(self
            .scene_identity
            .lock()
            .map_err(|_| io::Error::other("instance identity state poisoned"))?
            .clone())
    }

    pub(super) fn actor_rids(&self) -> &std::collections::BTreeMap<u32, u16> {
        &self.binding.actor_rids
    }

    pub(super) fn duration_ms(&self) -> u64 {
        self.duration_ms
    }

    pub(super) fn returned_to_room(&self) -> bool {
        let state = self.return_state.load(Ordering::Acquire);
        state & LOGOUT_SENT != 0 && state & (ROOM_QUIT_SENT | INSTANCE_DISCONNECTED) != 0
    }

    pub(super) fn preparation_open(&self) -> bool {
        self.preparation_state.load(Ordering::Acquire) == PREPARING
    }

    pub(super) fn mark_match_requested(&self) -> io::Result<()> {
        self.preparation_state
            .compare_exchange(
                PREPARING,
                MATCH_REQUESTED,
                Ordering::AcqRel,
                Ordering::Acquire,
            )
            .map(|_| ())
            .map_err(|_| invalid("instance match was already requested"))
    }

    pub(super) fn reserve_rearm(&self) -> bool {
        self.preparation_state
            .compare_exchange(PREPARING, REARMING, Ordering::AcqRel, Ordering::Acquire)
            .is_ok()
    }

    pub(super) fn mark_logout_sent(&self) {
        self.return_state.fetch_or(LOGOUT_SENT, Ordering::AcqRel);
    }

    pub(super) fn mark_room_quit_sent(&self) {
        self.return_state.fetch_or(ROOM_QUIT_SENT, Ordering::AcqRel);
    }

    pub(super) fn mark_instance_disconnected(&self) {
        self.return_state
            .fetch_or(INSTANCE_DISCONNECTED, Ordering::AcqRel);
    }

    pub(super) fn activate_match(&self) -> io::Result<()> {
        if let Some(activator) = &self.activator {
            activator.activate()
        } else {
            self.sender.clock_ms().map(|_| ())
        }
    }

    #[allow(clippy::too_many_arguments)]
    pub(super) fn new(
        binding: SourceBinding,
        roster: SceneRoster,
        sender: BattleSender,
        activator: Option<BattleActivator>,
        player_cursor: Arc<tokio::sync::Mutex<u64>>,
        map_id: u32,
        duration_ms: u64,
        resources: Arc<t7r_battle_resources::ResourceBundle>,
    ) -> io::Result<Self> {
        let (outgoing, output_receiver) =
            mailbox::bounded(OUTPUT_BATCH_LIMIT, OUTPUT_BYTE_LIMIT).map_err(invalid)?;
        Ok(Self {
            binding,
            roster,
            map_id,
            duration_ms,
            sender,
            activator,
            player_cursor,
            scene_identity: Mutex::new(None),
            preparation_state: AtomicU8::new(PREPARING),
            return_state: AtomicU8::new(0),
            outgoing,
            output_receiver: Mutex::new(Some(output_receiver)),
            resources,
            formation: OnceLock::new(),
        })
    }

    pub(super) fn claim(&self) -> io::Result<()> {
        self.preparation_state
            .fetch_update(Ordering::AcqRel, Ordering::Acquire, |state| {
                matches!(state, PREPARING | MATCH_REQUESTED).then_some(INSTANCE_CLAIMED)
            })
            .map(|_| ())
            .map_err(|_| invalid("instance source is already bound or rearming"))
    }

    pub(super) fn take_output_receiver(&self) -> io::Result<BudgetReceiver<InstanceOutputBatch>> {
        self.output_receiver
            .lock()
            .map_err(|_| io::Error::other("instance output state poisoned"))?
            .take()
            .ok_or_else(|| invalid("instance output is already bound"))
    }

    pub(super) fn send(&self, messages: Vec<ClientBattleMessage>) -> io::Result<()> {
        self.send_projected(
            messages,
            None,
            None,
            super::refresh::RefreshUpdate::default(),
        )
    }

    pub(super) fn send_projected(
        &self,
        messages: Vec<ClientBattleMessage>,
        settlement: Option<Settlement>,
        local_life: Option<Life>,
        refresh: super::refresh::RefreshUpdate,
    ) -> io::Result<()> {
        if messages.is_empty() && refresh.is_empty() && settlement.is_none() && local_life.is_none()
        {
            return Err(invalid("instance output batch is empty"));
        }
        let mut bytes = size_of::<InstanceOutputBatch>()
            + size_of::<super::refresh::RefreshUpdate>()
            + refresh.heap_bytes();
        for message in &messages {
            if message.body.len() > t7r_battle_core::protocol::MAX_FRAME - 23 {
                return Err(invalid("instance output frame exceeds protocol limit"));
            }
            bytes = bytes
                .checked_add(size_of::<ClientBattleMessage>())
                .and_then(|value| value.checked_add(message.body.len()))
                .ok_or_else(|| invalid("instance output batch size overflow"))?;
        }
        if let Some(settlement) = &settlement {
            bytes = bytes
                .checked_add(
                    settlement.members.len() * size_of::<crate::battle_session::RankedScore>(),
                )
                .ok_or_else(|| invalid("instance settlement batch size overflow"))?;
        }
        self.outgoing
            .try_send(
                InstanceOutputBatch {
                    messages,
                    settlement,
                    local_life,
                    refresh: Box::new(refresh),
                },
                bytes,
            )
            .map_err(|error| match error {
                QueueError::Full(_) => {
                    io::Error::new(io::ErrorKind::WouldBlock, "instance output queue full")
                }
                QueueError::Closed(_) => {
                    io::Error::new(io::ErrorKind::BrokenPipe, "instance output disconnected")
                }
                QueueError::Oversized(_) => invalid("instance output batch exceeds queue budget"),
            })
    }

    pub(super) async fn submit(&self, command: Command) -> io::Result<()> {
        let mut last_batch = self.player_cursor.lock().await;
        let at_ms = match self.sender.clock_ms() {
            Ok(now) => now,
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => return Ok(()),
            Err(error) => return Err(error),
        };
        let batch_id = last_batch
            .checked_add(1)
            .ok_or_else(|| invalid("instance player batch overflow"))?;
        let input = if let Command::ChangeSlot(_) = command {
            // The original signed slot request is private; public intent v1 stays unchanged.
            Input::PlayerAction {
                stamp: SourceStamp {
                    epoch: self.binding.epoch,
                    source_id: self.binding.source_id,
                    batch_id,
                    at_ms,
                },
                actor_id: self.actor_id(),
                command,
            }
        } else {
            let (command, flags) = semantic_command(command)?;
            let value = PlayerIntent {
                stamp: SourceStamp {
                    epoch: self.binding.epoch,
                    source_id: self.binding.source_id,
                    batch_id,
                    at_ms,
                },
                actor_id: self.roster.local.actor_id,
                client_rid: self.roster.local.client_rid,
                command,
                flags,
            };
            let payload = value.encode().map_err(invalid)?;
            let bound = player_input(&payload, &self.binding, *last_batch)?;
            bound.input
        };
        tokio::time::timeout(
            SUBMIT_TIMEOUT,
            self.sender.submit(self.binding.epoch, input),
        )
        .await
        .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "instance input timed out"))??;
        *last_batch = batch_id;
        Ok(())
    }

    pub(super) async fn submit_quit(&self) -> io::Result<()> {
        tokio::time::timeout(
            SUBMIT_TIMEOUT,
            self.sender.submit(self.binding.epoch, Input::Quit),
        )
        .await
        .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "instance quit timed out"))?
    }

    pub(super) async fn submit_training(
        &self,
        command: crate::battle_session::training::Command,
    ) -> io::Result<()> {
        tokio::time::timeout(
            SUBMIT_TIMEOUT,
            self.sender.submit(
                self.binding.epoch,
                Input::Training {
                    source_id: self.binding.source_id,
                    actor_id: self.actor_id(),
                    command,
                },
            ),
        )
        .await
        .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "training input timed out"))?
    }

    pub(super) async fn submit_spawn(&self) -> io::Result<()> {
        tokio::time::timeout(
            SUBMIT_TIMEOUT,
            self.sender.submit(
                self.binding.epoch,
                Input::SourceSpawn {
                    source_id: self.binding.source_id,
                    actor_id: self.roster.local.actor_id,
                },
            ),
        )
        .await
        .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "instance spawn timed out"))?
    }

    pub(super) async fn submit_squad(
        &self,
        command: crate::battle_session::SquadCommand,
    ) -> io::Result<()> {
        self.submit_squad_input(Input::SourceSquad {
            source_id: self.binding.source_id,
            actor_id: self.actor_id(),
            command,
        })
        .await
    }

    pub(super) async fn submit_squad_spawn(
        &self,
        target_id: u32,
        position: [f64; 3],
    ) -> io::Result<()> {
        self.submit_squad_input(Input::SourceSquadSpawn {
            source_id: self.binding.source_id,
            actor_id: self.actor_id(),
            target_id,
            position,
        })
        .await
    }

    async fn submit_squad_input(&self, input: Input) -> io::Result<()> {
        tokio::time::timeout(
            SUBMIT_TIMEOUT,
            self.sender.submit(self.binding.epoch, input),
        )
        .await
        .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "instance squad input timed out"))?
    }
}

#[path = "instance_selection.rs"]
mod selection;

fn semantic_command(command: Command) -> io::Result<(u8, u8)> {
    let direction = |direction| match direction {
        Direction::Up => 0,
        Direction::Right => 1,
        Direction::Down => 2,
        Direction::Left => 3,
    };
    Ok(match command {
        Command::Press {
            intent,
            defence_down,
            dash_run,
            dash_forward,
        } => {
            let command = match intent {
                Intent::Attack(value) => 1 + direction(value),
                Intent::Guard(value) => 5 + direction(value),
                Intent::Shield => 9,
                Intent::Dash => 10,
            };
            let flags =
                u8::from(defence_down) | (u8::from(dash_run) << 1) | (u8::from(dash_forward) << 2);
            (command, flags)
        }
        Command::ReleaseAttack => (11, 0),
        Command::ReleaseGuard => (12, 0),
        _ => return Err(invalid("unsupported instance player command")),
    })
}

fn invalid(message: impl std::fmt::Display) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn original_commands_have_canonical_semantic_ids_and_flags() {
        assert_eq!(
            semantic_command(Command::Press {
                intent: Intent::Guard(Direction::Down),
                defence_down: true,
                dash_run: false,
                dash_forward: true,
            })
            .unwrap(),
            (7, 5)
        );
        assert_eq!(semantic_command(Command::ReleaseAttack).unwrap(), (11, 0));
        assert_eq!(semantic_command(Command::ReleaseGuard).unwrap(), (12, 0));
        assert!(semantic_command(Command::Cancel).is_err());
    }
}
