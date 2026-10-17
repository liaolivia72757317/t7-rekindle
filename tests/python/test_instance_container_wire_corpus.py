from pathlib import Path

from Business.scripts import player_profile
from Business.scripts.codec import loadout_flow, login_flow
from Business.scripts.codec.method3 import encode_method3_payload, method3_padding_length
from Business.scripts.codec.protocol import encode_sh_package
from loadout_fixture import catalogue


ROW = Path(__file__).resolve().parents[1] / "fixtures/protocol/instance-container-v1.txt"
LOBBY = Path(__file__).resolve().parents[1] / "fixtures/protocol/loadout-v1.txt"


def test_instance_battle_hero_container_matches_original_profile_and_method3():
    name, sequence, command, body_hex, frame_hex = ROW.read_text(encoding="ascii").split()
    assert name == "battle-hero-container"
    sequence, command = int(sequence), int(command)
    body = bytes.fromhex(body_hex)
    assert login_flow.decode_item_container_request(b"\0\1" + body[2:]) == (2, 7)
    plain = sequence.to_bytes(4, "big") + encode_sh_package(command, 0, body)
    cipher = encode_method3_payload(plain, bytes(16),
                                    random_padding=bytes(method3_padding_length(len(plain)) - 6))
    assert (bytes.fromhex("550e00040000000c") + len(cipher).to_bytes(4, "big") + cipher).hex() == frame_hex
    cat = catalogue()
    profile = player_profile.create(cat)
    lobby = next(line.split() for line in LOBBY.read_text(encoding="ascii").splitlines()
                 if line.startswith("container-2 "))
    frozen = bytes.fromhex(lobby[4].split(":", 1)[1])
    expected = b"\0\x66" + frozen[2:6] + (7).to_bytes(4, "big") + frozen[10:]
    assert loadout_flow.containers(profile, cat, 2, 7, selector=0x66) == [expected]
