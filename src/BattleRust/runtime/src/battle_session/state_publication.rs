use super::*;
use t7r_battle_core::battle_semantics::state::{MatchState, StateActor};

const CHECKPOINT_INTERVAL_MS: u64 = 1000;

impl State {
    pub(super) fn append_state_output(
        &mut self,
        previous: &State,
        epoch: u64,
        outputs: &mut Vec<Output>,
    ) -> io::Result<()> {
        if previous.settlement.is_some() {
            return Ok(());
        }
        let mut changed = Vec::new();
        for (&id, actor) in &self.actors {
            let guard = self.guard_meters[&id].value();
            let old = previous.actors.get(&id);
            if old.is_some_and(|old| Arc::ptr_eq(actor, old))
                && previous.guard_meters[&id].value() == guard
            {
                continue;
            }
            let current = state_actor(actor, guard)?;
            let before = old
                .map(|old| state_actor(old, previous.guard_meters[&id].value()))
                .transpose()?;
            if before.as_ref() != Some(&current) {
                changed.push(current);
            }
        }
        let checkpoint = previous.state_revision == 0
            || self.now_ms - previous.last_checkpoint_at_ms >= CHECKPOINT_INTERVAL_MS;
        if changed.is_empty() && !checkpoint {
            return Ok(());
        }
        let revision = if previous.state_revision == 0 {
            1
        } else if changed.is_empty() {
            previous.state_revision
        } else {
            previous
                .state_revision
                .checked_add(1)
                .ok_or_else(|| invalid("match state revision overflow"))?
        };
        let actors = if checkpoint {
            self.actors
                .iter()
                .map(|(&id, actor)| state_actor(actor, self.guard_meters[&id].value()))
                .collect::<io::Result<Vec<_>>>()?
        } else {
            changed
        };
        let state = MatchState {
            mode: if checkpoint { 2 } else { 1 },
            epoch,
            revision,
            base_revision: if checkpoint {
                0
            } else {
                previous.state_revision
            },
            at_ms: self.now_ms,
            actors,
        };
        state.encode().map_err(invalid)?;
        self.state_revision = revision;
        if checkpoint {
            self.last_checkpoint_at_ms = self.now_ms;
        }
        let at = outputs
            .iter()
            .position(|event| matches!(event, Output::Ended(_)))
            .unwrap_or(outputs.len());
        outputs.insert(at, Output::State(state));
        Ok(())
    }
}

fn state_actor(actor: &Actor, guard: f64) -> io::Result<StateActor> {
    if actor.shields.len() > 4 {
        return Err(invalid("too many shield states for public projection"));
    }
    let mut shields = [(0, 0); 4];
    for (index, (&weapon_id, &health)) in actor.shields.iter().enumerate() {
        shields[index] = (weapon_id, health);
    }
    let action = actor.action.snapshot();
    let (life, life_at_ms, respawn_at_ms, spawn_position, spawn_heading) = match actor.life {
        Life::Alive => (1, 0, 0, [0.; 3], 0.),
        Life::Dead {
            died_at_ms,
            respawn_at_ms,
        } => (2, died_at_ms, respawn_at_ms, [0.; 3], 0.),
        Life::Selecting { died_at_ms } => (3, died_at_ms, 0, [0.; 3], 0.),
        Life::Waiting { died_at_ms } => (4, died_at_ms, 0, [0.; 3], 0.),
        Life::Spawning {
            spawned_at_ms,
            spawn,
        } => (5, spawned_at_ms, 0, spawn.position, spawn.heading),
    };
    let intent = match action.intent {
        None => 0,
        Some(Intent::Attack(Direction::Up)) => 1,
        Some(Intent::Attack(Direction::Right)) => 2,
        Some(Intent::Attack(Direction::Down)) => 3,
        Some(Intent::Attack(Direction::Left)) => 4,
        Some(Intent::Guard(Direction::Up)) => 5,
        Some(Intent::Guard(Direction::Right)) => 6,
        Some(Intent::Guard(Direction::Down)) => 7,
        Some(Intent::Guard(Direction::Left)) => 8,
        Some(Intent::Shield) => 9,
        Some(Intent::Dash) => 10,
    };
    let phase = match action.phase {
        Phase::Idle => 0,
        Phase::Prepare => 1,
        Phase::Hold => 2,
        Phase::Strike => 3,
        Phase::Recovery => 4,
        Phase::WeaponChange => 5,
        Phase::Parried => 6,
        Phase::Terrain => 7,
        Phase::ParrySuccess => 8,
        Phase::Hurt => 9,
    };
    Ok(StateActor {
        actor_id: actor.member.actor_id,
        camp: actor.member.camp,
        kind: match actor.member.kind {
            MemberKind::Human => 1,
            MemberKind::Bot => 2,
        },
        life,
        intent,
        phase,
        flags: u8::from(action.released),
        sequence: action.sequence,
        duration_ms: action.duration_ms,
        hero_id: actor.member.hero_id,
        health: actor.health,
        guard,
        score: [
            actor.score.kills,
            actor.score.deaths,
            actor.score.points,
            actor.score.streak,
        ],
        weapon_id: action.weapon_id,
        offhand_id: action.offhand_id,
        main_weapon_id: action.main_weapon_id,
        shield_count: actor.shields.len() as u8,
        shields,
        action_changed_at_ms: action.changed_at_ms,
        life_at_ms,
        respawn_at_ms,
        spawn_position,
        spawn_heading,
    })
}
