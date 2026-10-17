use super::*;

pub const MAX_SQUADS_PER_CAMP: i32 = 40;
pub const MAX_SQUAD_MEMBERS: usize = 5;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SquadCommand {
    Create,
    Join(i32),
    Leave,
    Dismiss,
    SetJoinable(bool),
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SquadMember {
    pub actor_id: u32,
    pub joined_at_s: u32,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Squad {
    pub id: i32,
    pub camp: u8,
    pub leader: u32,
    pub joinable: bool,
    pub members: Vec<SquadMember>,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct SquadRoster {
    pub entries: Vec<Squad>,
}

impl SquadRoster {
    pub fn with_bots(bots: impl Iterator<Item = (u32, u8)>) -> Self {
        let mut result = Self::default();
        let mut bots: Vec<_> = bots.collect();
        bots.sort_unstable();
        for (actor_id, camp) in bots {
            // Keep one seat per automatic squad available to a human player.
            let squad = result
                .entries
                .iter_mut()
                .find(|s| s.camp == camp && s.members.len() < MAX_SQUAD_MEMBERS - 1);
            if let Some(squad) = squad {
                squad.members.push(SquadMember {
                    actor_id,
                    joined_at_s: 0,
                });
            } else {
                let id = result.entries.iter().filter(|s| s.camp == camp).count() as i32 + 1;
                result.entries.push(Squad {
                    id,
                    camp,
                    leader: actor_id,
                    joinable: true,
                    members: vec![SquadMember {
                        actor_id,
                        joined_at_s: 0,
                    }],
                });
            }
        }
        result
    }

    pub(crate) fn with_local_player(
        bots: impl Iterator<Item = (u32, u8)>,
        (actor_id, camp): (u32, u8),
    ) -> Self {
        let mut result = Self::with_bots(bots);
        let member = SquadMember {
            actor_id,
            joined_at_s: 0,
        };
        if let Some(first) = result
            .entries
            .iter_mut()
            .find(|s| s.camp == camp && s.id == 1)
        {
            first.members.push(member);
        } else {
            result.entries.push(Squad {
                id: 1,
                camp,
                leader: actor_id,
                joinable: true,
                members: vec![member],
            });
        }
        result
    }

    pub fn for_actor(&self, actor_id: u32) -> Option<&Squad> {
        self.entries
            .iter()
            .find(|s| s.members.iter().any(|m| m.actor_id == actor_id))
    }

    pub(crate) fn heap_bytes(&self) -> usize {
        self.entries.capacity() * std::mem::size_of::<Squad>()
            + self
                .entries
                .iter()
                .map(|s| s.members.capacity() * std::mem::size_of::<SquadMember>())
                .sum::<usize>()
    }

    fn apply(&mut self, actor_id: u32, camp: u8, command: SquadCommand, now_ms: u64) -> i32 {
        let current = self
            .entries
            .iter()
            .position(|s| s.members.iter().any(|m| m.actor_id == actor_id));
        if matches!(command, SquadCommand::Create | SquadCommand::Join(_)) && current.is_some() {
            return 810004;
        }
        let member = SquadMember {
            actor_id,
            joined_at_s: (now_ms / 1000).min(u64::from(u32::MAX)) as u32,
        };
        match command {
            SquadCommand::Create => {
                let Some(id) = (1..=MAX_SQUADS_PER_CAMP)
                    .find(|id| self.entries.iter().all(|s| s.camp != camp || s.id != *id))
                else {
                    return 810002;
                };
                self.entries.push(Squad {
                    id,
                    camp,
                    leader: actor_id,
                    joinable: true,
                    members: vec![member],
                });
            }
            SquadCommand::Join(id) => {
                let Some(squad) = self
                    .entries
                    .iter_mut()
                    .find(|s| s.id == id && s.camp == camp)
                else {
                    return 810010;
                };
                if !squad.joinable {
                    return 810008;
                }
                if squad.members.len() >= MAX_SQUAD_MEMBERS {
                    return 810007;
                }
                squad.members.push(member);
            }
            SquadCommand::Leave | SquadCommand::Dismiss | SquadCommand::SetJoinable(_) => {
                let Some(index) = current else {
                    return 810005;
                };
                let squad = &mut self.entries[index];
                if command != SquadCommand::Leave && squad.leader != actor_id {
                    return 810009;
                }
                match command {
                    SquadCommand::SetJoinable(joinable) => squad.joinable = joinable,
                    SquadCommand::Dismiss => {
                        self.entries.remove(index);
                    }
                    _ => {
                        squad.members.retain(|m| m.actor_id != actor_id);
                        if squad.members.is_empty() {
                            self.entries.remove(index);
                        } else if squad.leader == actor_id {
                            squad.leader = squad.members[0].actor_id;
                        }
                    }
                }
            }
        }
        0
    }
}

impl State {
    pub(super) fn squad_command(
        &mut self,
        actor_id: u32,
        command: SquadCommand,
        outputs: &mut Vec<Output>,
    ) {
        let camp = self.actors[&actor_id].member.camp;
        let previous = self.squads.clone();
        let result = Arc::make_mut(&mut self.squads).apply(actor_id, camp, command, self.now_ms);
        outputs.push(Output::Squad {
            actor_id,
            command,
            result,
            previous,
            roster: self.squads.clone(),
        });
    }
}
