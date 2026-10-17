use std::{
    collections::HashMap,
    panic::{AssertUnwindSafe, catch_unwind},
    sync::{Arc, Mutex, OnceLock},
};
use t7r_battle_resources::{MAX_BYTES, ResourceBundle};
use t7r_battle_runtime::gateway::{Gateway, platform::MAX_PLATFORM_BYTES};

pub const OK: i32 = 0;
pub const INVALID_ARGUMENT: i32 = 1;
pub const INVALID_HANDLE: i32 = 2;
pub const INVALID_STATE: i32 = 3;
pub const BUFFER_TOO_SMALL: i32 = 4;
pub const EMPTY: i32 = 5;
pub const STALE: i32 = 6;
pub const INTERNAL_ERROR: i32 = 7;
pub const BUSY: i32 = 8;

#[repr(C)]
#[derive(Default)]
pub struct GatewaySnapshot {
    pub abi_version: u32,
    pub struct_size: u32,
    pub epoch: u64,
    pub state: u32,
    pub login_port: u16,
    pub logic_port: u16,
    pub instance_port: u16,
    pub reserved: u16,
    pub active_connections: u32,
    pub pending_requests: u32,
    pub connection_errors: u32,
    pub rules_version: u32,
    pub resource_version: u32,
    pub map_id: u32,
    pub reserved2: u32,
}

#[derive(Default)]
struct Handles {
    next: u64,
    values: HashMap<u64, Arc<Mutex<Option<Gateway>>>>,
}
static HANDLES: OnceLock<Mutex<Handles>> = OnceLock::new();

fn registry() -> &'static Mutex<Handles> {
    HANDLES.get_or_init(|| Mutex::new(Handles::default()))
}
pub(crate) fn register(resources: ResourceBundle) -> Result<u64, i32> {
    let mut handles = registry().lock().map_err(|_| INTERNAL_ERROR)?;
    let id = handles.next.checked_add(1).ok_or(INTERNAL_ERROR)?;
    handles.next = id;
    handles.values.insert(
        id,
        Arc::new(Mutex::new(Some(Gateway::new(Arc::new(resources))))),
    );
    Ok(id)
}
pub(crate) fn guarded(action: impl FnOnce() -> Result<(), i32>) -> i32 {
    catch_unwind(AssertUnwindSafe(action))
        .unwrap_or(Err(INTERNAL_ERROR))
        .err()
        .unwrap_or(OK)
}

/// Private additive map selection. The caller detaches native sampling and stops
/// the old staged battle before changing the resource bundle.
#[unsafe(no_mangle)]
pub extern "C" fn t7r_gateway_select_map(handle: u64, map_id: u32) -> i32 {
    guarded(|| {
        with_gateway(handle, |gateway| {
            gateway.select_match_map(map_id).map_err(|_| INVALID_STATE)
        })
    })
}
pub(crate) fn with_gateway(
    handle: u64,
    action: impl FnOnce(&mut Gateway) -> Result<(), i32>,
) -> Result<(), i32> {
    let gateway = registry()
        .lock()
        .map_err(|_| INTERNAL_ERROR)?
        .values
        .get(&handle)
        .cloned()
        .ok_or(INVALID_HANDLE)?;
    let mut gateway = gateway.lock().map_err(|_| INTERNAL_ERROR)?;
    action(gateway.as_mut().ok_or(INVALID_HANDLE)?)
}

/// # Safety
/// `data` is readable for `length` bytes and `handle` is a writable, aligned u64.
/// The buffers are borrowed only for this call; no pointer is retained.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn t7r_gateway_create(data: *const u8, length: u32, handle: *mut u64) -> i32 {
    guarded(|| {
        if handle.is_null() {
            return Err(INVALID_ARGUMENT);
        }
        // SAFETY: caller supplies a valid output as specified by the ABI.
        unsafe {
            *handle = 0;
        }
        if data.is_null() || length == 0 || length as usize > MAX_BYTES {
            return Err(INVALID_ARGUMENT);
        }
        // SAFETY: the input is readable for length bytes by contract.
        let data = unsafe { std::slice::from_raw_parts(data, length as usize) };
        let resources = ResourceBundle::from_slice(data).map_err(|_| INVALID_ARGUMENT)?;
        let id = register(resources)?;
        // SAFETY: output was checked above; all initialization succeeded.
        unsafe {
            *handle = id;
        }
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn t7r_gateway_start(handle: u64, epoch: u64) -> i32 {
    guarded(|| {
        with_gateway(handle, |gateway| {
            gateway.start(epoch).map(|_| ()).map_err(|error| {
                if error.kind() == std::io::ErrorKind::InvalidInput {
                    INVALID_STATE
                } else {
                    INTERNAL_ERROR
                }
            })
        })
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn t7r_gateway_stop(handle: u64) -> i32 {
    guarded(|| with_gateway(handle, |gateway| gateway.stop().map_err(|_| INTERNAL_ERROR)))
}

#[unsafe(no_mangle)]
pub extern "C" fn t7r_gateway_release(handle: u64) -> i32 {
    guarded(|| {
        let gateway = registry()
            .lock()
            .map_err(|_| INTERNAL_ERROR)?
            .values
            .remove(&handle)
            .ok_or(INVALID_HANDLE)?;
        let mut released = gateway
            .lock()
            .map_err(|_| INTERNAL_ERROR)?
            .take()
            .ok_or(INVALID_HANDLE)?;
        released.stop().map_err(|_| INTERNAL_ERROR)
    })
}

/// # Safety
/// `output` is a writable, aligned GatewaySnapshot with ABI/version initialized.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn t7r_gateway_snapshot(handle: u64, output: *mut GatewaySnapshot) -> i32 {
    guarded(|| {
        if output.is_null() {
            return Err(INVALID_ARGUMENT);
        }
        // SAFETY: caller supplies the declared initialized structure.
        let output = unsafe { &mut *output };
        if output.abi_version != 1 || output.struct_size != size_of::<GatewaySnapshot>() as u32 {
            return Err(INVALID_ARGUMENT);
        }
        with_gateway(handle, |gateway| {
            let state = gateway.snapshot().map_err(|_| INTERNAL_ERROR)?;
            *output = GatewaySnapshot {
                abi_version: 1,
                struct_size: size_of::<GatewaySnapshot>() as u32,
                epoch: state.epoch,
                state: if state.failed {
                    2
                } else {
                    u32::from(state.running)
                },
                login_port: state.ports[0],
                logic_port: state.ports[1],
                instance_port: state.ports[2],
                active_connections: state.active_connections,
                pending_requests: state.pending_requests,
                connection_errors: state.connection_errors,
                rules_version: state.rules_version,
                resource_version: state.resource_version,
                map_id: state.map_id,
                ..Default::default()
            };
            Ok(())
        })
    })
}

/// # Safety
/// `required` is writable; `buffer` is writable for `capacity` bytes or null
/// when capacity is zero. The bundle remains owned by the gateway.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn t7r_gateway_resource_json(
    handle: u64,
    buffer: *mut u8,
    capacity: u32,
    required: *mut u32,
) -> i32 {
    guarded(|| {
        if required.is_null() || (buffer.is_null() && capacity != 0) {
            return Err(INVALID_ARGUMENT);
        }
        // SAFETY: the caller supplies a writable scalar.
        unsafe { *required = 0 };
        with_gateway(handle, |gateway| {
            let bytes =
                serde_json::to_vec(gateway.resource_bundle().data()).map_err(|_| INTERNAL_ERROR)?;
            if bytes.len() > MAX_BYTES {
                return Err(INTERNAL_ERROR);
            }
            // SAFETY: the output was validated before entering the registry.
            unsafe { *required = bytes.len() as u32 };
            if bytes.len() > capacity as usize {
                return Err(BUFFER_TOO_SMALL);
            }
            // SAFETY: capacity covers the serialized bundle.
            unsafe { std::ptr::copy_nonoverlapping(bytes.as_ptr(), buffer, bytes.len()) };
            Ok(())
        })
    })
}

/// # Safety
/// `required` is a writable u32; `buffer` is writable for `capacity` bytes,
/// or null when capacity is zero. A short buffer never consumes a request.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn t7r_gateway_read_request(
    handle: u64,
    buffer: *mut u8,
    capacity: u32,
    required: *mut u32,
) -> i32 {
    guarded(|| {
        if required.is_null() || (buffer.is_null() && capacity != 0) {
            return Err(INVALID_ARGUMENT);
        }
        // SAFETY: output follows the ABI contract.
        unsafe {
            *required = 0;
        }
        with_gateway(handle, |gateway| {
            let mut mailbox = gateway.mailbox().lock().map_err(|_| INTERNAL_ERROR)?;
            match mailbox.read(capacity as usize) {
                Err(size) => {
                    unsafe {
                        *required = size as u32;
                    }
                    Err(BUFFER_TOO_SMALL)
                }
                Ok(None) => Err(EMPTY),
                Ok(Some(bytes)) => {
                    // SAFETY: capacity was verified before dequeueing, bytes is nonempty.
                    unsafe {
                        std::ptr::copy_nonoverlapping(bytes.as_ptr(), buffer, bytes.len());
                        *required = bytes.len() as u32;
                    }
                    Ok(())
                }
            }
        })
    })
}

/// # Safety
/// `data` is readable for `length` bytes; ownership remains with the caller.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn t7r_gateway_complete(handle: u64, data: *const u8, length: u32) -> i32 {
    guarded(|| {
        if data.is_null() || length == 0 || length as usize > MAX_PLATFORM_BYTES {
            return Err(INVALID_ARGUMENT);
        }
        // SAFETY: input buffer is borrowed for this synchronous parse only.
        let bytes = unsafe { std::slice::from_raw_parts(data, length as usize) };
        with_gateway(handle, |gateway| {
            match gateway
                .mailbox()
                .lock()
                .map_err(|_| INTERNAL_ERROR)?
                .complete(bytes)
                .map_err(|_| INVALID_ARGUMENT)?
            {
                true => Ok(()),
                false => Err(STALE),
            }
        })
    })
}

/// # Safety
/// Same buffer contract as t7r_gateway_read_request; this call never consumes diagnostics.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn t7r_gateway_error(
    handle: u64,
    buffer: *mut u8,
    capacity: u32,
    required: *mut u32,
) -> i32 {
    guarded(|| {
        if required.is_null() || (buffer.is_null() && capacity != 0) {
            return Err(INVALID_ARGUMENT);
        }
        unsafe {
            *required = 0;
        }
        with_gateway(handle, |gateway| {
            let bytes = gateway
                .last_error()
                .map_err(|_| INTERNAL_ERROR)?
                .into_bytes();
            unsafe {
                *required = bytes.len() as u32;
            }
            if bytes.len() > capacity as usize {
                return Err(BUFFER_TOO_SMALL);
            }
            if !bytes.is_empty() {
                unsafe {
                    std::ptr::copy_nonoverlapping(bytes.as_ptr(), buffer, bytes.len());
                }
            }
            Ok(())
        })
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{sync::mpsc, thread, time::Duration};

    fn handle() -> u64 {
        register(
            ResourceBundle::from_slice(include_bytes!(
                "../../../../tests/fixtures/resources/bundle-v1.json"
            ))
            .unwrap(),
        )
        .unwrap()
    }

    #[test]
    fn waiting_for_one_gateway_does_not_hold_the_handle_registry() {
        let first = handle();
        let second = handle();
        let (entered, waiting) = mpsc::sync_channel(1);
        let (release, finish) = mpsc::sync_channel(1);
        let blocked = thread::spawn(move || {
            with_gateway(first, |_| {
                entered.send(()).unwrap();
                finish.recv().unwrap();
                Ok(())
            })
        });
        waiting.recv_timeout(Duration::from_secs(2)).unwrap();
        let (completed, completion) = mpsc::sync_channel(1);
        let independent = thread::spawn(move || {
            completed.send(with_gateway(second, |_| Ok(()))).unwrap();
        });
        let result = completion.recv_timeout(Duration::from_secs(2));
        // Release before asserting so a regression cannot strand the test thread.
        release.send(()).unwrap();
        assert_eq!(blocked.join().unwrap(), Ok(()));
        independent.join().unwrap();
        assert_eq!(result.unwrap(), Ok(()));
        assert_eq!(t7r_gateway_release(first), OK);
        assert_eq!(t7r_gateway_release(second), OK);
        assert_eq!(with_gateway(first, |_| Ok(())), Err(INVALID_HANDLE));
    }
}
