pub const ABI_VERSION: u32 = 1;

#[cfg(windows)]
pub mod gateway;

#[cfg(windows)]
pub mod battle;

#[cfg(windows)]
mod local_resources;

#[cfg(windows)]
mod titles;

#[unsafe(no_mangle)]
pub extern "C" fn t7r_runtime_abi_version() -> u32 {
    ABI_VERSION
}
