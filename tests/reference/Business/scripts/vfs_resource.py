"""Read selected resources through the known VFS tail directory, without extraction."""
from collections import namedtuple
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
import re
import struct
import zlib

MAX_TAIL_BYTES = 64 * 1024 * 1024
MAX_ENTRY_BYTES = 16 * 1024 * 1024
DATA_START = 0x800
RECORD_SIZE = 44
CHUNK_SIZE = 16384
PATH_PREFIX = re.compile(rb'\.\.[/\\]data[/\\]', re.IGNORECASE)
XOR_TABLE = bytes(value ^ 0xCC for value in range(256))
Entry = namedtuple('Entry', 'flags offset storedSize decodedSize chunkCount')


class ResourceError(ValueError):
    def __init__(self, code, archive, entry, detail):
        self.code, self.archive, self.entry = code, str(archive), entry
        super().__init__(f'[T7R_RESOURCE:{code}] archive={str(archive)!r} entry={entry!r}: {detail}')


def canonicalPath(path):
    value = path.replace('\\', '/').lower()
    if value.startswith('../'):
        value = value[3:]
    parts = value.split('/')
    if (not value.startswith('data/') or any(part in ('', '.', '..') for part in parts)
            or any(ord(char) < 32 or char in ':\x7f' for char in value)):
        raise ValueError(f'invalid resource path: {path!r}')
    return value


def nameRuns(tail):
    position = 0
    while match := PATH_PREFIX.search(tail, position):
        start = end = match.start()
        while PATH_PREFIX.match(tail, end):
            stop = tail.find(b'\0', end, end + 1025)
            if stop < 0 or any(value < 32 for value in tail[end:stop]):
                break
            end = stop + 1
        if end > start:
            yield start, end
        position = max(end, start + 8)


def decodeRecord(tail, offset):
    shifted = tail[offset:offset + RECORD_SIZE].translate(XOR_TABLE)
    decoded = bytes((value >> 1) | ((shifted[i - 1] & 1) << 7) for i, value in enumerate(shifted))
    return struct.unpack('<11I', decoded)


def parseDirectory(tail, base, runStart, directoryStart):
    if directoryStart == len(tail) or (len(tail) - directoryStart) % RECORD_SIZE:
        raise ValueError('directory extent is not a positive multiple of 44')
    # A raw data entry can end with a path-like string. Derive the actual
    # table start from its final active record, rather than trusting that run.
    namesStart = directoryStart
    for offset in range(len(tail) - RECORD_SIZE, directoryStart - 1, -RECORD_SIZE):
        fields = decodeRecord(tail, offset)
        if fields[0]:
            namesStart = directoryStart - fields[10] - fields[9] - 1
            break
    if (not runStart <= namesStart < directoryStart
            or (namesStart > runStart and tail[namesStart - 1] != 0)):
        raise ValueError('name table bounds do not match directory paths')
    result, nameCursor, chunkCursor, dataEnd = {}, 0, 0, DATA_START
    for offset in range(directoryStart, len(tail), RECORD_SIZE):
        fields = decodeRecord(tail, offset)
        flags, dataOffset, storedSize, decodedSize = fields[:4]
        chunks, chunkStart, pathLength, pathOffset = fields[7:]
        label = f'record={(offset - directoryStart) // RECORD_SIZE} offset={base + offset}'
        if flags not in (0, 1, 3):
            raise ValueError(f'{label}: unsupported flags={flags}')
        if flags == 0:
            continue
        pathEnd = namesStart + nameCursor + pathLength
        if (pathOffset != nameCursor or not 8 < pathLength <= 1024 or pathEnd >= directoryStart
                or tail[pathEnd] != 0):
            raise ValueError(f'{label}: path length/offset mismatch')
        path = canonicalPath(tail[namesStart + nameCursor:pathEnd].decode('gbk'))
        label += f' entry={path!r}'
        if path in result:
            raise ValueError(f'{label}: duplicate normalized path')
        if (dataOffset < dataEnd or dataOffset + storedSize > min(base + namesStart, 0xFFFFFFFF)):
            raise ValueError(f'{label}: overlapping or out-of-range data offset={dataOffset} size={storedSize}')
        if chunkStart != chunkCursor:
            raise ValueError(f'{label}: chunk ordinal mismatch')
        if flags == 3 and chunks != (decodedSize + CHUNK_SIZE - 1) // CHUNK_SIZE:
            raise ValueError(f'{label}: chunk count does not match decoded size')
        if flags == 1 and (storedSize != decodedSize or chunks != 0):
            raise ValueError(f'{label}: raw size/chunk mismatch')
        result[path] = Entry(flags, dataOffset, storedSize, decodedSize, chunks)
        nameCursor += pathLength + 1
        chunkCursor += chunks
        dataEnd = dataOffset + storedSize
    if not result or namesStart + nameCursor != directoryStart:
        raise ValueError('directory did not consume all names')
    return MappingProxyType(result)


@lru_cache(maxsize=2)
def directoryIndex(archive):
    with archive.open('rb') as source:
        if source.read(4) != bytes.fromhex('9010ba22'):
            raise ResourceError('directory_invalid', archive.name, '', 'unsupported archive header')
        size = source.seek(0, 2)
        base = max(DATA_START, size - MAX_TAIL_BYTES)
        if size <= base:
            raise ResourceError('directory_invalid', archive.name, '', 'truncated archive')
        source.seek(base)
        tail = source.read(size - base)
        if len(tail) != size - base:
            raise ResourceError('directory_invalid', archive.name, '', 'truncated tail')
    result = None
    failure, longestRun = 'no validated directory in bounded tail', -1
    for start, end in nameRuns(tail):
        try:
            candidate = parseDirectory(tail, base, start, end)
        except ValueError as error:
            if end - start > longestRun:
                failure, longestRun = str(error), end - start
            continue
        if result is not None:
            raise ResourceError('directory_ambiguous', archive.name, '', 'multiple validated directories')
        result = candidate
    if result is None:
        raise ResourceError('directory_invalid', archive.name, '', failure)
    return result


def clearCache():
    directoryIndex.cache_clear()


def readCompressedEntry(source, offset, storedSize, decodedSize, chunkCount=None):
    if min(offset, storedSize, decodedSize) < 0 or max(storedSize, decodedSize) > MAX_ENTRY_BYTES:
        raise ValueError('entry bounds invalid')
    source.seek(offset)
    remaining = source.read(storedSize)
    if len(remaining) != storedSize:
        raise ValueError('archive is truncated')
    result, count = bytearray(), 0
    while remaining:
        decoder = zlib.decompressobj()
        try:
            block = decoder.decompress(remaining, min(CHUNK_SIZE, decodedSize - len(result)) + 1)
        except zlib.error as error:
            raise ValueError('invalid compressed stream') from error
        if not decoder.eof or decoder.unconsumed_tail or not 0 < len(block) <= CHUNK_SIZE:
            raise ValueError('compressed stream extent mismatch')
        if len(result) + len(block) > decodedSize:
            raise ValueError('decoded size exceeds entry')
        result.extend(block)
        count += 1
        remaining = decoder.unused_data
    if len(result) != decodedSize or (chunkCount is not None and count != chunkCount):
        raise ValueError('decoded size or chunk count mismatch')
    return bytes(result)


def readEntry(archivePath, resourcePath):
    archive = Path(archivePath)
    path = canonicalPath(resourcePath)
    try:
        entry = directoryIndex(archive).get(path)
        if entry is None:
            raise ResourceError('entry_missing', archive.name, path, 'target not found')
        if max(entry.storedSize, entry.decodedSize) > MAX_ENTRY_BYTES:
            raise ResourceError('entry_size', archive.name, path, f'entry={entry}; limit={MAX_ENTRY_BYTES}')
        with archive.open('rb') as source:
            try:
                if entry.flags == 3:
                    return readCompressedEntry(source, entry.offset, entry.storedSize, entry.decodedSize, entry.chunkCount)
                source.seek(entry.offset)
                data = source.read(entry.storedSize)
                if len(data) != entry.decodedSize:
                    raise ValueError('truncated raw entry')
                return data
            except ValueError as error:
                raise ResourceError('entry_decode', archive.name, path, f'entry={entry}; {error}') from error
    except OSError as error:
        raise ResourceError('archive_read', archive.name, path, str(error)) from error


def readResource(clientDirectory, archiveName, resourcePath, parser):
    directory = Path(clientDirectory)
    if not directory.is_absolute():
        raise ResourceError('archive_read', archiveName, resourcePath, 'selected absolute Bin directory required')
    data = readEntry(directory.parent / 'vfs' / archiveName, resourcePath)
    try:
        return parser(data)
    except Exception as error:
        raise ResourceError('content_mismatch', archiveName, resourcePath, str(error)) from error
