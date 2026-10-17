use super::*;

pub(super) fn project(
    event: &ActionEvent,
    state: &t7r_battle_core::battle_semantics::state::MatchState,
    scene: &SceneProjection<'_>,
    server_time_offset_ms: i64,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<ClientBattleMessage> {
    let ActionEvent::EquipmentChanged {
        actor_id,
        weapon_id,
        offhand_id,
    } = *event
    else {
        return Err(invalid("expected equipment change event"));
    };
    let actor = state
        .actors
        .iter()
        .find(|actor| actor.actor_id == actor_id)
        .ok_or_else(|| invalid("equipment actor is absent from authoritative state"))?;
    if actor.weapon_id != weapon_id || actor.offhand_id != offhand_id {
        return Err(invalid("equipment differs from authoritative state"));
    }
    let visible = std::iter::once(&scene.roster.local)
        .chain(&scene.roster.bots)
        .find(|member| member.actor_id == actor_id && member.hero_id == actor.hero_id)
        .ok_or_else(|| invalid("equipment actor is absent from client scene"))?;
    client_battle_wire::encode_equipment_update_for_client(
        actor_id,
        &visible.equipment,
        weapon_id,
        offhand_id,
        client_battle_wire::wire_time(state.at_ms, server_time_offset_ms)?,
        actor_rids,
    )
}
