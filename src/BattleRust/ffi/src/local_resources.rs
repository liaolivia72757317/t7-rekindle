use crate::gateway::{self, INTERNAL_ERROR, INVALID_ARGUMENT, OK};
use serde::Deserialize;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::path::PathBuf;
use t7r_battle_resources::cache::{self, CacheStatus};

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct LocalResources {
    client_directory: PathBuf,
    cache_directory: PathBuf,
    rules_version: u32,
    resource_version: u32,
    client_version: String,
}

const MAX_REQUEST: usize = 128 * 1024;
const ERROR_CAPACITY: usize = 4096;

/// # Safety
/// The request is readable for `length` bytes. All output pointers are valid,
/// aligned, non-overlapping and writable; the error buffer has `capacity` bytes.
/// Error capacity must be at least 4096. No borrowed pointer survives the call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn t7r_gateway_create_local(
    request: *const u8,
    length: u32,
    handle: *mut u64,
    cache_hit: *mut u32,
    error: *mut u8,
    capacity: u32,
    error_length: *mut u32,
) -> i32 {
    if handle.is_null() || cache_hit.is_null() || error_length.is_null() {
        return INVALID_ARGUMENT;
    }
    // SAFETY: the caller provides writable outputs as documented above.
    unsafe {
        *handle = 0;
        *cache_hit = 0;
        *error_length = 0;
    }
    if request.is_null()
        || length == 0
        || length as usize > MAX_REQUEST
        || error.is_null()
        || (capacity as usize) < ERROR_CAPACITY
    {
        return INVALID_ARGUMENT;
    }
    let result = catch_unwind(AssertUnwindSafe(|| {
        // SAFETY: the request is borrowed only during this call.
        let bytes = unsafe { std::slice::from_raw_parts(request, length as usize) };
        let config: LocalResources =
            serde_json::from_slice(bytes).map_err(|value| (INVALID_ARGUMENT, value.to_string()))?;
        let resources = cache::load_or_build(
            &config.client_directory,
            &config.cache_directory,
            config.rules_version,
            config.resource_version,
            &config.client_version,
        )
        .map_err(|value| (INTERNAL_ERROR, value.to_string()))?;
        let hit = u32::from(resources.status == CacheStatus::Hit);
        let id = gateway::register(resources.bundle)
            .map_err(|status| (status, "gateway handle allocation failed".to_string()))?;
        Ok::<_, (i32, String)>((id, hit))
    }))
    .unwrap_or_else(|_| Err((INTERNAL_ERROR, "resource preparation panicked".to_string())));
    match result {
        Ok((id, hit)) => {
            // SAFETY: validated non-null outputs are writable by the ABI contract.
            unsafe {
                *handle = id;
                *cache_hit = hit;
            }
            OK
        }
        Err((status, message)) => {
            let mut count = message.len().min(ERROR_CAPACITY);
            while !message.is_char_boundary(count) {
                count -= 1;
            }
            // SAFETY: count is bounded by the required buffer capacity.
            unsafe {
                std::ptr::copy_nonoverlapping(message.as_ptr(), error, count);
                *error_length = count as u32;
            }
            status
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn local_creation_reports_errors_without_publishing_handles() {
        for request in [b"{}".as_slice(), b"null", b"{\"unexpected\":true}"] {
            let (mut handle, mut hit, mut length) = (99, 99, 99);
            let mut error = [0; ERROR_CAPACITY];
            // SAFETY: every buffer and scalar lives through the call.
            let status = unsafe {
                t7r_gateway_create_local(
                    request.as_ptr(),
                    request.len() as u32,
                    &mut handle,
                    &mut hit,
                    error.as_mut_ptr(),
                    error.len() as u32,
                    &mut length,
                )
            };
            assert_eq!(status, INVALID_ARGUMENT);
            assert_eq!((handle, hit), (0, 0));
            assert!(
                !std::str::from_utf8(&error[..length as usize])
                    .unwrap()
                    .is_empty()
            );
        }
    }
}
