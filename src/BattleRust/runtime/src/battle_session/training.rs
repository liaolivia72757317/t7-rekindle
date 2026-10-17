//! Session-only training. Scores never enter the platform profile or achievements.
use super::*;

pub const INSTRUCTOR_RID: u64 = 61;
pub const DIALOG_ID: i32 = 18;
pub const PREPARE_MS: u64 = 3_000;
pub const ROUND_MS: u64 = 60_000;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Command {
    Ready,
    Talk {
        npc_rid: u64,
        talk_id: i32,
        option: i32,
    },
    Dismiss,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Snapshot {
    pub map_id: u32,
    pub state: i32,
    pub since_ms: u64,
    pub duration_ms: u32,
    pub hits: u32,
    pub blocks: u32,
    pub parries: u32,
    pub score: u32,
    pub show_menu: bool,
}

#[derive(Clone)]
pub(super) struct Training {
    pub snapshot: Snapshot,
    pub player_id: u32,
    ready: bool,
}

impl Training {
    pub fn new(map_id: u32, player_id: u32, now_ms: u64) -> Self {
        Self {
            snapshot: Snapshot {
                map_id,
                state: 0,
                since_ms: now_ms,
                duration_ms: 0,
                hits: 0,
                blocks: 0,
                parries: 0,
                score: 0,
                show_menu: false,
            },
            player_id,
            ready: false,
        }
    }

    pub fn due(&self) -> Option<u64> {
        matches!(self.snapshot.state, 2 | 3)
            .then_some(self.snapshot.since_ms + u64::from(self.snapshot.duration_ms))
    }

    pub fn accepts_contact(&self, at_ms: f64) -> bool {
        self.snapshot.state == 3
            && at_ms >= self.snapshot.since_ms as f64
            && at_ms < self.due().expect("running deadline") as f64
    }

    fn observe(&mut self, events: &[Output]) -> bool {
        let before = self.snapshot.clone();
        for event in events {
            match event {
                Output::Damage {
                    attacker,
                    evidence,
                    result,
                    ..
                } if self.snapshot.map_id == 6006
                    && *attacker == self.player_id
                    && result.hp_damage > 0
                    && self.accepts_contact(evidence.occurred_at_ms) =>
                {
                    self.snapshot.hits += 1;
                }
                Output::Parry {
                    target,
                    evidence,
                    outcome,
                    ..
                } if self.snapshot.map_id == 6009
                    && *target == self.player_id
                    && self.accepts_contact(evidence.occurred_at_ms) =>
                {
                    if *outcome == ParryOutcome::Broken {
                        continue;
                    }
                    if *outcome == ParryOutcome::Chambered {
                        self.snapshot.parries += 1;
                    } else {
                        self.snapshot.blocks += 1;
                    }
                }
                _ => {}
            }
        }
        self.snapshot.score =
            (self.snapshot.hits + self.snapshot.blocks + self.snapshot.parries) * 10;
        before != self.snapshot
    }
}

impl State {
    pub(super) fn training_command(
        &mut self,
        command: Command,
        resources: &BattleResources,
        events: &mut Vec<Output>,
    ) -> io::Result<()> {
        let Some(training) = &mut self.training else {
            return Err(invalid("training command outside a training scene"));
        };
        let mut profile = None;
        let mut return_player = None;
        match command {
            Command::Ready if !training.ready => {
                training.ready = true;
            }
            Command::Dismiss if training.ready && training.snapshot.state == 4 => {
                training.snapshot.state = 0;
                training.snapshot.since_ms = self.now_ms;
                training.snapshot.duration_ms = 0;
                training.snapshot.show_menu = false;
                return_player = Some(training.player_id);
            }
            Command::Talk {
                npc_rid: INSTRUCTOR_RID,
                talk_id: DIALOG_ID,
                option,
            } if training.ready && training.snapshot.state == 0 && matches!(option, 0 | 1) => {
                // Difficulty changes personality only, retaining the existing loadout and AI rules.
                profile = Some(if option == 0 {
                    ai::Profile::Balanced
                } else {
                    ai::Profile::Aggressive
                });
                training.snapshot.state = 2;
                training.snapshot.since_ms = self.now_ms;
                training.snapshot.duration_ms = PREPARE_MS as u32;
                training.snapshot.hits = 0;
                training.snapshot.blocks = 0;
                training.snapshot.parries = 0;
                training.snapshot.score = 0;
                training.snapshot.show_menu = false;
            }
            _ => return Ok(()),
        }
        if let Some(id) = return_player {
            let spawn = resources
                .training_return()
                .ok_or_else(|| invalid("missing training return point"))?;
            self.action(id, action::Command::Cancel, None, events)?;
            self.spawn_actor(id, spawn, resources, events)?;
        }
        if let Some(profile) = profile {
            let configs = self
                .actors
                .values()
                .filter(|actor| actor.member.kind == MemberKind::Bot)
                .map(|actor| {
                    let mut config = self
                        .ai
                        .state(actor.member.actor_id)
                        .expect("bound bot")
                        .config
                        .clone();
                    config.profile = profile;
                    config
                })
                .collect();
            self.ai = ai::Controller::new(self.now_ms, configs).map_err(invalid)?;
            for id in self.actors.keys().copied().collect::<Vec<_>>() {
                let camp = usize::from(self.actors[&id].member.camp - 1);
                self.action(id, action::Command::Cancel, None, events)?;
                self.spawn_actor(id, resources.camps()[camp].spawns()[0], resources, events)?;
            }
        }
        events.push(Output::Training(
            self.training.as_ref().expect("training").snapshot.clone(),
        ));
        Ok(())
    }

    pub(super) fn training_tick(&mut self, events: &mut Vec<Output>) -> io::Result<()> {
        let Some(training) = &mut self.training else {
            return Ok(());
        };
        if !training.due().is_some_and(|due| self.now_ms >= due) {
            return Ok(());
        }
        if training.snapshot.state == 2 {
            training.snapshot.state = 3;
            training.snapshot.since_ms = self.now_ms;
            training.snapshot.duration_ms = ROUND_MS as u32;
        } else {
            training.snapshot.state = 4;
            training.snapshot.since_ms = self.now_ms;
            training.snapshot.duration_ms = 0;
            for id in self.actors.keys().copied().collect::<Vec<_>>() {
                self.action(id, action::Command::Cancel, None, events)?;
                if self.actors[&id].member.kind == MemberKind::Bot {
                    events.push(Output::Bot(ai::BotOutput {
                        actor_id: id,
                        target_id: None,
                        status: "training-result",
                        action: None,
                        movement: ai::MovementIntent::Stop,
                    }));
                }
            }
        }
        events.push(Output::Training(
            self.training.as_ref().expect("training").snapshot.clone(),
        ));
        Ok(())
    }

    pub(super) fn training_score(&mut self, events: &mut Vec<Output>) {
        if let Some(training) = &mut self.training
            && training.observe(events)
        {
            events.push(Output::Training(training.snapshot.clone()));
        }
    }
}
