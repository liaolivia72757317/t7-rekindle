use super::*;
use crate::{
    battle_session::{Member, MemberKind, Output, PreparedHero},
    gateway::loadout::{Catalogue, RoundFormation},
};

impl InstanceIngress {
    pub(in crate::gateway) async fn formation(
        &self,
        catalogue: &Catalogue,
        mailbox: Arc<Mutex<crate::gateway::platform::PlatformMailbox>>,
    ) -> io::Result<Arc<RoundFormation>> {
        if let Some(formation) = self.formation.get() {
            return Ok(formation.clone());
        }
        let actor = &self.roster.local;
        let member = Member {
            actor_id: actor.actor_id,
            kind: MemberKind::Human,
            sample_source_id: self.binding.source_id,
            camp: actor.camp,
            hero_id: actor.hero_id,
            weapon_id: actor.weapon_id,
            equipment: actor.equipment.clone(),
        };
        let formation = Arc::new(
            if t7r_battle_resources::training_maps::contains(self.map_id()) {
                RoundFormation::training(&self.resources, &member)?
            } else {
                RoundFormation::read(mailbox, catalogue, &self.resources, &member).await?
            },
        );
        self.formation
            .set(formation.clone())
            .map_err(|_| invalid("round formation is already frozen"))?;
        Ok(formation)
    }

    pub(in crate::gateway) async fn select_hero(
        &self,
        selected: Arc<PreparedHero>,
    ) -> io::Result<bool> {
        if selected.member.actor_id != self.actor_id()
            || selected.member.sample_source_id != self.binding.source_id
        {
            return Err(invalid("selection differs from bound instance member"));
        }
        tokio::time::timeout(
            SUBMIT_TIMEOUT,
            self.sender.select_hero(self.binding.epoch, selected),
        )
        .await
        .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "instance hero selection timed out"))?
    }

    pub(in crate::gateway) fn roster_for_outputs(
        &self,
        outputs: &[Output],
    ) -> io::Result<SceneRoster> {
        let mut roster = self.roster.clone();
        let actor = outputs.iter().find_map(|output| match output {
            Output::State(state) => state
                .actors
                .iter()
                .find(|actor| actor.actor_id == self.actor_id()),
            _ => None,
        });
        if let Some(actor) = actor {
            if let Some(formation) = self.formation.get() {
                let selected = formation
                    .choice(formation.position(actor.hero_id)?)
                    .ok_or_else(|| invalid("changed hero is absent from round formation"))?;
                roster.local.hero_id = selected.member.hero_id;
                roster.local.max_health = selected.max_health();
                roster.local.equipment = selected.member.equipment.clone();
                roster.local_shields = selected.shields();
            } else if actor.hero_id != roster.local.hero_id {
                return Err(invalid("changed hero has no round formation"));
            }
            roster.local.weapon_id = actor.weapon_id;
            roster.local.offhand_id = actor.offhand_id;
        }
        Ok(roster)
    }
}
