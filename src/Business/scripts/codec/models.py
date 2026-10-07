"""Immutable records emitted by M3 static analyzers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TdrBlock:
    source: str
    file_offset: int
    length: int
    metalib_name: str
    sha256: str
    data: bytes

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "source": self.source,
            "fileOffset": self.file_offset,
            "length": self.length,
            "metalibName": self.metalib_name,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class TdrEnumItem:
    symbolic_name: str
    command_id: int
    macro_index: int
    description: str
    direction: str
    string_offset: int

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "symbolicName": self.symbolic_name,
            "commandId": self.command_id,
            "commandIdHex": f"0x{self.command_id:08X}",
            "macroIndex": self.macro_index,
            "macroIndexHex": f"0x{self.macro_index:08X}",
            "description": self.description,
            "direction": self.direction,
            "stringOffset": self.string_offset,
        }


@dataclass(frozen=True, slots=True)
class TdrEnum:
    metalib_name: str
    enum_name: str
    descriptor_offset: int
    items: tuple[TdrEnumItem, ...]
    values_by_id: tuple[int, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "metalibName": self.metalib_name,
            "enumName": self.enum_name,
            "descriptorOffset": self.descriptor_offset,
            "items": [item.to_dict() for item in self.items],
            "valuesById": list(self.values_by_id),
        }


@dataclass(frozen=True, slots=True)
class TdrMacro:
    symbolic_name: str
    value: int
    macro_index: int
    description: str
    string_offset: int

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "symbolicName": self.symbolic_name,
            "value": self.value,
            "valueHex": f"0x{self.value:08X}",
            "macroIndex": self.macro_index,
            "macroIndexHex": f"0x{self.macro_index:08X}",
            "description": self.description,
            "stringOffset": self.string_offset,
        }


@dataclass(frozen=True, slots=True)
class TdrField:
    name: str
    type_name: str
    memory_offset: int
    description: str
    descriptor_offset: int
    count: int = 1
    declared_size: int = 0
    storage_size: int = 0
    flags: int = 0
    length_prefix_size: int = 0
    selector_id: int | None = None
    selector_name: str = ""
    refer_offset: int | None = None
    refer_path: str = ""
    select_offset: int | None = None
    select_path: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "name": self.name,
            "typeName": self.type_name,
            "memoryOffset": self.memory_offset,
            "description": self.description,
            "descriptorOffset": self.descriptor_offset,
            "count": self.count,
            "declaredSize": self.declared_size,
            "storageSize": self.storage_size,
            "flags": self.flags,
            "lengthPrefixSize": self.length_prefix_size,
            "selectorId": self.selector_id,
            "selectorName": self.selector_name,
            "referOffset": self.refer_offset,
            "referPath": self.refer_path,
            "selectOffset": self.select_offset,
            "selectPath": self.select_path,
        }


@dataclass(frozen=True, slots=True)
class TdrStruct:
    metalib_name: str
    name: str
    description: str
    descriptor_offset: int
    descriptor_size: int
    fields: tuple[TdrField, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "metalibName": self.metalib_name,
            "name": self.name,
            "description": self.description,
            "descriptorOffset": self.descriptor_offset,
            "descriptorSize": self.descriptor_size,
            "fields": [field.to_dict() for field in self.fields],
        }


@dataclass(frozen=True, slots=True)
class TdrUnion:
    metalib_name: str
    name: str
    description: str
    descriptor_offset: int
    descriptor_size: int
    fields: tuple[TdrField, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "metalibName": self.metalib_name,
            "name": self.name,
            "description": self.description,
            "descriptorOffset": self.descriptor_offset,
            "descriptorSize": self.descriptor_size,
            "fields": [field.to_dict() for field in self.fields],
        }
