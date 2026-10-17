use crate::gbk;
use std::collections::BTreeMap;
use std::io;

pub(crate) fn invalid(detail: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, detail.into())
}

pub(crate) fn u16_at(row: &[u8], offset: usize) -> u16 {
    u16::from_le_bytes(
        row[offset..offset + 2]
            .try_into()
            .expect("validated table row width"),
    )
}

pub(crate) fn u32_at(row: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes(
        row[offset..offset + 4]
            .try_into()
            .expect("validated table row width"),
    )
}

pub(crate) fn rows(data: &[u8], width: usize) -> io::Result<std::slice::ChunksExact<'_, u8>> {
    if data.len() < 140 {
        return Err(invalid("truncated battle resource table"));
    }
    let count = u32_at(data, 12) as u64;
    if &data[..4] != b"MSES"
        || u32_at(data, 4) != 7
        || u32_at(data, 8) as usize != width
        || &data[64..68] != b"GBK\0"
        || u32_at(data, 132) != 140
        || data.len() as u64 != 140 + count * width as u64
    {
        return Err(invalid("unsupported battle resource table layout"));
    }
    Ok(data[140..].chunks_exact(width))
}

pub(crate) fn unique_rows(
    data: &[u8],
    width: usize,
    short_key: bool,
) -> io::Result<BTreeMap<u32, &[u8]>> {
    let mut result = BTreeMap::new();
    for row in rows(data, width)? {
        let key = if short_key {
            u32::from(u16_at(row, 0))
        } else {
            u32_at(row, 0)
        };
        if result.insert(key, row).is_some() {
            return Err(invalid(format!("duplicate resource key {key}")));
        }
    }
    Ok(result)
}

pub(crate) fn text(row: &[u8], offset: usize, size: usize) -> io::Result<String> {
    let raw = &row[offset..offset + size];
    let end = raw
        .iter()
        .position(|&value| value == 0)
        .ok_or_else(|| invalid("unterminated client resource string"))?;
    gbk::decode(&raw[..end])
}
