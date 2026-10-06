"""Bounded extraction of modules, x86 thread contexts, and stack address candidates."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .errors import M3AnalysisError

MINIDUMP_SIGNATURE = b"MDMP"
THREAD_LIST_STREAM = 3
MODULE_LIST_STREAM = 4
MEMORY_LIST_STREAM = 5
EXCEPTION_STREAM = 6
MEMORY64_LIST_STREAM = 9
MAX_STREAMS = 4096
MAX_MODULES = 4096
MAX_THREADS = 4096
MODULE_RECORD_SIZE = 108
THREAD_RECORD_SIZE = 48
X86_CONTEXT_MINIMUM_SIZE = 200
MAX_STACK_CANDIDATES = 512
EXCEPTION_STREAM_SIZE = 168
MAX_EXCEPTION_PARAMETERS = 15
MAX_MEMORY_RANGES = 65536


@dataclass(frozen=True)
class StreamLocation:
    size: int
    rva: int


class Reader:
    def __init__(self, data: bytes, *, source: str) -> None:
        self.data = data
        self.source = source

    def require(self, offset: int, size: int, description: str) -> None:
        if offset < 0 or size < 0 or offset > len(self.data) or size > len(self.data) - offset:
            raise M3AnalysisError(
                f"truncated {description}: need {size} bytes, input size is {len(self.data)}",
                source=self.source,
                offset=max(offset, 0),
            )

    def u32(self, offset: int, description: str) -> int:
        self.require(offset, 4, description)
        return struct.unpack_from("<I", self.data, offset)[0]

    def u64(self, offset: int, description: str) -> int:
        self.require(offset, 8, description)
        return struct.unpack_from("<Q", self.data, offset)[0]

    def utf16_string(self, rva: int) -> str:
        byte_length = self.u32(rva, "MINIDUMP_STRING length")
        self.require(rva + 4, byte_length, "MINIDUMP_STRING data")
        if byte_length % 2 != 0:
            raise M3AnalysisError(
                "MINIDUMP_STRING has an odd UTF-16 byte length",
                source=self.source,
                offset=rva,
            )
        try:
            return self.data[rva + 4 : rva + 4 + byte_length].decode("utf-16-le")
        except UnicodeDecodeError as error:
            raise M3AnalysisError(
                f"invalid MINIDUMP_STRING UTF-16: {error.reason}",
                source=self.source,
                offset=rva + 4 + error.start,
            ) from error


def _stream_locations(reader: Reader) -> tuple[int, int, dict[int, StreamLocation]]:
    reader.require(0, 32, "MINIDUMP_HEADER")
    if reader.data[:4] != MINIDUMP_SIGNATURE:
        raise M3AnalysisError("invalid minidump signature", source=reader.source, offset=0)

    version = reader.u32(4, "minidump version")
    stream_count = reader.u32(8, "stream count")
    directory_rva = reader.u32(12, "stream directory RVA")
    flags = reader.u64(24, "minidump flags")
    if stream_count > MAX_STREAMS:
        raise M3AnalysisError(
            f"stream count {stream_count} exceeds limit {MAX_STREAMS}",
            source=reader.source,
            offset=8,
        )
    reader.require(directory_rva, stream_count * 12, "stream directory")

    locations: dict[int, StreamLocation] = {}
    for index in range(stream_count):
        offset = directory_rva + index * 12
        stream_type = reader.u32(offset, "stream type")
        size = reader.u32(offset + 4, "stream size")
        rva = reader.u32(offset + 8, "stream RVA")
        if stream_type == 0:
            continue
        reader.require(rva, size, f"stream type {stream_type}")
        if stream_type in locations:
            raise M3AnalysisError(
                f"duplicate stream type {stream_type}",
                source=reader.source,
                offset=offset,
            )
        locations[stream_type] = StreamLocation(size=size, rva=rva)
    return version, flags, locations


def _parse_modules(reader: Reader, location: StreamLocation) -> list[dict[str, object]]:
    reader.require(location.rva, 4, "module list count")
    count = reader.u32(location.rva, "module count")
    if count > MAX_MODULES:
        raise M3AnalysisError(
            f"module count {count} exceeds limit {MAX_MODULES}",
            source=reader.source,
            offset=location.rva,
        )
    records_size = count * MODULE_RECORD_SIZE
    if 4 + records_size > location.size:
        raise M3AnalysisError(
            "module list stream is smaller than its declared record count",
            source=reader.source,
            offset=location.rva,
        )
    reader.require(location.rva + 4, records_size, "module records")

    modules: list[dict[str, object]] = []
    for index in range(count):
        offset = location.rva + 4 + index * MODULE_RECORD_SIZE
        base = reader.u64(offset, "module base")
        size = reader.u32(offset + 8, "module image size")
        name_rva = reader.u32(offset + 20, "module name RVA")
        modules.append(
            {
                "index": index,
                "name": reader.utf16_string(name_rva),
                "base": f"0x{base:08X}",
                "size": size,
                "baseValue": base,
            }
        )
    return modules


def _find_module(address: int, modules: list[dict[str, object]]) -> tuple[dict[str, object], int] | None:
    for module in modules:
        base = int(module["baseValue"])
        size = int(module["size"])
        if base <= address < base + size:
            return module, address - base
    return None


def _address_value(address: int, modules: list[dict[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {"address": f"0x{address:08X}"}
    match = _find_module(address, modules)
    if match is not None:
        module, rva = match
        value.update({"module": module["name"], "moduleRva": f"0x{rva:08X}"})
    return value


def _parse_x86_context(
    reader: Reader,
    *,
    context_rva: int,
    context_size: int,
    modules: list[dict[str, object]],
    description: str,
) -> dict[str, object]:
    reader.require(context_rva, context_size, description)
    if context_size < X86_CONTEXT_MINIMUM_SIZE:
        raise M3AnalysisError(
            f"{description} is {context_size} bytes; x86 context needs at least {X86_CONTEXT_MINIMUM_SIZE}",
            source=reader.source,
            offset=context_rva,
        )
    edi = reader.u32(context_rva + 156, f"{description} x86 EDI")
    esi = reader.u32(context_rva + 160, f"{description} x86 ESI")
    ebx = reader.u32(context_rva + 164, f"{description} x86 EBX")
    edx = reader.u32(context_rva + 168, f"{description} x86 EDX")
    ecx = reader.u32(context_rva + 172, f"{description} x86 ECX")
    eax = reader.u32(context_rva + 176, f"{description} x86 EAX")
    ebp = reader.u32(context_rva + 180, f"{description} x86 EBP")
    eip = reader.u32(context_rva + 184, f"{description} x86 EIP")
    esp = reader.u32(context_rva + 196, f"{description} x86 ESP")
    return {
        "eip": _address_value(eip, modules),
        "esp": f"0x{esp:08X}",
        "ebp": f"0x{ebp:08X}",
        "eax": f"0x{eax:08X}",
        "ecx": f"0x{ecx:08X}",
        "edx": f"0x{edx:08X}",
        "ebx": f"0x{ebx:08X}",
        "esi": f"0x{esi:08X}",
        "edi": f"0x{edi:08X}",
    }


def _parse_exception(
    reader: Reader,
    location: StreamLocation,
    modules: list[dict[str, object]],
    memory_ranges: list[dict[str, int]],
) -> dict[str, object]:
    if location.size < EXCEPTION_STREAM_SIZE:
        raise M3AnalysisError(
            f"exception stream is {location.size} bytes; expected at least {EXCEPTION_STREAM_SIZE}",
            source=reader.source,
            offset=location.rva,
        )
    offset = location.rva
    thread_id = reader.u32(offset, "exception thread id")
    exception_code = reader.u32(offset + 8, "exception code")
    exception_flags = reader.u32(offset + 12, "exception flags")
    exception_record = reader.u64(offset + 16, "nested exception record")
    exception_address = reader.u64(offset + 24, "exception address")
    parameter_count = reader.u32(offset + 32, "exception parameter count")
    if parameter_count > MAX_EXCEPTION_PARAMETERS:
        raise M3AnalysisError(
            f"exception parameter count {parameter_count} exceeds limit {MAX_EXCEPTION_PARAMETERS}",
            source=reader.source,
            offset=offset + 32,
        )
    parameters = [
        f"0x{reader.u64(offset + 40 + index * 8, 'exception parameter'):016X}"
        for index in range(parameter_count)
    ]
    context_size = reader.u32(offset + 160, "exception context size")
    context_rva = reader.u32(offset + 164, "exception context RVA")
    context = _parse_x86_context(
        reader,
        context_rva=context_rva,
        context_size=context_size,
        modules=modules,
        description="exception context",
    )
    value: dict[str, object] = {
        "threadId": thread_id,
        "exceptionCode": f"0x{exception_code:08X}",
        "exceptionFlags": f"0x{exception_flags:08X}",
        "exceptionRecord": f"0x{exception_record:016X}",
        "exceptionAddress": _address_value(exception_address, modules),
        "parameters": parameters,
        "context": context,
    }
    code_window = _read_memory_window(
        reader,
        address=exception_address,
        memory_ranges=memory_ranges,
        bytes_before=16,
        maximum_size=64,
    )
    if code_window is not None:
        value["codeWindow"] = code_window
    return value


def _parse_memory_ranges(
    reader: Reader,
    location: StreamLocation,
) -> list[dict[str, int]]:
    reader.require(location.rva, 4, "memory list count")
    count = reader.u32(location.rva, "memory range count")
    if count > MAX_MEMORY_RANGES:
        raise M3AnalysisError(
            f"memory range count {count} exceeds limit {MAX_MEMORY_RANGES}",
            source=reader.source,
            offset=location.rva,
        )
    descriptors_size = count * 16
    if 4 + descriptors_size > location.size:
        raise M3AnalysisError(
            "memory list stream is smaller than its declared range count",
            source=reader.source,
            offset=location.rva,
        )
    reader.require(location.rva + 4, descriptors_size, "memory range descriptors")

    ranges: list[dict[str, int]] = []
    for index in range(count):
        offset = location.rva + 4 + index * 16
        start = reader.u64(offset, "memory range start")
        size = reader.u32(offset + 8, "memory range size")
        content_rva = reader.u32(offset + 12, "memory range content RVA")
        reader.require(content_rva, size, "memory range content")
        ranges.append({"start": start, "size": size, "rva": content_rva})
    return ranges


def _parse_memory64_ranges(
    reader: Reader,
    location: StreamLocation,
) -> list[dict[str, int]]:
    if location.size < 16:
        raise M3AnalysisError(
            "memory64 list stream is smaller than its header",
            source=reader.source,
            offset=location.rva,
        )
    count = reader.u64(location.rva, "memory64 range count")
    if count > MAX_MEMORY_RANGES:
        raise M3AnalysisError(
            f"memory64 range count {count} exceeds limit {MAX_MEMORY_RANGES}",
            source=reader.source,
            offset=location.rva,
        )
    base_rva = reader.u64(location.rva + 8, "memory64 content RVA")
    descriptors_size = count * 16
    if 16 + descriptors_size > location.size:
        raise M3AnalysisError(
            "memory64 list stream is smaller than its declared range count",
            source=reader.source,
            offset=location.rva,
        )
    reader.require(location.rva + 16, descriptors_size, "memory64 range descriptors")

    ranges: list[dict[str, int]] = []
    content_rva = base_rva
    for index in range(count):
        offset = location.rva + 16 + index * 16
        start = reader.u64(offset, "memory64 range start")
        size = reader.u64(offset + 8, "memory64 range size")
        reader.require(content_rva, size, "memory64 range content")
        ranges.append({"start": start, "size": size, "rva": content_rva})
        content_rva += size
    return ranges


def _read_memory_window(
    reader: Reader,
    *,
    address: int,
    memory_ranges: list[dict[str, int]],
    bytes_before: int,
    maximum_size: int,
) -> dict[str, object] | None:
    for memory_range in memory_ranges:
        start = memory_range["start"]
        size = memory_range["size"]
        if not start <= address < start + size:
            continue
        window_start = max(start, address - bytes_before)
        window_size = min(maximum_size, start + size - window_start)
        content_offset = memory_range["rva"] + window_start - start
        reader.require(content_offset, window_size, "exception code window")
        content = reader.data[content_offset : content_offset + window_size]
        return {
            "startAddress": f"0x{window_start:08X}",
            "exceptionOffset": address - window_start,
            "bytesHex": " ".join(f"{byte:02X}" for byte in content),
        }
    return None


def _parse_threads(
    reader: Reader,
    location: StreamLocation,
    modules: list[dict[str, object]],
) -> list[dict[str, object]]:
    reader.require(location.rva, 4, "thread list count")
    count = reader.u32(location.rva, "thread count")
    if count > MAX_THREADS:
        raise M3AnalysisError(
            f"thread count {count} exceeds limit {MAX_THREADS}",
            source=reader.source,
            offset=location.rva,
        )
    records_size = count * THREAD_RECORD_SIZE
    if 4 + records_size > location.size:
        raise M3AnalysisError(
            "thread list stream is smaller than its declared record count",
            source=reader.source,
            offset=location.rva,
        )
    reader.require(location.rva + 4, records_size, "thread records")

    threads: list[dict[str, object]] = []
    for index in range(count):
        offset = location.rva + 4 + index * THREAD_RECORD_SIZE
        thread_id = reader.u32(offset, "thread id")
        stack_start = reader.u64(offset + 24, "thread stack start")
        stack_size = reader.u32(offset + 32, "thread stack size")
        stack_rva = reader.u32(offset + 36, "thread stack RVA")
        context_size = reader.u32(offset + 40, "thread context size")
        context_rva = reader.u32(offset + 44, "thread context RVA")
        reader.require(stack_rva, stack_size, "thread stack data")
        context = _parse_x86_context(
            reader,
            context_rva=context_rva,
            context_size=context_size,
            modules=modules,
            description="thread context",
        )
        esp = int(str(context["esp"]), 16)
        scan_offset = max(0, esp - stack_start) if esp >= stack_start else 0
        scan_offset += (-scan_offset) % 4
        candidates: list[dict[str, object]] = []
        for stack_offset in range(scan_offset, max(scan_offset, stack_size - 3), 4):
            address = reader.u32(stack_rva + stack_offset, "thread stack word")
            match = _find_module(address, modules)
            if match is None:
                continue
            module, module_rva = match
            candidates.append(
                {
                    "stackAddress": f"0x{stack_start + stack_offset:08X}",
                    "address": f"0x{address:08X}",
                    "module": module["name"],
                    "moduleRva": f"0x{module_rva:08X}",
                }
            )
            if len(candidates) == MAX_STACK_CANDIDATES:
                break

        threads.append(
            {
                "index": index,
                "threadId": thread_id,
                **context,
                "stackStart": f"0x{stack_start:08X}",
                "stackSize": stack_size,
                "stackModuleCandidates": candidates,
            }
        )
    return threads


def inspect_minidump(data: bytes, *, source: str) -> dict[str, object]:
    """Return deterministic metadata without treating stack candidates as proven frames."""

    reader = Reader(data, source=source)
    version, flags, locations = _stream_locations(reader)
    if MODULE_LIST_STREAM not in locations:
        raise M3AnalysisError("module list stream is missing", source=source, offset=0)
    if THREAD_LIST_STREAM not in locations:
        raise M3AnalysisError("thread list stream is missing", source=source, offset=0)

    modules = _parse_modules(reader, locations[MODULE_LIST_STREAM])
    threads = _parse_threads(reader, locations[THREAD_LIST_STREAM], modules)
    if MEMORY_LIST_STREAM in locations and MEMORY64_LIST_STREAM in locations:
        raise M3AnalysisError(
            "both memory list and memory64 list streams are present",
            source=source,
            offset=0,
        )
    if MEMORY_LIST_STREAM in locations:
        memory_ranges = _parse_memory_ranges(reader, locations[MEMORY_LIST_STREAM])
    elif MEMORY64_LIST_STREAM in locations:
        memory_ranges = _parse_memory64_ranges(reader, locations[MEMORY64_LIST_STREAM])
    else:
        memory_ranges = []
    exception = (
        _parse_exception(reader, locations[EXCEPTION_STREAM], modules, memory_ranges)
        if EXCEPTION_STREAM in locations
        else None
    )
    for module in modules:
        module.pop("baseValue")
    return {
        "format": "Windows minidump",
        "version": f"0x{version:08X}",
        "flags": f"0x{flags:016X}",
        "modules": modules,
        "threads": threads,
        "exception": exception,
        "memoryRanges": [
            {
                "start": f"0x{memory_range['start']:08X}",
                "size": memory_range["size"],
                "contentRva": f"0x{memory_range['rva']:08X}",
            }
            for memory_range in memory_ranges
        ],
    }
