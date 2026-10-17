use std::io;
use windows_sys::Win32::Globalization::{WC_NO_BEST_FIT_CHARS, WideCharToMultiByte};

pub fn player_name(value: &str) -> io::Result<Vec<u8>> {
    let name = value.trim();
    if name.is_empty()
        || name
            .chars()
            .any(|c| c.is_control() || c == '€' || ('\u{e000}'..='\u{f8ff}').contains(&c))
        || name.len() > 124
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "invalid player name",
        ));
    }
    let wide: Vec<u16> = name.encode_utf16().collect();
    let mut bytes = [0; 128];
    let mut replaced = 0;
    // SAFETY: input/output lengths describe live buffers; the call retains no pointers.
    let count = unsafe {
        WideCharToMultiByte(
            936,
            WC_NO_BEST_FIT_CHARS,
            wide.as_ptr(),
            wide.len() as i32,
            bytes.as_mut_ptr(),
            bytes.len() as i32,
            std::ptr::null(),
            &mut replaced,
        )
    };
    if !(1..=31).contains(&count) || replaced != 0 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "player name must fit 31 GBK bytes",
        ));
    }
    Ok(bytes[..count as usize].to_vec())
}
