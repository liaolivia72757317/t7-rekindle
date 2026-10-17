from pathlib import Path

from Business.scripts.codec import move_flow


ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/bot-move-wire-v1.txt'
TURN_ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/bot-turn-wire-v1.txt'


def test_bot_move_wire_vectors_match_original_codec():
    for line in ROWS.read_text(encoding='ascii').splitlines():
        if not line or line.startswith('#'):
            continue
        (name, tick, rid, state, lateral, forward, current, maximum,
         x, y, z, heading, group, active, acceleration, body) = line.split()
        encoded = move_flow.encode_move_bc_with_system_and_active(
            server_tick=int(tick), target_instance_id=int(rid), state=int(state),
            left_right=int(lateral), forward_back=int(forward), current_velocity=int(current),
            max_velocity=int(maximum), position=(float(x), float(y), float(z)),
            direction_yaw=int(heading), system_group=int(group), active=int(active),
            acceleration=int(acceleration))
        assert encoded.hex() == body, name
        assert len(encoded) == move_flow.MOVE_BC_WITH_SYSTEM_AND_ACTIVE_BODY_LENGTH


def test_bot_turn_wire_matches_original_codec():
    for line in TURN_ROWS.read_text(encoding='ascii').splitlines():
        if not line or line.startswith('#'):
            continue
        name, tick, rid, yaw, body = line.split()
        encoded = move_flow.encode_move_in_air_direct_bc(
            server_tick=int(tick), target_instance_id=int(rid), direction_yaw=int(yaw),
            left_right=0, forward_back=0)
        assert encoded.hex() == body, name
