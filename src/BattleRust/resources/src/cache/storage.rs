use super::input;
use crate::vfs::ResourceError;
use crate::{MAX_BYTES, ResourceBundle};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::fs::{File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

const MAX_CACHE_BYTES: usize = MAX_BYTES + 4096;

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Envelope {
    cache_version: u32,
    identity: String,
    bundle_digest: String,
    bundle: Value,
}

fn failure(code: &'static str, error: impl std::fmt::Display) -> ResourceError {
    ResourceError::new(code, "resource cache", "", error)
}

pub(super) fn lock(directory: &Path, key: &str) -> Result<File, ResourceError> {
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(directory.join(format!("t7r-battle-{key}.lock")))
        .map_err(|error| failure("cache_lock", error))?;
    file.lock().map_err(|error| failure("cache_lock", error))?;
    Ok(file)
}

pub(super) fn read(path: &Path, identity: &str) -> Result<Option<ResourceBundle>, ResourceError> {
    let file = match File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(failure("cache_read", error)),
    };
    let mut bytes = Vec::new();
    file.take((MAX_CACHE_BYTES + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|error| failure("cache_read", error))?;
    if bytes.len() > MAX_CACHE_BYTES {
        return Ok(None);
    }
    let envelope: Envelope = match serde_json::from_slice(&bytes) {
        Ok(value) => value,
        Err(_) => return Ok(None),
    };
    if envelope.cache_version != 1 || envelope.identity != identity {
        return Ok(None);
    }
    let bytes =
        serde_json::to_vec(&envelope.bundle).map_err(|error| failure("cache_read", error))?;
    if bytes.len() > MAX_BYTES || input::digest(&bytes) != envelope.bundle_digest {
        return Ok(None);
    }
    Ok(ResourceBundle::from_slice(&bytes).ok())
}

pub(super) fn publish(
    path: &Path,
    identity: &str,
    bundle: &ResourceBundle,
) -> Result<(), ResourceError> {
    let bytes = serde_json::to_vec(bundle.data()).map_err(|error| failure("cache_write", error))?;
    let envelope = Envelope {
        cache_version: 1,
        identity: identity.to_owned(),
        bundle_digest: input::digest(&bytes),
        bundle: bundle.data().clone(),
    };
    let encoded = serde_json::to_vec(&envelope).map_err(|error| failure("cache_write", error))?;
    if encoded.len() > MAX_CACHE_BYTES {
        return Err(failure("cache_write", "resource cache exceeds size limit"));
    }
    let (mut file, temporary) =
        create_temporary(path).map_err(|error| failure("cache_write", error))?;
    let written = file.write_all(&encoded).and_then(|_| file.sync_all());
    drop(file);
    if let Err(error) = written {
        return Err(cleanup_failure(temporary, error));
    }
    match std::fs::rename(&temporary, path) {
        Ok(()) => Ok(()),
        Err(error) => Err(cleanup_failure(temporary, error)),
    }
}

fn create_temporary(path: &Path) -> io::Result<(File, PathBuf)> {
    static NEXT: AtomicU64 = AtomicU64::new(1);
    for _ in 0..128 {
        let suffix = NEXT.fetch_add(1, Ordering::Relaxed);
        let temporary = path.with_extension(format!("{}.{}.tmp", std::process::id(), suffix));
        match OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)
        {
            Ok(file) => return Ok((file, temporary)),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        }
    }
    Err(io::Error::new(
        io::ErrorKind::AlreadyExists,
        "resource cache temporary filenames are occupied",
    ))
}

fn cleanup_failure(temporary: PathBuf, error: io::Error) -> ResourceError {
    match std::fs::remove_file(temporary) {
        Ok(()) => failure("cache_write", error),
        Err(cleanup) => failure(
            "cache_write",
            format!("{error}; temporary cleanup failed: {cleanup}"),
        ),
    }
}
