use super::{ClientBattleMessage, Command, invalid};
use std::io;
use t7r_battle_core::action::WeaponChangeStatus;

pub(crate) fn decode_change(command: u16, body: &[u8]) -> io::Result<Option<Command>> {
    if command != 5 {
        return Ok(None);
    }
    let selector = body
        .get(..2)
        .ok_or_else(|| invalid("weapon selector is truncated"))?;
    if selector != [0, 1] {
        return Ok(None);
    }
    if body.len() != 4 {
        return Err(invalid("invalid weapon change request length"));
    }
    Ok(Some(Command::ChangeSlot(i16::from_be_bytes([
        body[2], body[3],
    ]))))
}

pub(crate) fn change_result(
    status: WeaponChangeStatus,
    weapon_id: u32,
    offhand_id: u32,
) -> io::Result<ClientBattleMessage> {
    let weapon = i32::try_from(weapon_id).map_err(|_| invalid("weapon id exceeds wire range"))?;
    let offhand =
        i32::try_from(offhand_id).map_err(|_| invalid("offhand id exceeds wire range"))?;
    if weapon == 0 {
        return Err(invalid("invalid weapon change result"));
    }
    let result: i32 = match status {
        WeaponChangeStatus::Success => 0,
        WeaponChangeStatus::Busy => 620000,
        WeaponChangeStatus::InvalidSlot => 620001,
    };
    let mut body = 2_u16.to_be_bytes().to_vec();
    for value in [result, weapon, offhand] {
        body.extend_from_slice(&value.to_be_bytes());
    }
    Ok(ClientBattleMessage { command: 5, body })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn signed_slots_are_adjudicated_by_core_but_damaged_layouts_are_rejected() {
        for slot in [i16::MIN, -1, 0, 1, 4, 13, i16::MAX] {
            let body = [&[0, 1][..], &slot.to_be_bytes()].concat();
            assert_eq!(
                decode_change(5, &body).unwrap(),
                Some(Command::ChangeSlot(slot))
            );
        }
        for body in [&[][..], &[0], &[0, 1], &[0, 1, 0], &[0, 1, 0, 13, 0]] {
            assert!(decode_change(5, body).is_err());
        }
        assert_eq!(decode_change(5, &[0, 99]).unwrap(), None);
        assert_eq!(decode_change(6, &[]).unwrap(), None);
        assert!(change_result(WeaponChangeStatus::Success, 0, 0).is_err());
        assert!(change_result(WeaponChangeStatus::Success, 1, u32::MAX).is_err());
    }
}
