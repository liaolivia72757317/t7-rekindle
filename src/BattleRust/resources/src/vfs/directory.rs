use super::{CHUNK_SIZE, Entry, ResourceError};
use crate::gbk;
use std::collections::BTreeMap;
use std::io::{Read, Seek, SeekFrom};

const DATA_START: u64 = 0x800;
const MAX_TAIL_BYTES: u64 = 64 * 1024 * 1024;
const RECORD_SIZE: usize = 44;

pub(super) fn canonical_path(path: &str) -> Result<String, &'static str> {
    let value = path.replace('\\', "/").to_lowercase();
    let value = value.strip_prefix("../").unwrap_or(&value);
    if !value.starts_with("data/")
        || value.split('/').any(|part| matches!(part, "" | "." | ".."))
        || value
            .chars()
            .any(|c| c < ' ' || matches!(c, ':' | '\u{7f}'))
    {
        return Err("invalid resource path");
    }
    Ok(value.to_owned())
}

fn path_prefix(tail: &[u8], offset: usize) -> bool {
    tail.get(offset..offset + 8).is_some_and(|value| {
        value[0..2] == *b".."
            && matches!(value[2], b'/' | b'\\')
            && value[3..7].eq_ignore_ascii_case(b"data")
            && matches!(value[7], b'/' | b'\\')
    })
}

fn decode_record(tail: &[u8], offset: usize) -> [u32; 11] {
    let shifted: [u8; RECORD_SIZE] = std::array::from_fn(|i| tail[offset + i] ^ 0xcc);
    let bytes: [u8; RECORD_SIZE] = std::array::from_fn(|i| {
        (shifted[i] >> 1) | ((shifted[(i + RECORD_SIZE - 1) % RECORD_SIZE] & 1) << 7)
    });
    std::array::from_fn(|i| {
        u32::from_le_bytes(bytes[i * 4..i * 4 + 4].try_into().expect("record width"))
    })
}

fn parse(
    tail: &[u8],
    base: u64,
    run_start: usize,
    directory_start: usize,
) -> Result<BTreeMap<String, Entry>, String> {
    let invalid = |reason: &str| Err(reason.to_owned());
    if directory_start == tail.len() || !(tail.len() - directory_start).is_multiple_of(RECORD_SIZE)
    {
        return invalid("directory extent is not a positive multiple of 44");
    }
    let mut names_start = directory_start;
    for offset in (directory_start..tail.len()).step_by(RECORD_SIZE).rev() {
        let fields = decode_record(tail, offset);
        if fields[0] != 0 {
            let length = u64::from(fields[10]) + u64::from(fields[9]) + 1;
            names_start = directory_start
                .checked_sub(length as usize)
                .ok_or("name table bounds mismatch")?;
            break;
        }
    }
    if names_start < run_start
        || names_start >= directory_start
        || (names_start > run_start && tail[names_start - 1] != 0)
    {
        return invalid("name table bounds do not match directory paths");
    }
    let mut entries = BTreeMap::new();
    let (mut name_cursor, mut chunk_cursor, mut data_end) = (0usize, 0u64, DATA_START);
    for offset in (directory_start..tail.len()).step_by(RECORD_SIZE) {
        let fields = decode_record(tail, offset);
        let [
            flags,
            data_offset,
            stored_size,
            decoded_size,
            _,
            _,
            _,
            chunks,
            chunk_start,
            path_length,
            path_offset,
        ] = fields;
        let label = format!(
            "record={} offset={}",
            (offset - directory_start) / RECORD_SIZE,
            base + offset as u64
        );
        if !matches!(flags, 0 | 1 | 3) {
            return Err(format!("{label}: unsupported flags={flags}"));
        }
        if flags == 0 {
            continue;
        }
        let path_end = names_start + name_cursor + path_length as usize;
        if path_offset as usize != name_cursor
            || !(9..=1024).contains(&path_length)
            || path_end >= directory_start
            || tail[path_end] != 0
        {
            return Err(format!("{label}: path length/offset mismatch"));
        }
        let decoded = gbk::decode(&tail[names_start + name_cursor..path_end])
            .map_err(|error| format!("{label}: {error}"))?;
        let path = canonical_path(&decoded).map_err(|error| format!("{label}: {error}"))?;
        if entries.contains_key(&path) {
            return Err(format!("{label}: duplicate normalized path"));
        }
        let entry_end = u64::from(data_offset) + u64::from(stored_size);
        if u64::from(data_offset) < data_end
            || entry_end > (base + names_start as u64).min(u64::from(u32::MAX))
        {
            return Err(format!("{label}: overlapping or out-of-range data"));
        }
        if u64::from(chunk_start) != chunk_cursor {
            return Err(format!("{label}: chunk ordinal mismatch"));
        }
        if flags == 3 && u64::from(chunks) != u64::from(decoded_size).div_ceil(CHUNK_SIZE as u64) {
            return Err(format!("{label}: chunk count does not match decoded size"));
        }
        if flags == 1 && (stored_size != decoded_size || chunks != 0) {
            return Err(format!("{label}: raw size/chunk mismatch"));
        }
        entries.insert(
            path,
            Entry {
                flags,
                offset: data_offset,
                stored_size,
                decoded_size,
                chunk_count: chunks,
            },
        );
        name_cursor += path_length as usize + 1;
        chunk_cursor += u64::from(chunks);
        data_end = entry_end;
    }
    if entries.is_empty() || names_start + name_cursor != directory_start {
        return invalid("directory did not consume all names");
    }
    Ok(entries)
}

pub(super) fn read(
    source: &mut (impl Read + Seek),
    archive: &str,
) -> Result<BTreeMap<String, Entry>, ResourceError> {
    let invalid = |detail: &str| ResourceError::new("directory_invalid", archive, "", detail);
    let mut header = [0; 4];
    source
        .seek(SeekFrom::Start(0))
        .and_then(|_| source.read_exact(&mut header))
        .map_err(|error| invalid(&error.to_string()))?;
    if header != [0x90, 0x10, 0xba, 0x22] {
        return Err(invalid("unsupported archive header"));
    }
    let size = source
        .seek(SeekFrom::End(0))
        .map_err(|error| ResourceError::new("archive_read", archive, "", error))?;
    let base = DATA_START.max(size.saturating_sub(MAX_TAIL_BYTES));
    if size <= base {
        return Err(invalid("truncated archive"));
    }
    source
        .seek(SeekFrom::Start(base))
        .map_err(|error| ResourceError::new("archive_read", archive, "", error))?;
    let mut tail = vec![0; (size - base) as usize];
    source
        .read_exact(&mut tail)
        .map_err(|error| invalid(&error.to_string()))?;
    let (mut result, mut failure, mut longest_run) =
        (None, "no validated directory in bounded tail".to_owned(), 0);
    let mut position = 0;
    while position + 8 <= tail.len() {
        if !path_prefix(&tail, position) {
            position += 1;
            continue;
        }
        let start = position;
        let mut end = start;
        while path_prefix(&tail, end) {
            let stop = tail[end..tail.len().min(end + 1025)]
                .iter()
                .position(|&value| value == 0)
                .map(|offset| end + offset);
            let Some(stop) = stop else {
                break;
            };
            if tail[end..stop].iter().any(|&value| value < 32) {
                break;
            }
            end = stop + 1;
        }
        if end > start {
            match parse(&tail, base, start, end) {
                Ok(candidate) if result.is_none() => result = Some(candidate),
                Ok(_) => {
                    return Err(ResourceError::new(
                        "directory_ambiguous",
                        archive,
                        "",
                        "multiple validated directories",
                    ));
                }
                Err(detail) if end - start > longest_run => {
                    failure = detail;
                    longest_run = end - start;
                }
                Err(_) => {}
            }
        }
        position = end.max(start + 8);
    }
    result.ok_or_else(|| invalid(&failure))
}
