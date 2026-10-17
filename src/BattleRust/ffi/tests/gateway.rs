#![cfg(windows)]

use t7r_battle_ffi::gateway::*;

#[test]
fn contact_decode_failure_keeps_its_cause_through_followup_errors() {
    let data = include_bytes!("../../../../tests/fixtures/resources/bundle-v1.json");
    let line = include_str!("../../../../tests/fixtures/protocol/battle-contacts-v3.txt")
        .lines()
        .find(|line| line.starts_with("contacts "))
        .unwrap();
    let mut payload: Vec<_> = line
        .split_whitespace()
        .nth(1)
        .unwrap()
        .as_bytes()
        .chunks_exact(2)
        .map(|v| u8::from_str_radix(std::str::from_utf8(v).unwrap(), 16).unwrap())
        .skip(40)
        .collect();
    payload[116..120].fill(0);
    payload[120] = 0; // Targeted body contact without a body region is still invalid.
    let mut handle = 0;
    // SAFETY: all buffers are live, aligned and sized for the duration of each ABI call.
    unsafe {
        assert_eq!(
            t7r_gateway_create(data.as_ptr(), data.len() as u32, &mut handle),
            OK
        );
        let status = t7r_battle_ffi::battle::t7r_gateway_battle_submit(
            handle,
            14,
            payload.as_ptr(),
            payload.len() as u32,
        );
        let mut buffer = [0_u8; 4096];
        let mut length = 0;
        let read = t7r_gateway_error(
            handle,
            buffer.as_mut_ptr(),
            buffer.len() as u32,
            &mut length,
        );
        let first = String::from_utf8_lossy(&buffer[..length as usize]).to_string();
        assert_eq!(t7r_gateway_start(handle, 0), INVALID_STATE);
        let second_read = t7r_gateway_error(
            handle,
            buffer.as_mut_ptr(),
            buffer.len() as u32,
            &mut length,
        );
        let second = String::from_utf8_lossy(&buffer[..length as usize]).to_string();
        assert_eq!(t7r_gateway_release(handle), OK);
        assert_eq!((status, read, second_read), (INVALID_ARGUMENT, OK, OK));
        assert!(
            first.contains("contact payload decode failed") && first.contains("region"),
            "{first}"
        );
        assert!(first.contains("batchId=1"), "{first}");
        assert!(second.contains(&first), "{second}");
    }
}

#[test]
fn ffi_checks_resource_arguments_layout_and_released_handles() {
    let data = include_bytes!("../../../../tests/fixtures/resources/bundle-v1.json");
    let mut handle = 99;
    // SAFETY: all non-null buffers used here are live and correctly sized.
    unsafe {
        assert_eq!(
            t7r_gateway_create(std::ptr::null(), 0, &mut handle),
            INVALID_ARGUMENT
        );
        assert_eq!(handle, 0);
        assert_eq!(
            t7r_gateway_create(data.as_ptr(), data.len() as u32, std::ptr::null_mut()),
            INVALID_ARGUMENT
        );
        assert_eq!(
            t7r_gateway_create(data.as_ptr(), data.len() as u32, &mut handle),
            OK
        );
        assert_ne!(handle, 0);
        assert_eq!(size_of::<GatewaySnapshot>(), 56);
        let mut state = GatewaySnapshot::default();
        assert_eq!(t7r_gateway_snapshot(handle, &mut state), INVALID_ARGUMENT);
        state.abi_version = 1;
        state.struct_size = 56;
        assert_eq!(t7r_gateway_snapshot(handle, &mut state), OK);
        assert_eq!((state.state, state.epoch, state.map_id), (0, 0, 1028));
        assert_eq!(t7r_gateway_start(handle, 0), INVALID_STATE);
        assert_eq!(t7r_gateway_start(handle, 1), OK);
        let mut preparation = 99;
        assert_eq!(
            t7r_battle_ffi::battle::t7r_gateway_battle_preparation_open(
                handle,
                std::ptr::null_mut()
            ),
            INVALID_ARGUMENT
        );
        assert_eq!(
            t7r_battle_ffi::battle::t7r_gateway_battle_preparation_open(handle, &mut preparation),
            INVALID_STATE
        );
        assert_eq!(preparation, 0);
        let mut reserved = 99;
        assert_eq!(
            t7r_battle_ffi::battle::t7r_gateway_battle_reserve_rearm(handle, std::ptr::null_mut()),
            INVALID_ARGUMENT
        );
        assert_eq!(
            t7r_battle_ffi::battle::t7r_gateway_battle_reserve_rearm(handle, &mut reserved),
            INVALID_STATE
        );
        assert_eq!(reserved, 0);
        let mut required = 99;
        assert_eq!(
            t7r_gateway_read_request(handle, std::ptr::null_mut(), 1, &mut required),
            INVALID_ARGUMENT
        );
        assert_eq!(
            t7r_gateway_read_request(handle, std::ptr::null_mut(), 0, &mut required),
            EMPTY
        );
        assert_eq!(required, 0);
        assert_eq!(
            t7r_gateway_complete(handle, std::ptr::null(), 0),
            INVALID_ARGUMENT
        );
        assert_eq!(
            t7r_gateway_complete(handle, data.as_ptr(), 4097),
            INVALID_ARGUMENT
        );
        assert_eq!(t7r_gateway_release(handle), OK);
        assert_eq!(t7r_gateway_stop(handle), INVALID_HANDLE);
        assert_eq!(t7r_gateway_snapshot(handle, &mut state), INVALID_HANDLE);
        assert_eq!(t7r_gateway_release(handle), INVALID_HANDLE);
    }
}

#[test]
fn ffi_release_serializes_with_start_without_leaving_a_live_worker() {
    let data = include_bytes!("../../../../tests/fixtures/resources/bundle-v1.json");
    let mut handle = 0;
    // SAFETY: the source and output are live for the duration of the call.
    assert_eq!(
        unsafe { t7r_gateway_create(data.as_ptr(), data.len() as u32, &mut handle) },
        OK
    );
    let start = std::thread::spawn(move || t7r_gateway_start(handle, 1));
    assert_eq!(t7r_gateway_release(handle), OK);
    assert!(matches!(start.join().unwrap(), OK | INVALID_HANDLE));
    assert_eq!(t7r_gateway_start(handle, 2), INVALID_HANDLE);
}

#[test]
fn ffi_resource_copy_is_bounded_and_survives_stop() {
    let data = include_bytes!("../../../../tests/fixtures/resources/bundle-v1.json");
    let mut handle = 0;
    // SAFETY: all buffers remain live and have the declared capacity.
    unsafe {
        assert_eq!(
            t7r_gateway_create(data.as_ptr(), data.len() as u32, &mut handle),
            OK
        );
        let mut required = 0;
        assert_eq!(
            t7r_gateway_resource_json(handle, std::ptr::null_mut(), 1, &mut required),
            INVALID_ARGUMENT
        );
        assert_eq!(
            t7r_gateway_resource_json(handle, std::ptr::null_mut(), 0, &mut required),
            BUFFER_TOO_SMALL
        );
        assert!(required > 0 && required as usize <= t7r_battle_resources::MAX_BYTES);
        let mut short = [0_u8; 1];
        assert_eq!(
            t7r_gateway_resource_json(handle, short.as_mut_ptr(), 1, &mut required),
            BUFFER_TOO_SMALL
        );
        let mut bytes = vec![0_u8; required as usize];
        assert_eq!(
            t7r_gateway_resource_json(handle, bytes.as_mut_ptr(), required, &mut required),
            OK
        );
        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(&bytes).unwrap(),
            serde_json::from_slice::<serde_json::Value>(data).unwrap()
        );
        assert_eq!(t7r_gateway_stop(handle), OK);
        assert_eq!(
            t7r_gateway_resource_json(
                handle,
                bytes.as_mut_ptr(),
                bytes.len() as u32,
                &mut required
            ),
            OK
        );
        assert_eq!(t7r_gateway_release(handle), OK);
        assert_eq!(
            t7r_gateway_resource_json(
                handle,
                bytes.as_mut_ptr(),
                bytes.len() as u32,
                &mut required
            ),
            INVALID_HANDLE
        );
    }
}
