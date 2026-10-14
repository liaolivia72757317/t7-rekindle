"""Custom-room requests and directory responses for the client gateway."""

from __future__ import annotations

from dataclasses import dataclass
import struct

from .protocol import _decode_tdr_string, _encode_tdr_string

COMMAND = 59
CREATE_REQUEST = 2
GET_REQUEST = 6
JOIN_REQUEST = 8
QUIT_REQUEST = 10
LIST_REQUEST = 16
LIST_RESPONSE = 17
READY_REQUEST = 18
CAMP_REQUEST = 20
START_REQUEST = 21
CHANNEL_REQUEST = 23
CHANNEL_RESPONSE = 24
MAX_ROOMS_PER_PAGE = 20


@dataclass(frozen=True, slots=True)
class CreateRequest:
    map_id: int
    name: bytes
    password: bytes
    channel_id: int


@dataclass(frozen=True, slots=True)
class GetRequest:
    channel_id: int
    room_number: int


@dataclass(frozen=True, slots=True)
class JoinRequest:
    room_guid: int
    password: bytes


@dataclass(frozen=True, slots=True)
class ListRequest:
    channel_id: int
    start: int
    limit: int


@dataclass(frozen=True, slots=True)
class ReadyRequest:
    ready: bool


@dataclass(frozen=True, slots=True)
class CampRequest:
    camp: int


@dataclass(frozen=True, slots=True)
class QuitRequest:
    pass


@dataclass(frozen=True, slots=True)
class StartRequest:
    pass


@dataclass(frozen=True, slots=True)
class ChannelRequest:
    pass


Request = (CreateRequest | GetRequest | JoinRequest | ListRequest | ReadyRequest
           | CampRequest | QuitRequest | StartRequest | ChannelRequest)


class _Reader:
    def __init__(self, body: bytes):
        if not isinstance(body, bytes):
            raise TypeError("body must be bytes")
        self.body, self.offset = body, 0

    def unpack(self, layout: str):
        packet = struct.Struct(">" + layout)
        try:
            values = packet.unpack_from(self.body, self.offset)
        except struct.error as error:
            raise ValueError("custom-room request is truncated") from error
        self.offset += packet.size
        return values

    def string(self, maximum_size: int, field_name: str) -> bytes:
        value, self.offset = _decode_tdr_string(
            self.body, self.offset, maximum_size=maximum_size, field_name=field_name)
        return value

    def finish(self):
        if self.offset != len(self.body):
            raise ValueError("custom-room request has trailing bytes")


def decode_request(body: bytes) -> Request:
    reader = _Reader(body)
    selector, = reader.unpack("H")
    if selector == CREATE_REQUEST:
        map_id, = reader.unpack("i")
        name = reader.string(64, "room name")
        password = reader.string(32, "password")
        channel_id, = reader.unpack("i")
        result = CreateRequest(map_id, name, password, channel_id)
    elif selector == GET_REQUEST:
        result = GetRequest(*reader.unpack("iI"))
    elif selector == JOIN_REQUEST:
        room_guid, = reader.unpack("Q")
        if room_guid == 0:
            raise ValueError("room guid must be positive")
        result = JoinRequest(room_guid, reader.string(32, "password"))
    elif selector == LIST_REQUEST:
        channel_id, start, limit = reader.unpack("iii")
        if start < 0 or not 1 <= limit <= MAX_ROOMS_PER_PAGE:
            raise ValueError("invalid custom-room page")
        result = ListRequest(channel_id, start, limit)
    elif selector == READY_REQUEST:
        status, = reader.unpack("i")
        if status not in (1, 2):
            raise ValueError("invalid ready status")
        result = ReadyRequest(status == 1)
    elif selector == CAMP_REQUEST:
        camp, = reader.unpack("i")
        result = CampRequest(camp)
    elif selector in (QUIT_REQUEST, START_REQUEST, CHANNEL_REQUEST):
        reader.unpack("b")
        result = {QUIT_REQUEST: QuitRequest, START_REQUEST: StartRequest,
                  CHANNEL_REQUEST: ChannelRequest}[selector]()
    else:
        raise ValueError("unsupported custom-room request")
    reader.finish()
    return result


@dataclass(frozen=True, slots=True)
class ChannelInfo:
    channel_id: int
    name: bytes
    network_location: int


@dataclass(frozen=True, slots=True)
class RoomInfo:
    room_guid: int
    room_number: int
    map_id: int
    owner_name: bytes
    name: bytes
    scene_room_id: int
    needs_password: bool
    member_count: int
    gm_privilege: int
    start_time: int


def _pack(layout: str, *values) -> bytes:
    if any(type(value) is not int for value in values):
        raise TypeError("custom-room numeric fields must be integers")
    try:
        return struct.pack(">" + layout, *values)
    except struct.error as error:
        raise ValueError("custom-room field is outside its wire range") from error


def encode_channels(channels: tuple[ChannelInfo, ...], *, result: int = 0) -> bytes:
    if len(channels) > 50:
        raise ValueError("too many custom-room channels")
    parts = [_pack("Hii", CHANNEL_RESPONSE, result, len(channels))]
    for channel in channels:
        parts.extend((_pack("i", channel.channel_id),
                      _encode_tdr_string(channel.name, maximum_size=32, field_name="channel name"),
                      _pack("i", channel.network_location)))
    return b"".join(parts)


def encode_room_list(rooms: tuple[RoomInfo, ...], *, start: int, total: int,
                     result: int = 0) -> bytes:
    if type(start) is not int or type(total) is not int:
        raise TypeError("page offsets must be integers")
    if (start < 0 or total < 0 or len(rooms) > MAX_ROOMS_PER_PAGE
            or (rooms and start + len(rooms) > total)):
        raise ValueError("invalid custom-room page")
    parts = [_pack("Hiiii", LIST_RESPONSE, result, start, len(rooms), total)]
    for room in rooms:
        if room.room_guid <= 0 or room.room_number <= 0 or room.member_count < 0:
            raise ValueError("invalid custom-room entry")
        if type(room.needs_password) is not bool:
            raise TypeError("needs_password must be bool")
        parts.append(_encode_summary(room))
    return b"".join(parts)


def _encode_summary(room: RoomInfo) -> bytes:
    return b"".join((_pack("QIi", room.room_guid, room.room_number, room.map_id),
                     _encode_tdr_string(room.owner_name, maximum_size=32, field_name="owner name"),
                     _encode_tdr_string(room.name, maximum_size=64, field_name="room name"),
                     _pack("QbibQ", room.scene_room_id, int(room.needs_password),
                           room.member_count, room.gm_privilege, room.start_time)))


@dataclass(frozen=True, slots=True)
class MemberInfo:
    user_id: int
    name: bytes
    camp: int
    ready: bool
    level: int = 1
    logic_address: int = 0
    clan_name: bytes = b""
    grade: int = 0


@dataclass(frozen=True, slots=True)
class FullRoomInfo:
    summary: RoomInfo
    owner_user_id: int
    members: tuple[MemberInfo, ...]
    channel_id: int
    password: bytes = b""


EMPTY_ROOM = FullRoomInfo(RoomInfo(0, 0, 0, b"", b"", 0, False, 0, 0, 0), 0, (), 0)


def encode_member(member: MemberInfo) -> bytes:
    if member.user_id <= 0 or member.camp not in (1, 2) or type(member.ready) is not bool:
        raise ValueError("invalid custom-room member")
    return b"".join((_pack("I", member.user_id),
                     _encode_tdr_string(member.name, maximum_size=32, field_name="member name"),
                     _pack("iiii", member.level, member.camp, 1 if member.ready else 2, member.logic_address),
                     _encode_tdr_string(member.clan_name, maximum_size=32, field_name="clan name"),
                     _pack("i", member.grade)))


def encode_full_room(room: FullRoomInfo) -> bytes:
    summary = room.summary
    if len(room.members) > 200 or len(room.members) != summary.member_count:
        raise ValueError("invalid custom-room member count")
    ids = [member.user_id for member in room.members]
    if len(ids) != len(set(ids)) or (ids and room.owner_user_id not in ids):
        raise ValueError("invalid custom-room owner or duplicate members")
    parts = [_pack("QIiI", summary.room_guid, summary.room_number, summary.map_id, room.owner_user_id),
             _encode_tdr_string(summary.owner_name, maximum_size=32, field_name="owner name"),
             _encode_tdr_string(summary.name, maximum_size=64, field_name="room name"),
             _pack("Q", summary.scene_room_id),
             _encode_tdr_string(room.password, maximum_size=32, field_name="password"),
             _pack("ii", len(ids), len(ids))]
    for camp in range(4):
        parts.append(_pack("iiii", sum(member.camp == camp for member in room.members), 0, 0, 0))
    parts.extend(encode_member(member) for member in room.members)
    parts.append(_pack("ibQ", room.channel_id, summary.gm_privilege, summary.start_time))
    return b"".join(parts)


def encode_room_response(selector: int, room: FullRoomInfo = EMPTY_ROOM, *, result: int = 0) -> bytes:
    if selector not in (3, 9):
        raise ValueError("invalid custom-room response")
    return _pack("HI" if selector == 3 else "Hi", selector, result) + encode_full_room(room)


def encode_get_response(room: RoomInfo = EMPTY_ROOM.summary, *, result: int = 0) -> bytes:
    return _pack("Hi", 7, result) + _encode_summary(room)


def encode_result(selector: int, result: int) -> bytes:
    if selector not in (11, 19, 22):
        raise ValueError("invalid custom-room result")
    return _pack("Hi", selector, result)


def encode_room_notify(room: FullRoomInfo) -> bytes:
    return _pack("Hi", 1, 3) + encode_full_room(room)


def encode_dismiss_notify(room_guid: int) -> bytes:
    if room_guid <= 0:
        raise ValueError('room guid must be positive')
    return _pack('HiQ', 1, 2, room_guid)


def encode_member_notify(entry: int, member: MemberInfo) -> bytes:
    if entry not in (4, 5, 6):
        raise ValueError("invalid custom-room member notification")
    return _pack("Hi", 1, entry) + encode_member(member)
