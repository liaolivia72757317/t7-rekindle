use super::*;
use t7r_battle_core::navigation::{DEFAULT_RADIUS, PathStatus, Point, SpawnReference};

const SQUAD_ATTACK_COOLDOWN_MS: u64 = 10000;

impl State {
    pub(super) fn request_squad_spawn(
        &mut self,
        actor_id: u32,
        target_id: u32,
        position: Point,
        resources: &BattleResources,
        outputs: &mut Vec<Output>,
    ) -> io::Result<()> {
        match self.squad_spawn(actor_id, target_id, position, resources) {
            Ok(spawn) => self.spawn_actor(actor_id, spawn, resources, outputs),
            Err(result) => {
                outputs.push(Output::SpawnRejected { actor_id, result });
                Ok(())
            }
        }
    }

    fn squad_spawn(
        &self,
        actor_id: u32,
        target_id: u32,
        position: Point,
        resources: &BattleResources,
    ) -> Result<SpawnReference, i32> {
        if !matches!(self.actors[&actor_id].life, Life::Selecting { .. }) {
            return Err(810014);
        }
        let squad = self.squads.for_actor(actor_id).ok_or(810005)?;
        if target_id == actor_id || !squad.members.iter().any(|m| m.actor_id == target_id) {
            return Err(810006);
        }
        let target = self.actors.get(&target_id).ok_or(810014)?;
        if target.life != Life::Alive || target.health == 0 {
            return Err(810014);
        }
        let pose = self.fresh_pose(target_id).ok_or(810014)?;
        if target
            .last_attacked_at_ms
            .is_some_and(|at| self.now_ms.saturating_sub(at) < SQUAD_ATTACK_COOLDOWN_MS)
        {
            return Err(810013);
        }
        if !valid_point(position) {
            return Err(810015);
        }
        let distance = (position[0] - pose.position[0]).hypot(position[1] - pose.position[1]);
        if !(5.0..=10.0).contains(&distance) {
            return Err(810015);
        }
        if (position[2] - pose.position[2]).abs() > 1.0 {
            return Err(810016);
        }
        let ground = resources.navigation().project(position).ok_or(810016)?;
        if (ground[2] - pose.position[2]).abs() > 1.0 {
            return Err(810016);
        }
        let path = resources
            .navigation()
            .find_path(pose.position, ground, DEFAULT_RADIUS)
            .map_err(|_| 810015)?;
        if path.status != PathStatus::Ready {
            return Err(810015);
        }
        for (&id, actor) in &self.actors {
            if id == actor_id {
                continue;
            }
            let other = match actor.life {
                Life::Alive => match self.fresh_pose(id) {
                    Some(pose) => pose.position,
                    None => continue,
                },
                Life::Spawning { spawn, .. } => spawn.position,
                _ => continue,
            };
            if (ground[2] - other[2]).abs() < 1.8
                && (ground[0] - other[0]).hypot(ground[1] - other[1]) < 0.701
            {
                return Err(810015);
            }
        }
        Ok(SpawnReference {
            position: ground,
            heading: pose.heading,
        })
    }

    pub(super) fn mark_attacked(&mut self, actor_id: u32, occurred_at_ms: f64) -> io::Result<()> {
        let actor = self.actor_mut(actor_id)?;
        let at = occurred_at_ms.ceil() as u64;
        actor.last_attacked_at_ms = Some(actor.last_attacked_at_ms.map_or(at, |old| old.max(at)));
        Ok(())
    }
}
