//! Private, disposable resource cache; original client files are always read-only.
mod input;
mod storage;

use crate::ResourceBundle;
use crate::vfs::ResourceError;
use sha2::{Digest, Sha256};
use std::path::{Component, Path, PathBuf};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CacheStatus {
    Hit,
    Rebuilt,
}

#[derive(Debug)]
pub struct CachedBundle {
    pub bundle: ResourceBundle,
    pub status: CacheStatus,
}

pub fn load_or_build(
    client_directory: &Path,
    cache_directory: &Path,
    rules_version: u32,
    resource_version: u32,
    client_version: &str,
) -> Result<CachedBundle, ResourceError> {
    validate_options(
        client_directory,
        cache_directory,
        rules_version,
        resource_version,
        client_version,
    )?;
    let paths = Paths::resolve(client_directory, cache_directory)?;
    let key = paths.key(rules_version, resource_version, client_version);
    let _lock = storage::lock(&paths.cache, &key)?;
    let inputs = input::Snapshot::read(client_directory)?;
    let identity = inputs.identity(&key);
    let path = paths.cache.join(format!("t7r-battle-{key}.json"));
    if let Some(bundle) = storage::read(&path, &identity)?
        && bundle.rules_version() == rules_version
        && bundle.resource_version() == resource_version
        && bundle.data()["clientVersion"] == client_version
    {
        return Ok(CachedBundle {
            bundle,
            status: CacheStatus::Hit,
        });
    }
    let bundle = inputs.build(rules_version, resource_version, client_version)?;
    storage::publish(&path, &identity, &bundle)?;
    Ok(CachedBundle {
        bundle,
        status: CacheStatus::Rebuilt,
    })
}

fn validate_options(
    client: &Path,
    cache: &Path,
    rules: u32,
    resources: u32,
    version: &str,
) -> Result<(), ResourceError> {
    if !client.is_absolute()
        || !cache.is_absolute()
        || cache.components().any(|part| part == Component::ParentDir)
    {
        return Err(ResourceError::new(
            "cache_arguments",
            "resource cache",
            "",
            "absolute client/cache directories without parent traversal are required",
        ));
    }
    if rules == 0
        || resources == 0
        || version.is_empty()
        || version.len() > 64
        || !version
            .bytes()
            .all(|value| value.is_ascii_alphanumeric() || b"_.-".contains(&value))
    {
        return Err(ResourceError::new(
            "cache_arguments",
            "resource cache",
            "",
            "invalid battle bundle version identifiers",
        ));
    }
    Ok(())
}

struct Paths {
    client: PathBuf,
    root: PathBuf,
    cache: PathBuf,
}

impl Paths {
    fn resolve(client: &Path, cache: &Path) -> Result<Self, ResourceError> {
        let failure = |error| ResourceError::new("cache_path", "resource cache", "", error);
        let canonical_client = client.canonicalize().map_err(failure)?;
        if !canonical_client.is_dir() {
            return Err(failure(std::io::Error::other(
                "selected client directory is not a directory",
            )));
        }
        let root = client
            .parent()
            .ok_or_else(|| failure(std::io::Error::other("client directory has no parent")))?
            .canonicalize()
            .map_err(failure)?;
        let candidate = resolve_missing(cache).map_err(failure)?;
        if contains(&root, &candidate) || contains(&canonical_client, &candidate) {
            return Err(failure(std::io::Error::other(
                "cache directory must be outside the selected client installation",
            )));
        }
        std::fs::create_dir_all(cache).map_err(failure)?;
        let cache = cache.canonicalize().map_err(failure)?;
        if contains(&root, &cache) || contains(&canonical_client, &cache) {
            return Err(failure(std::io::Error::other(
                "cache directory resolves inside the selected client installation",
            )));
        }
        Ok(Self {
            client: canonical_client,
            root,
            cache,
        })
    }

    fn key(&self, rules: u32, resources: u32, version: &str) -> String {
        let mut digest = Sha256::new();
        input::field(&mut digest, b"T7R.resource-cache.key.v1");
        input::field(&mut digest, self.client.as_os_str().as_encoded_bytes());
        input::field(&mut digest, self.root.as_os_str().as_encoded_bytes());
        input::field(&mut digest, &rules.to_le_bytes());
        input::field(&mut digest, &resources.to_le_bytes());
        input::field(&mut digest, version.as_bytes());
        input::hex(&digest.finalize())
    }
}

fn contains(root: &Path, path: &Path) -> bool {
    #[cfg(windows)]
    {
        Path::new(&path.as_os_str().to_string_lossy().to_lowercase())
            .starts_with(root.as_os_str().to_string_lossy().to_lowercase())
    }
    #[cfg(not(windows))]
    {
        path.starts_with(root)
    }
}

fn resolve_missing(path: &Path) -> std::io::Result<PathBuf> {
    let mut parent = path;
    let mut suffix = Vec::new();
    loop {
        match parent.canonicalize() {
            Ok(mut value) => {
                for name in suffix.into_iter().rev() {
                    value.push(name);
                }
                return Ok(value);
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                let name = parent.file_name().ok_or(error)?;
                suffix.push(name);
                parent = parent.parent().ok_or_else(|| {
                    std::io::Error::other("cache directory has no existing ancestor")
                })?;
            }
            Err(error) => return Err(error),
        }
    }
}
