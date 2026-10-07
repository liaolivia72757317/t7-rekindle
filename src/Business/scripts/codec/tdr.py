"""Read-only discovery of embedded Tencent TDR metadata and enum IDs."""

from __future__ import annotations

import hashlib
import re
import struct

from .errors import M3AnalysisError
from .models import TdrBlock, TdrEnum, TdrEnumItem, TdrField, TdrMacro, TdrStruct, TdrUnion

TDR_MAGIC = b"\xd6\x02\x0b\x00\x20\x00\x00\x00"
TDR_MINIMUM_LENGTH = 0x114
TDR_LENGTH_OFFSET = 0x08
TDR_NAME_OFFSET = 0x94
TDR_NAME_LENGTH = 0x80
METALIB_DATA_BASE = 0x114
MACRO_COUNT_OFFSET = 0x34
MACRO_GROUP_COUNT_OFFSET = 0x3C
MACRO_TABLE_OFFSET = 0x4C
MACRO_GROUP_INDEX_OFFSET = 0x70
MACRO_DESCRIPTOR_SIZE = 0x10
MACRO_NAME_OFFSET = 0x00
MACRO_VALUE_OFFSET = 0x04
MACRO_DESCRIPTION_OFFSET = 0x08
MACRO_GROUP_INDEX_ITEM_SIZE = 0x08
MACRO_GROUP_HEADER_SIZE = 0x94
MACRO_GROUP_COUNT_FIELD_OFFSET = 0x00
MACRO_GROUP_MAX_COUNT_OFFSET = 0x04
MACRO_GROUP_DESCRIPTION_OFFSET = 0x08
MACRO_GROUP_LOOKUP_INDEX_OFFSET = 0x0C
MACRO_GROUP_DECLARATION_INDEX_OFFSET = 0x10
MACRO_GROUP_NAME_OFFSET = 0x14
MACRO_GROUP_NAME_LENGTH = 0x80
META_COUNT_OFFSET = 0x28
META_INDEX_OFFSET = 0x58
META_INDEX_ITEM_SIZE = 8
META_HEADER_SIZE = 0xB8
META_KIND_OFFSET = 0x10
META_FIELD_COUNT_OFFSET = 0x2C
META_SELF_OFFSET = 0x3C
META_NAME_OFFSET = 0x84
META_DESCRIPTION_OFFSET = 0x88
META_KIND_STRUCT = 1
META_KIND_UNION = 0
FIELD_DESCRIPTOR_SIZE = 0xB4
FIELD_SELECTOR_OFFSET = 0x00
FIELD_NAME_OFFSET = 0x0C
FIELD_STORAGE_SIZE_OFFSET = 0x18
FIELD_DECLARED_SIZE_OFFSET = 0x20
FIELD_COUNT_OFFSET = 0x24
FIELD_MEMORY_OFFSET = 0x2C
FIELD_SELECTOR_MACRO_OFFSET = 0x30
FIELD_PRIMITIVE_TYPE_OFFSET = 0x3C
FIELD_FLAGS_OFFSET = 0x44
FIELD_LENGTH_PREFIX_SIZE_OFFSET = 0x50
FIELD_REFER_OFFSET = 0x5C
FIELD_SELECT_OFFSET = 0x68
FIELD_CUSTOM_TYPE_OFFSET = 0x78
FIELD_DESCRIPTION_OFFSET = 0x90
NO_OFFSET = 0xFFFFFFFF

# Indexes are verified against the 0x58-byte builtin-type descriptor table used
# by the current baseline TDR runtime. Keep aliases exactly as emitted to XML.
TDR_PRIMITIVE_TYPES = (
    "union",
    "struct",
    "tinyint",
    "tinyuint",
    "smallint",
    "smalluint",
    "int",
    "uint",
    "bigint",
    "biguint",
    "int8",
    "uint8",
    "int16",
    "uint16",
    "int32",
    "uint32",
    "int64",
    "uint64",
    "float",
    "double",
    "decimal",
    "date",
    "time",
    "datetime",
    "string",
    "byte",
    "ip",
    "wchar",
    "wstring",
    "void",
    "char",
    "uchar",
    "short",
    "ushort",
    "long",
    "ulong",
    "longlong",
    "ulonglong",
    "money",
)


def _read_u32(data: bytes, offset: int, *, source: str) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise M3AnalysisError("cannot read a 32-bit value", source=source, offset=offset)
    return struct.unpack_from("<I", data, offset)[0]


def _read_i32(data: bytes, offset: int, *, source: str) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise M3AnalysisError("cannot read a signed 32-bit value", source=source, offset=offset)
    return struct.unpack_from("<i", data, offset)[0]


def _read_fixed_ascii(data: bytes, offset: int, length: int, *, source: str) -> str:
    if offset < 0 or offset + length > len(data):
        raise M3AnalysisError("fixed string exceeds input range", source=source, offset=offset)
    raw = data[offset : offset + length].split(b"\0", 1)[0]
    if not raw:
        raise M3AnalysisError("fixed string is empty", source=source, offset=offset)
    try:
        value = raw.decode("ascii")
    except UnicodeDecodeError as error:
        raise M3AnalysisError("fixed string is not ASCII", source=source, offset=offset) from error
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", value):
        raise M3AnalysisError("fixed string contains unsupported characters", source=source, offset=offset)
    return value


def _read_cstring(data: bytes, offset: int, *, source: str, field: str) -> str:
    if offset < 0 or offset >= len(data):
        raise M3AnalysisError(f"{field} offset exceeds input range", source=source, offset=offset)
    end_offset = data.find(b"\0", offset)
    if end_offset < 0:
        raise M3AnalysisError(f"{field} is not terminated", source=source, offset=offset)
    try:
        return data[offset:end_offset].decode("gbk")
    except UnicodeDecodeError as error:
        raise M3AnalysisError(f"{field} is not valid GBK", source=source, offset=offset) from error


def _relative_offset(data: bytes, value: int, *, source: str, field_offset: int) -> int:
    if value == NO_OFFSET:
        raise M3AnalysisError("required TDR relative offset is absent", source=source, offset=field_offset)
    absolute = METALIB_DATA_BASE + value
    if absolute < METALIB_DATA_BASE or absolute >= len(data):
        raise M3AnalysisError("TDR relative offset exceeds the block", source=source, offset=field_offset)
    return absolute


def find_tdr_blocks(data: bytes, *, source: str) -> tuple[TdrBlock, ...]:
    """Find exact, bounded TDR metalibs without executing the containing image."""

    blocks: list[TdrBlock] = []
    cursor = 0
    while True:
        offset = data.find(TDR_MAGIC, cursor)
        if offset < 0:
            break
        if offset + TDR_MINIMUM_LENGTH > len(data):
            raise M3AnalysisError("truncated TDR header", source=source, offset=offset)
        length = _read_u32(data, offset + TDR_LENGTH_OFFSET, source=source)
        if length < TDR_MINIMUM_LENGTH:
            raise M3AnalysisError(
                f"TDR length {length} is smaller than the minimum header",
                source=source,
                offset=offset + TDR_LENGTH_OFFSET,
            )
        end_offset = offset + length
        if end_offset < offset or end_offset > len(data):
            raise M3AnalysisError(
                f"TDR range ending at 0x{end_offset:X} exceeds source size 0x{len(data):X}",
                source=source,
                offset=offset,
            )
        block_data = data[offset:end_offset]
        metalib_name = _read_fixed_ascii(
            block_data,
            TDR_NAME_OFFSET,
            TDR_NAME_LENGTH,
            source=source,
        )
        blocks.append(
            TdrBlock(
                source=source,
                file_offset=offset,
                length=length,
                metalib_name=metalib_name,
                sha256=hashlib.sha256(block_data).hexdigest(),
                data=block_data,
            )
        )
        cursor = end_offset
    return tuple(blocks)


def _direction(description: str) -> str:
    normalized = re.sub(r"\s+", "", description.lower())
    client_to_server = ("client->logic", "client->server", "c->s", "客户端->", "上行")
    server_to_client = ("logic->client", "server->client", "s->c", "->客户端", "下行")
    if any(marker in normalized for marker in client_to_server):
        return "C→S"
    if any(marker in normalized for marker in server_to_client):
        return "S→C"
    return "unknown"


def parse_tdr_macro(block: TdrBlock, *, macro_name: str) -> TdrMacro:
    """Recover one exact global macro descriptor and its value."""

    try:
        encoded_name = macro_name.encode("ascii")
    except UnicodeEncodeError as error:
        raise M3AnalysisError("macro name is not ASCII", source=block.source, offset=0) from error
    if not encoded_name or b"\0" in encoded_name:
        raise M3AnalysisError("macro name is empty or contains NUL", source=block.source, offset=0)

    data = block.data
    macro_count = _read_u32(data, MACRO_COUNT_OFFSET, source=block.source)
    macro_table = _relative_offset(
        data,
        _read_u32(data, MACRO_TABLE_OFFSET, source=block.source),
        source=block.source,
        field_offset=MACRO_TABLE_OFFSET,
    )
    if macro_count == 0 or macro_table + macro_count * MACRO_DESCRIPTOR_SIZE > len(data):
        raise M3AnalysisError("TDR macro table exceeds the block", source=block.source, offset=macro_table)

    matches: list[TdrMacro] = []
    for macro_index in range(macro_count):
        macro_offset = macro_table + macro_index * MACRO_DESCRIPTOR_SIZE
        name_offset = _relative_offset(
            data,
            _read_u32(data, macro_offset + MACRO_NAME_OFFSET, source=block.source),
            source=block.source,
            field_offset=macro_offset + MACRO_NAME_OFFSET,
        )
        symbolic_name = _read_cstring(data, name_offset, source=block.source, field="macro name")
        if symbolic_name.encode("gbk") != encoded_name:
            continue
        description_relative = _read_u32(
            data,
            macro_offset + MACRO_DESCRIPTION_OFFSET,
            source=block.source,
        )
        description = ""
        if description_relative != NO_OFFSET:
            description = _read_cstring(
                data,
                _relative_offset(
                    data,
                    description_relative,
                    source=block.source,
                    field_offset=macro_offset + MACRO_DESCRIPTION_OFFSET,
                ),
                source=block.source,
                field="macro description",
            )
        matches.append(
            TdrMacro(
                symbolic_name=symbolic_name,
                value=_read_u32(data, macro_offset + MACRO_VALUE_OFFSET, source=block.source),
                macro_index=macro_index,
                description=description,
                string_offset=name_offset,
            )
        )
    if len(matches) != 1:
        raise M3AnalysisError(
            f"expected one macro named {macro_name!r}, found {len(matches)}",
            source=block.source,
            offset=macro_table,
        )
    return matches[0]


def parse_tdr_enum(block: TdrBlock, *, enum_name: str, item_prefix: str) -> TdrEnum:
    """Recover one TDR macro group used as an enum, including exact values."""

    data = block.data
    try:
        encoded_name = enum_name.encode("ascii")
        encoded_prefix = item_prefix.encode("ascii")
    except UnicodeEncodeError as error:
        raise M3AnalysisError("enum name or item prefix is not ASCII", source=block.source, offset=0) from error
    if not encoded_name or b"\0" in encoded_name or b"\0" in encoded_prefix:
        raise M3AnalysisError("enum name or item prefix is empty or contains NUL", source=block.source, offset=0)

    macro_count = _read_u32(data, MACRO_COUNT_OFFSET, source=block.source)
    macro_table = _relative_offset(
        data,
        _read_u32(data, MACRO_TABLE_OFFSET, source=block.source),
        source=block.source,
        field_offset=MACRO_TABLE_OFFSET,
    )
    macro_table_length = macro_count * MACRO_DESCRIPTOR_SIZE
    if macro_count == 0 or macro_table + macro_table_length > len(data):
        raise M3AnalysisError("TDR macro table exceeds the block", source=block.source, offset=macro_table)

    group_count = _read_u32(data, MACRO_GROUP_COUNT_OFFSET, source=block.source)
    group_index = _relative_offset(
        data,
        _read_u32(data, MACRO_GROUP_INDEX_OFFSET, source=block.source),
        source=block.source,
        field_offset=MACRO_GROUP_INDEX_OFFSET,
    )
    group_index_length = group_count * MACRO_GROUP_INDEX_ITEM_SIZE
    if group_count == 0 or group_index + group_index_length > len(data):
        raise M3AnalysisError("TDR macro-group index exceeds the block", source=block.source, offset=group_index)

    matches: list[tuple[int, int]] = []
    for group_number in range(group_count):
        index_item = group_index + group_number * MACRO_GROUP_INDEX_ITEM_SIZE
        group_offset = _relative_offset(
            data,
            _read_u32(data, index_item, source=block.source),
            source=block.source,
            field_offset=index_item,
        )
        group_size = _read_u32(data, index_item + 4, source=block.source)
        if group_size < MACRO_GROUP_HEADER_SIZE or group_offset + group_size > len(data):
            raise M3AnalysisError("TDR macro-group descriptor exceeds the block", source=block.source, offset=index_item)
        group_name = _read_fixed_ascii(
            data,
            group_offset + MACRO_GROUP_NAME_OFFSET,
            MACRO_GROUP_NAME_LENGTH,
            source=block.source,
        )
        if group_name == enum_name:
            matches.append((group_offset, group_size))

    if len(matches) != 1:
        raise M3AnalysisError(
            f"expected one macro group for {enum_name!r}, found {len(matches)}",
            source=block.source,
            offset=group_index,
        )
    descriptor_offset, descriptor_size = matches[0]
    count = _read_u32(data, descriptor_offset + MACRO_GROUP_COUNT_FIELD_OFFSET, source=block.source)
    max_count = _read_u32(data, descriptor_offset + MACRO_GROUP_MAX_COUNT_OFFSET, source=block.source)
    lookup_relative = _read_u32(data, descriptor_offset + MACRO_GROUP_LOOKUP_INDEX_OFFSET, source=block.source)
    declaration_relative = _read_u32(
        data,
        descriptor_offset + MACRO_GROUP_DECLARATION_INDEX_OFFSET,
        source=block.source,
    )
    expected_declaration = MACRO_GROUP_HEADER_SIZE + count * 4
    expected_size = MACRO_GROUP_HEADER_SIZE + count * 8
    if (
        count == 0
        or count != max_count
        or lookup_relative != MACRO_GROUP_HEADER_SIZE
        or declaration_relative != expected_declaration
        or descriptor_size != expected_size
    ):
        raise M3AnalysisError("enum macro-index arrays are inconsistent", source=block.source, offset=descriptor_offset)

    lookup_offset = descriptor_offset + lookup_relative
    declaration_offset = descriptor_offset + declaration_relative
    lookup_indexes = struct.unpack_from(f"<{count}I", data, lookup_offset)
    declaration_indexes = struct.unpack_from(f"<{count}I", data, declaration_offset)
    if tuple(sorted(lookup_indexes)) != tuple(sorted(declaration_indexes)):
        raise M3AnalysisError("enum macro-index tables do not match", source=block.source, offset=lookup_offset)

    recovered_items: list[TdrEnumItem] = []
    for macro_index in declaration_indexes:
        if macro_index >= macro_count:
            raise M3AnalysisError(
                f"enum macro index {macro_index} exceeds macro count {macro_count}",
                source=block.source,
                offset=declaration_offset,
            )
        macro_offset = macro_table + macro_index * MACRO_DESCRIPTOR_SIZE
        name_relative = _read_u32(data, macro_offset + MACRO_NAME_OFFSET, source=block.source)
        name_offset = _relative_offset(
            data,
            name_relative,
            source=block.source,
            field_offset=macro_offset + MACRO_NAME_OFFSET,
        )
        symbolic_name = _read_cstring(data, name_offset, source=block.source, field="enum item name")
        description_relative = _read_u32(
            data,
            macro_offset + MACRO_DESCRIPTION_OFFSET,
            source=block.source,
        )
        description = ""
        if description_relative != NO_OFFSET:
            description = _read_cstring(
                data,
                _relative_offset(
                    data,
                    description_relative,
                    source=block.source,
                    field_offset=macro_offset + MACRO_DESCRIPTION_OFFSET,
                ),
                source=block.source,
                field="enum item description",
            )
        command_id = _read_u32(data, macro_offset + MACRO_VALUE_OFFSET, source=block.source)
        recovered_items.append(
            TdrEnumItem(
                symbolic_name=symbolic_name,
                command_id=command_id,
                macro_index=macro_index,
                description=description,
                direction=_direction(description),
                string_offset=name_offset,
            )
        )

    prefix_matches = sum(
        item.symbolic_name.encode("gbk").startswith(encoded_prefix)
        for item in recovered_items
    )
    if prefix_matches == 0:
        raise M3AnalysisError(
            f"enum declares {count} values but found 0 item names for prefix {item_prefix!r}",
            source=block.source,
            offset=descriptor_offset,
        )
    items = tuple(recovered_items)
    return TdrEnum(
        metalib_name=block.metalib_name,
        enum_name=enum_name,
        descriptor_offset=descriptor_offset,
        items=items,
        values_by_id=tuple(sorted(item.command_id for item in items)),
    )


def _find_meta_descriptor(
    block: TdrBlock,
    *,
    type_name: str,
    expected_kind: int,
    kind_name: str,
) -> tuple[int, int, int, str]:
    data = block.data
    try:
        encoded_name = type_name.encode("ascii")
    except UnicodeEncodeError as error:
        raise M3AnalysisError(f"{kind_name} name is not ASCII", source=block.source, offset=0) from error
    if not encoded_name or b"\0" in encoded_name:
        raise M3AnalysisError(f"{kind_name} name is empty or contains NUL", source=block.source, offset=0)

    meta_count = _read_u32(data, META_COUNT_OFFSET, source=block.source)
    index_relative = _read_u32(data, META_INDEX_OFFSET, source=block.source)
    index_offset = _relative_offset(
        data,
        index_relative,
        source=block.source,
        field_offset=META_INDEX_OFFSET,
    )
    index_length = meta_count * META_INDEX_ITEM_SIZE
    if meta_count == 0 or index_offset + index_length > len(data):
        raise M3AnalysisError("TDR meta index exceeds the block", source=block.source, offset=index_offset)

    matches: list[tuple[int, int]] = []
    for meta_index in range(meta_count):
        item_offset = index_offset + meta_index * META_INDEX_ITEM_SIZE
        meta_relative = _read_u32(data, item_offset, source=block.source)
        meta_size = _read_u32(data, item_offset + 4, source=block.source)
        meta_offset = _relative_offset(
            data,
            meta_relative,
            source=block.source,
            field_offset=item_offset,
        )
        if meta_size < META_HEADER_SIZE or meta_offset + meta_size > len(data):
            raise M3AnalysisError("TDR meta descriptor exceeds the block", source=block.source, offset=item_offset)
        name_relative = _read_u32(data, meta_offset + META_NAME_OFFSET, source=block.source)
        name_offset = _relative_offset(
            data,
            name_relative,
            source=block.source,
            field_offset=meta_offset + META_NAME_OFFSET,
        )
        name = _read_cstring(data, name_offset, source=block.source, field="meta name")
        if name.encode("gbk") == encoded_name:
            matches.append((meta_offset, meta_size))

    if len(matches) != 1:
        raise M3AnalysisError(
            f"expected one {kind_name} named {type_name!r}, found {len(matches)}",
            source=block.source,
            offset=index_offset,
        )
    meta_offset, meta_size = matches[0]
    meta_kind = _read_u32(data, meta_offset + META_KIND_OFFSET, source=block.source)
    if meta_kind != expected_kind:
        raise M3AnalysisError(f"{type_name!r} is not a {kind_name}", source=block.source, offset=meta_offset)
    self_relative = _read_u32(data, meta_offset + META_SELF_OFFSET, source=block.source)
    if METALIB_DATA_BASE + self_relative != meta_offset:
        raise M3AnalysisError(f"TDR {kind_name} self offset is inconsistent", source=block.source, offset=meta_offset)

    field_count = _read_u32(data, meta_offset + META_FIELD_COUNT_OFFSET, source=block.source)
    expected_size = META_HEADER_SIZE + field_count * FIELD_DESCRIPTOR_SIZE
    if expected_size != meta_size:
        raise M3AnalysisError(
            f"TDR {kind_name} descriptor size 0x{meta_size:X} does not match {field_count} fields",
            source=block.source,
            offset=meta_offset,
        )
    description_relative = _read_u32(data, meta_offset + META_DESCRIPTION_OFFSET, source=block.source)
    description = ""
    if description_relative != NO_OFFSET:
        description = _read_cstring(
            data,
            _relative_offset(
                data,
                description_relative,
                source=block.source,
                field_offset=meta_offset + META_DESCRIPTION_OFFSET,
            ),
            source=block.source,
            field="meta description",
        )
    return meta_offset, meta_size, field_count, description


def _entry_type_name(block: TdrBlock, field_offset: int) -> str:
    data = block.data
    custom_type_relative = _read_u32(data, field_offset + FIELD_CUSTOM_TYPE_OFFSET, source=block.source)
    if custom_type_relative != NO_OFFSET:
        custom_meta_offset = _relative_offset(
            data,
            custom_type_relative,
            source=block.source,
            field_offset=field_offset + FIELD_CUSTOM_TYPE_OFFSET,
        )
        custom_name_relative = _read_u32(data, custom_meta_offset + META_NAME_OFFSET, source=block.source)
        return _read_cstring(
            data,
            _relative_offset(
                data,
                custom_name_relative,
                source=block.source,
                field_offset=custom_meta_offset + META_NAME_OFFSET,
            ),
            source=block.source,
            field="entry custom type name",
        )
    primitive_type = _read_u32(data, field_offset + FIELD_PRIMITIVE_TYPE_OFFSET, source=block.source)
    if primitive_type >= len(TDR_PRIMITIVE_TYPES):
        raise M3AnalysisError(
            f"unsupported TDR primitive type index {primitive_type}",
            source=block.source,
            offset=field_offset + FIELD_PRIMITIVE_TYPE_OFFSET,
        )
    return TDR_PRIMITIVE_TYPES[primitive_type]


def _selector_macro(block: TdrBlock, macro_index: int, selector_id: int) -> str:
    data = block.data
    macro_count = _read_u32(data, MACRO_COUNT_OFFSET, source=block.source)
    if macro_index >= macro_count:
        raise M3AnalysisError(
            f"selector macro index {macro_index} exceeds macro count {macro_count}",
            source=block.source,
            offset=MACRO_COUNT_OFFSET,
        )
    macro_table = _relative_offset(
        data,
        _read_u32(data, MACRO_TABLE_OFFSET, source=block.source),
        source=block.source,
        field_offset=MACRO_TABLE_OFFSET,
    )
    macro_offset = macro_table + macro_index * MACRO_DESCRIPTOR_SIZE
    if macro_offset + MACRO_DESCRIPTOR_SIZE > len(data):
        raise M3AnalysisError("selector macro exceeds the block", source=block.source, offset=macro_offset)
    name_offset = _relative_offset(
        data,
        _read_u32(data, macro_offset + MACRO_NAME_OFFSET, source=block.source),
        source=block.source,
        field_offset=macro_offset + MACRO_NAME_OFFSET,
    )
    macro_name = _read_cstring(data, name_offset, source=block.source, field="selector macro name")
    macro_value = _read_u32(data, macro_offset + MACRO_VALUE_OFFSET, source=block.source)
    if macro_value != selector_id:
        raise M3AnalysisError(
            f"selector {selector_id} does not match macro {macro_name}={macro_value}",
            source=block.source,
            offset=macro_offset + MACRO_VALUE_OFFSET,
        )
    return macro_name


def _resolve_reference_path(
    block: TdrBlock,
    *,
    meta_offset: int,
    current_field_index: int,
    target_offset: int,
) -> str:
    data = block.data

    def resolve(current_meta: int, relative_target: int, ignored_index: int | None, visited: frozenset[int]) -> list[str] | None:
        if current_meta in visited:
            raise M3AnalysisError("cyclic TDR reference traversal", source=block.source, offset=current_meta)
        field_count = _read_u32(data, current_meta + META_FIELD_COUNT_OFFSET, source=block.source)
        for field_index in range(field_count):
            field_offset = current_meta + META_HEADER_SIZE + field_index * FIELD_DESCRIPTOR_SIZE
            memory_offset = _read_u32(data, field_offset + FIELD_MEMORY_OFFSET, source=block.source)
            storage_size = _read_u32(data, field_offset + FIELD_STORAGE_SIZE_OFFSET, source=block.source)
            field_end = memory_offset + storage_size
            if field_end < memory_offset:
                raise M3AnalysisError("TDR field storage range overflows", source=block.source, offset=field_offset)
            if storage_size == 0 or relative_target < memory_offset or relative_target >= field_end:
                continue

            name_relative = _read_u32(data, field_offset + FIELD_NAME_OFFSET, source=block.source)
            field_name = _read_cstring(
                data,
                _relative_offset(
                    data,
                    name_relative,
                    source=block.source,
                    field_offset=field_offset + FIELD_NAME_OFFSET,
                ),
                source=block.source,
                field="referenced entry name",
            )
            path = [] if field_index == ignored_index else [field_name]
            nested_target = relative_target - memory_offset
            if nested_target == 0:
                return path

            custom_type_relative = _read_u32(data, field_offset + FIELD_CUSTOM_TYPE_OFFSET, source=block.source)
            flags = _read_u32(data, field_offset + FIELD_FLAGS_OFFSET, source=block.source)
            if custom_type_relative != NO_OFFSET and flags & 0x06 == 0:
                custom_meta_offset = _relative_offset(
                    data,
                    custom_type_relative,
                    source=block.source,
                    field_offset=field_offset + FIELD_CUSTOM_TYPE_OFFSET,
                )
                nested_path = resolve(
                    custom_meta_offset,
                    nested_target,
                    None,
                    visited | {current_meta},
                )
                if nested_path is not None:
                    return path + nested_path
            return path
        return None

    resolved = resolve(meta_offset, target_offset, current_field_index, frozenset())
    if not resolved:
        raise M3AnalysisError(
            f"cannot resolve TDR reference offset {target_offset}",
            source=block.source,
            offset=meta_offset,
        )
    return ".".join(resolved)


def _parse_meta_fields(
    block: TdrBlock,
    *,
    meta_offset: int,
    field_count: int,
    include_selectors: bool,
) -> tuple[TdrField, ...]:
    data = block.data

    fields: list[TdrField] = []
    for field_index in range(field_count):
        field_offset = meta_offset + META_HEADER_SIZE + field_index * FIELD_DESCRIPTOR_SIZE
        name_relative = _read_u32(data, field_offset + FIELD_NAME_OFFSET, source=block.source)
        field_name = _read_cstring(
            data,
            _relative_offset(
                data,
                name_relative,
                source=block.source,
                field_offset=field_offset + FIELD_NAME_OFFSET,
            ),
            source=block.source,
            field="entry name",
        )
        field_description_relative = _read_u32(
            data,
            field_offset + FIELD_DESCRIPTION_OFFSET,
            source=block.source,
        )
        field_description = ""
        if field_description_relative != NO_OFFSET:
            field_description = _read_cstring(
                data,
                _relative_offset(
                    data,
                    field_description_relative,
                    source=block.source,
                    field_offset=field_offset + FIELD_DESCRIPTION_OFFSET,
                ),
                source=block.source,
                field="entry description",
            )
        selector_id: int | None = None
        selector_name = ""
        if include_selectors:
            raw_selector = _read_u32(data, field_offset + FIELD_SELECTOR_OFFSET, source=block.source)
            selector_id = None if raw_selector == NO_OFFSET else raw_selector
            selector_macro_index = _read_u32(
                data,
                field_offset + FIELD_SELECTOR_MACRO_OFFSET,
                source=block.source,
            )
            if selector_macro_index != NO_OFFSET:
                if selector_id is None:
                    raise M3AnalysisError(
                        "union entry has a selector macro but no selector value",
                        source=block.source,
                        offset=field_offset + FIELD_SELECTOR_OFFSET,
                    )
                selector_name = _selector_macro(block, selector_macro_index, selector_id)
        raw_refer = _read_u32(data, field_offset + FIELD_REFER_OFFSET, source=block.source)
        raw_select = _read_u32(data, field_offset + FIELD_SELECT_OFFSET, source=block.source)
        refer_offset = None if raw_refer == NO_OFFSET else raw_refer
        select_offset = None if raw_select == NO_OFFSET else raw_select
        fields.append(
            TdrField(
                name=field_name,
                type_name=_entry_type_name(block, field_offset),
                memory_offset=_read_u32(data, field_offset + FIELD_MEMORY_OFFSET, source=block.source),
                description=field_description,
                descriptor_offset=field_offset,
                count=_read_i32(data, field_offset + FIELD_COUNT_OFFSET, source=block.source),
                declared_size=_read_u32(data, field_offset + FIELD_DECLARED_SIZE_OFFSET, source=block.source),
                storage_size=_read_u32(data, field_offset + FIELD_STORAGE_SIZE_OFFSET, source=block.source),
                flags=_read_u32(data, field_offset + FIELD_FLAGS_OFFSET, source=block.source),
                length_prefix_size=_read_u32(
                    data,
                    field_offset + FIELD_LENGTH_PREFIX_SIZE_OFFSET,
                    source=block.source,
                ),
                selector_id=selector_id,
                selector_name=selector_name,
                refer_offset=refer_offset,
                refer_path="" if refer_offset is None else _resolve_reference_path(
                    block,
                    meta_offset=meta_offset,
                    current_field_index=field_index,
                    target_offset=refer_offset,
                ),
                select_offset=select_offset,
                select_path="" if select_offset is None else _resolve_reference_path(
                    block,
                    meta_offset=meta_offset,
                    current_field_index=field_index,
                    target_offset=select_offset,
                ),
            )
        )
    return tuple(fields)


def parse_tdr_struct(block: TdrBlock, *, struct_name: str) -> TdrStruct:
    """Recover one struct and its declaration-order fields without executing TDR code."""

    meta_offset, meta_size, field_count, description = _find_meta_descriptor(
        block,
        type_name=struct_name,
        expected_kind=META_KIND_STRUCT,
        kind_name="struct",
    )

    return TdrStruct(
        metalib_name=block.metalib_name,
        name=struct_name,
        description=description,
        descriptor_offset=meta_offset,
        descriptor_size=meta_size,
        fields=_parse_meta_fields(
            block,
            meta_offset=meta_offset,
            field_count=field_count,
            include_selectors=False,
        ),
    )


def parse_tdr_union(block: TdrBlock, *, union_name: str) -> TdrUnion:
    """Recover one union and its selector-to-entry mapping without executing TDR code."""

    meta_offset, meta_size, field_count, description = _find_meta_descriptor(
        block,
        type_name=union_name,
        expected_kind=META_KIND_UNION,
        kind_name="union",
    )
    return TdrUnion(
        metalib_name=block.metalib_name,
        name=union_name,
        description=description,
        descriptor_offset=meta_offset,
        descriptor_size=meta_size,
        fields=_parse_meta_fields(
            block,
            meta_offset=meta_offset,
            field_count=field_count,
            include_selectors=True,
        ),
    )
