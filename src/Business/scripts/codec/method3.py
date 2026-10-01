"""AES-CBC codec for the dynamically verified TPDU method-3 payload."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from .protocol import TPDU_COMMAND_NONE, decode_sh_package, decode_tpdu_frame

AES_BLOCK_SIZE = 16
METHOD3_IV = bytes(range(AES_BLOCK_SIZE))
METHOD3_MARKER = b"tsf4g"

_SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76"
    "ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d83115"
    "04c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f84"
    "53d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa8"
    "51a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d1973"
    "60814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479"
    "e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a"
    "703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df"
    "8ca1890dbfe6426841992d0fb054bb16"
)
_INV_SBOX = bytes(_SBOX.index(value) for value in range(256))
_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


@dataclass(frozen=True, slots=True)
class Method3UplinkMessage:
    sequence: int
    command_id: int
    server_time_ms: int
    body: bytes


def _require_key_and_iv(key: bytes, iv: bytes) -> None:
    if not isinstance(key, bytes):
        raise TypeError("key must be bytes")
    if len(key) != AES_BLOCK_SIZE:
        raise ValueError(f"method-3 key must contain exactly {AES_BLOCK_SIZE} bytes")
    if not isinstance(iv, bytes):
        raise TypeError("iv must be bytes")
    if len(iv) != AES_BLOCK_SIZE:
        raise ValueError(f"method-3 IV must contain exactly {AES_BLOCK_SIZE} bytes")


def _xtime(value: int) -> int:
    return ((value << 1) ^ (0x11B if value & 0x80 else 0)) & 0xFF


def _multiply(left: int, right: int) -> int:
    result = 0
    while right:
        if right & 1:
            result ^= left
        left = _xtime(left)
        right >>= 1
    return result


def _expand_key(key: bytes) -> tuple[bytes, ...]:
    expanded = bytearray(key)
    generated = len(expanded)
    rcon_index = 0
    temporary = bytearray(4)
    while generated < 176:
        temporary[:] = expanded[generated - 4:generated]
        if generated % 16 == 0:
            temporary[:] = temporary[1:] + temporary[:1]
            temporary[:] = bytes(_SBOX[value] for value in temporary)
            temporary[0] ^= _RCON[rcon_index]
            rcon_index += 1
        for value in temporary:
            expanded.append(expanded[generated - 16] ^ value)
            generated += 1
    return tuple(bytes(expanded[offset:offset + 16]) for offset in range(0, 176, 16))


def _add_round_key(state: list[int], round_key: bytes) -> None:
    for index, value in enumerate(round_key):
        state[index] ^= value


def _shift_rows(state: list[int]) -> None:
    state[1], state[5], state[9], state[13] = state[5], state[9], state[13], state[1]
    state[2], state[6], state[10], state[14] = state[10], state[14], state[2], state[6]
    state[3], state[7], state[11], state[15] = state[15], state[3], state[7], state[11]


def _inverse_shift_rows(state: list[int]) -> None:
    state[1], state[5], state[9], state[13] = state[13], state[1], state[5], state[9]
    state[2], state[6], state[10], state[14] = state[10], state[14], state[2], state[6]
    state[3], state[7], state[11], state[15] = state[7], state[11], state[15], state[3]


def _mix_columns(state: list[int]) -> None:
    for offset in range(0, AES_BLOCK_SIZE, 4):
        first, second, third, fourth = state[offset:offset + 4]
        combined = first ^ second ^ third ^ fourth
        state[offset] ^= combined ^ _xtime(first ^ second)
        state[offset + 1] ^= combined ^ _xtime(second ^ third)
        state[offset + 2] ^= combined ^ _xtime(third ^ fourth)
        state[offset + 3] ^= combined ^ _xtime(fourth ^ first)


def _inverse_mix_columns(state: list[int]) -> None:
    for offset in range(0, AES_BLOCK_SIZE, 4):
        first, second, third, fourth = state[offset:offset + 4]
        state[offset] = (
            _multiply(first, 14) ^ _multiply(second, 11) ^ _multiply(third, 13) ^ _multiply(fourth, 9)
        )
        state[offset + 1] = (
            _multiply(first, 9) ^ _multiply(second, 14) ^ _multiply(third, 11) ^ _multiply(fourth, 13)
        )
        state[offset + 2] = (
            _multiply(first, 13) ^ _multiply(second, 9) ^ _multiply(third, 14) ^ _multiply(fourth, 11)
        )
        state[offset + 3] = (
            _multiply(first, 11) ^ _multiply(second, 13) ^ _multiply(third, 9) ^ _multiply(fourth, 14)
        )


def _encrypt_block(block: bytes, round_keys: tuple[bytes, ...]) -> bytes:
    state = list(block)
    _add_round_key(state, round_keys[0])
    for round_key in round_keys[1:10]:
        state[:] = (_SBOX[value] for value in state)
        _shift_rows(state)
        _mix_columns(state)
        _add_round_key(state, round_key)
    state[:] = (_SBOX[value] for value in state)
    _shift_rows(state)
    _add_round_key(state, round_keys[10])
    return bytes(state)


def _decrypt_block(block: bytes, round_keys: tuple[bytes, ...]) -> bytes:
    state = list(block)
    _add_round_key(state, round_keys[10])
    for round_key in reversed(round_keys[1:10]):
        _inverse_shift_rows(state)
        state[:] = (_INV_SBOX[value] for value in state)
        _add_round_key(state, round_key)
        _inverse_mix_columns(state)
    _inverse_shift_rows(state)
    state[:] = (_INV_SBOX[value] for value in state)
    _add_round_key(state, round_keys[0])
    return bytes(state)


def _cbc_encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    round_keys = _expand_key(key)
    previous = iv
    output = bytearray()
    for offset in range(0, len(data), AES_BLOCK_SIZE):
        block = bytes(left ^ right for left, right in zip(data[offset:offset + AES_BLOCK_SIZE], previous))
        previous = _encrypt_block(block, round_keys)
        output.extend(previous)
    return bytes(output)


def _cbc_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    round_keys = _expand_key(key)
    previous = iv
    output = bytearray()
    for offset in range(0, len(data), AES_BLOCK_SIZE):
        block = data[offset:offset + AES_BLOCK_SIZE]
        decrypted = _decrypt_block(block, round_keys)
        output.extend(left ^ right for left, right in zip(decrypted, previous))
        previous = block
    return bytes(output)


def method3_padding_length(data_length: int) -> int:
    if data_length <= 0:
        raise ValueError("method-3 plaintext must not be empty")
    remainder = data_length % AES_BLOCK_SIZE
    return (AES_BLOCK_SIZE if remainder <= 10 else 2 * AES_BLOCK_SIZE) - remainder


def encode_method3_payload(
    data: bytes,
    key: bytes,
    *,
    iv: bytes = METHOD3_IV,
    random_padding: bytes | None = None,
) -> bytes:
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    _require_key_and_iv(key, iv)
    padding_length = method3_padding_length(len(data))
    random_length = padding_length - len(METHOD3_MARKER) - 1
    if random_padding is None:
        random_padding = secrets.token_bytes(random_length)
    if not isinstance(random_padding, bytes):
        raise TypeError("random_padding must be bytes")
    if len(random_padding) != random_length:
        raise ValueError(
            f"method-3 random padding must contain exactly {random_length} bytes for this payload"
        )
    padded = data + random_padding + METHOD3_MARKER + bytes((padding_length,))
    return _cbc_encrypt(padded, key, iv)


def decode_method3_payload(data: bytes, key: bytes, *, iv: bytes = METHOD3_IV) -> bytes:
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    _require_key_and_iv(key, iv)
    if not data or len(data) % AES_BLOCK_SIZE != 0:
        raise ValueError("method-3 ciphertext must be a non-empty multiple of 16 bytes")
    plaintext = _cbc_decrypt(data, key, iv)
    if plaintext[-6:-1] != METHOD3_MARKER:
        raise ValueError("method-3 plaintext marker is invalid")
    padding_length = plaintext[-1]
    payload_length = len(plaintext) - padding_length
    if payload_length <= 0:
        raise ValueError("method-3 padding leaves no plaintext payload")
    expected_padding_length = method3_padding_length(payload_length)
    if padding_length != expected_padding_length:
        raise ValueError(
            f"method-3 padding length is invalid: expected {expected_padding_length}, got {padding_length}"
        )
    return plaintext[:payload_length]


def decode_method3_uplink_frame(data: bytes, key: bytes, *, iv: bytes = METHOD3_IV) -> Method3UplinkMessage:
    frame = decode_tpdu_frame(data)
    if frame.base.command != TPDU_COMMAND_NONE:
        raise ValueError(f"method-3 uplink requires TPDU NONE command, got 0x{frame.base.command:02X}")
    if frame.base.enc_header_length != 4 or frame.base.header_length != 12:
        raise ValueError("method-3 uplink requires EncHeadLen=4 and HeadLen=12")
    plaintext = decode_method3_payload(frame.body, key, iv=iv)
    if len(plaintext) < 14:
        raise ValueError("method-3 uplink plaintext must contain a sequence and SHPKG_CS header")
    sequence = int.from_bytes(plaintext[:4], "big")
    command_id, server_time_ms, body = decode_sh_package(plaintext[4:])
    return Method3UplinkMessage(sequence, command_id, server_time_ms, body)
