use crate::gateway::{INTERNAL_ERROR, INVALID_ARGUMENT, guarded, with_gateway};

#[repr(C)]
pub struct TitleRequest {
    pub abi_version: u32,
    pub struct_size: u32,
    pub request_id: u64,
    pub epoch: u64,
    pub title_id: u32,
    pub reserved: u32,
}

/// # Safety
/// `json` must be readable for `length` bytes. No input pointer is retained.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn t7r_gateway_publish_titles(
    handle: u64,
    epoch: u64,
    json: *const u8,
    length: u32,
    request_id: u64,
    success: u32,
) -> i32 {
    guarded(|| {
        if json.is_null() || length == 0 || length > 1024 * 1024 || success > 1 {
            return Err(INVALID_ARGUMENT);
        }
        // SAFETY: the caller supplies the readable bounded buffer described above.
        let bytes = unsafe { std::slice::from_raw_parts(json, length as usize) };
        with_gateway(handle, |gateway| {
            gateway
                .publish_titles(epoch, bytes, request_id, success == 1)
                .map_err(|_| INVALID_ARGUMENT)
        })
    })
}

/// # Safety
/// `output` points to a writable, aligned TitleRequest with initialized version and size.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn t7r_gateway_title_request(handle: u64, output: *mut TitleRequest) -> i32 {
    guarded(|| {
        if output.is_null() {
            return Err(INVALID_ARGUMENT);
        }
        // SAFETY: the caller provides the initialized writable structure described above.
        let output = unsafe { &mut *output };
        if output.abi_version != crate::ABI_VERSION
            || output.struct_size != size_of::<TitleRequest>() as u32
            || output.reserved != 0
        {
            return Err(INVALID_ARGUMENT);
        }
        with_gateway(handle, |gateway| {
            let request = gateway.title_request().map_err(|_| INTERNAL_ERROR)?;
            output.request_id = request.request_id;
            output.epoch = request.epoch;
            output.title_id = request.title_id;
            Ok(())
        })
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn layout_and_bad_layout_rejection() {
        assert_eq!(size_of::<TitleRequest>(), 32);
        assert_eq!(std::mem::offset_of!(TitleRequest, epoch), 16);
        let mut request = TitleRequest {
            abi_version: 0,
            struct_size: 32,
            request_id: 0,
            epoch: 0,
            title_id: 0,
            reserved: 0,
        };
        // SAFETY: test supplies a live writable structure.
        assert_eq!(
            unsafe { t7r_gateway_title_request(0, &mut request) },
            INVALID_ARGUMENT
        );
    }
}
