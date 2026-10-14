from dataclasses import replace
import struct

import pytest

from Business.scripts.codec import custom_room as codec


def string(value):
    return struct.pack(">I", len(value) + 1) + value + b"\0"


REQUESTS = (
    (struct.pack(">Hi", 2, 1028) + string(b"room") + string(b"") + struct.pack(">i", 1),
     codec.CreateRequest(1028, b"room", b"", 1)),
    (struct.pack(">HiI", 6, 1, 42), codec.GetRequest(1, 42)),
    (struct.pack(">HQ", 8, 0xFFFFFFFFFFFFFFFF) + string(b"password"),
     codec.JoinRequest(0xFFFFFFFFFFFFFFFF, b"password")),
    (struct.pack(">Hiii", 16, 1, 20, 20), codec.ListRequest(1, 20, 20)),
    (struct.pack(">Hi", 18, 1), codec.ReadyRequest(True)),
    (struct.pack(">Hi", 18, 2), codec.ReadyRequest(False)),
    (struct.pack(">Hi", 20, 2), codec.CampRequest(2)),
    (struct.pack(">Hb", 10, 0), codec.QuitRequest()),
    (struct.pack(">Hb", 21, 0), codec.StartRequest()),
    (struct.pack(">Hb", 23, 0), codec.ChannelRequest()),
)


@pytest.mark.parametrize("body,expected", REQUESTS)
def test_requests_preserve_fields_and_consume_the_exact_payload(body, expected):
    assert codec.decode_request(body) == expected
    with pytest.raises(ValueError):
        codec.decode_request(body + b"\0")
    for end in range(len(body)):
        with pytest.raises(ValueError):
            codec.decode_request(body[:end])


@pytest.mark.parametrize("status", (0, -1, 3, 0x7FFFFFFF))
def test_invalid_ready_values_do_not_become_true(status):
    with pytest.raises(ValueError, match="ready status"):
        codec.decode_request(struct.pack(">Hi", 18, status))


@pytest.mark.parametrize("start,limit", ((-1, 1), (0, 0), (0, 21), (0, -1)))
def test_invalid_page_bounds_are_rejected(start, limit):
    with pytest.raises(ValueError):
        codec.decode_request(struct.pack(">Hiii", 16, 1, start, limit))


@pytest.mark.parametrize("encoded", (
    struct.pack(">I", 0),
    struct.pack(">I", 65) + b"a" * 64 + b"\0",
    struct.pack(">I", 2) + b"ab",
    struct.pack(">I", 3) + b"\0a\0",
    struct.pack(">I", 8) + b"a\0",
))
def test_invalid_string_lengths_and_terminators_are_rejected(encoded):
    with pytest.raises(ValueError):
        codec.decode_request(struct.pack(">Hi", 2, 1028) + encoded + string(b"") + struct.pack(">i", 1))


def test_strings_are_bytes_with_bounded_storage_including_the_terminator():
    name = "房".encode("gbk") * 31 + b"a"
    body = struct.pack(">Hi", 2, 1028) + string(name) + string(b"p" * 31) + struct.pack(">i", 1)
    assert codec.decode_request(body).name == name
    with pytest.raises(ValueError):
        codec.decode_request(struct.pack(">HQ", 8, 1) + string(b"p" * 32))
    with pytest.raises(TypeError):
        codec.decode_request(bytearray(body))
    with pytest.raises(ValueError):
        codec.decode_request(struct.pack(">HQ", 8, 0) + string(b""))
    with pytest.raises(ValueError):
        codec.decode_request(struct.pack(">Hb", 999, 0))


def test_channel_array_uses_its_count_without_filling_unused_entries():
    encoded = codec.encode_channels((codec.ChannelInfo(1, b"test", 7),))
    assert encoded == bytes.fromhex(
        "0018 00000000 00000001 00000001 00000005 7465737400 00000007")
    assert codec.encode_channels(()) == bytes.fromhex("0018 00000000 00000000")
    with pytest.raises(ValueError):
        codec.encode_channels((codec.ChannelInfo(1, b"x", 0),) * 51)
    with pytest.raises(ValueError):
        codec.encode_channels((codec.ChannelInfo(1, b"x" * 32, 0),))


def room_info():
    return codec.RoomInfo(0xFEDCBA9876543210, 42, 1028, b"owner", b"room", 0,
                          False, 2, 0, 0)


def test_list_entry_preserves_guid_and_variable_strings_with_no_array_padding():
    encoded = codec.encode_room_list((room_info(),), start=20, total=21)
    expected = bytes.fromhex(
        "0011 00000000 00000014 00000001 00000015"
        "fedcba9876543210 0000002a 00000404"
        "00000006 6f776e657200 00000005 726f6f6d00"
        "0000000000000000 00 00000002 00 0000000000000000")
    assert encoded == expected
    assert codec.encode_room_list((), start=25, total=21) == bytes.fromhex(
        "0011 00000000 00000019 00000000 00000015")


@pytest.mark.parametrize("changes,error", (
    ({"room_guid": 0}, ValueError),
    ({"room_guid": 1 << 64}, ValueError),
    ({"room_number": 1 << 32}, ValueError),
    ({"member_count": -1}, ValueError),
    ({"map_id": 1 << 31}, ValueError),
    ({"name": b"a\0b"}, ValueError),
    ({"name": b"a" * 64}, ValueError),
    ({"owner_name": b"a" * 32}, ValueError),
    ({"needs_password": 1}, TypeError),
    ({"gm_privilege": True}, TypeError),
))
def test_invalid_entries_never_emit_a_partial_packet(changes, error):
    with pytest.raises(error):
        codec.encode_room_list((replace(room_info(), **changes),), start=0, total=1)


def test_list_counts_and_numeric_types_are_checked():
    for rooms, start, total in (((room_info(),) * 21, 0, 21), ((room_info(),), 1, 1),
                               ((), -1, 0), ((), 0, -1), ((), 1 << 31, 0)):
        with pytest.raises(ValueError):
            codec.encode_room_list(rooms, start=start, total=total)
    with pytest.raises(TypeError):
        codec.encode_room_list((), start=True, total=0)


def test_full_room_encodes_four_camp_counters_and_only_the_actual_members():
    info = replace(room_info(), member_count=1)
    member = codec.MemberInfo(0x12345678, b"owner", 1, True)
    room = codec.FullRoomInfo(info, member.user_id, (member,), 1)
    encoded = codec.encode_full_room(room)
    assert encoded[:20] == bytes.fromhex("fedcba9876543210 0000002a 00000404 12345678")
    assert encoded[52:60] == struct.pack(">ii", 1, 1)
    assert encoded[60:124] == struct.pack(">16i", 0, 0, 0, 0, 1, 0, 0, 0, *([0] * 8))
    assert encoded[124:163] == bytes.fromhex(
        "12345678 00000006 6f776e657200 00000001 00000001 00000001 00000000 00000001 00 00000000")
    assert encoded[163:] == struct.pack(">ibQ", 1, 0, 0)
    assert len(encoded) == 176
    assert codec.encode_room_response(3, room) == struct.pack(">HI", 3, 0) + encoded
    assert codec.encode_room_response(9, room) == struct.pack(">Hi", 9, 0) + encoded
    assert codec.encode_room_notify(room) == struct.pack(">Hi", 1, 3) + encoded
    assert codec.encode_member_notify(4, member) == struct.pack(">Hi", 1, 4) + encoded[124:163]
    assert len(codec.encode_room_response(3, result=1)) == 134


def test_full_room_rejects_duplicate_missing_owner_and_inconsistent_counts():
    member = codec.MemberInfo(700, b"owner", 1, False)
    info = replace(room_info(), member_count=1)
    room = codec.FullRoomInfo(info, 700, (member,), 1)
    for invalid in (replace(room, owner_user_id=900), replace(room, members=()),
                    replace(room, members=(member, member), summary=replace(info, member_count=2)),
                    replace(room, members=(replace(member, ready=1),))):
        with pytest.raises(ValueError):
            codec.encode_full_room(invalid)
