use serde_json::Value;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use t7r_battle_resources::cache::{CacheStatus, CachedBundle, load_or_build};

const CLIENT: &[u8] = include_bytes!("../../../../tests/fixtures/resources/client-cache-v1.vfs");

struct Fixture {
    root: PathBuf,
    client: PathBuf,
    cache: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        static NEXT: AtomicU64 = AtomicU64::new(1);
        let root = std::env::temp_dir().join(format!(
            "t7r-resource-cache-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir(&root).unwrap();
        let client = root.join("Game/Bin");
        std::fs::create_dir_all(&client).unwrap();
        std::fs::create_dir(root.join("Game/vfs")).unwrap();
        std::fs::write(root.join("Game/vfs/data1.vfs"), CLIENT).unwrap();
        Self {
            cache: root.join("Cache/nested"),
            root,
            client,
        }
    }
    fn load(&self) -> CachedBundle {
        load_or_build(&self.client, &self.cache, 3, 7, "fixture-v1").unwrap()
    }
    fn archive(&self) -> PathBuf {
        self.root.join("Game/vfs/data1.vfs")
    }
    fn cache_file(&self) -> PathBuf {
        std::fs::read_dir(&self.cache)
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .find(|path| {
                path.extension()
                    .is_some_and(|extension| extension == "json")
            })
            .unwrap()
    }
    fn change_names(&self) {
        let path = self.archive();
        let before = std::fs::metadata(&path).unwrap();
        let mut bytes = std::fs::read(&path).unwrap();
        let offset = bytes
            .windows(9)
            .position(|window| window == b"Fixture00")
            .unwrap();
        bytes[offset..offset + 9].copy_from_slice(b"Changed00");
        let mut file = OpenOptions::new()
            .write(true)
            .truncate(true)
            .open(&path)
            .unwrap();
        file.write_all(&bytes).unwrap();
        file.set_modified(before.modified().unwrap()).unwrap();
        drop(file);
        let after = std::fs::metadata(path).unwrap();
        assert_eq!(before.len(), after.len());
        assert_eq!(before.modified().unwrap(), after.modified().unwrap());
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        assert_eq!(self.root.parent(), Some(std::env::temp_dir().as_path()));
        std::fs::remove_dir_all(&self.root).unwrap();
    }
}

#[test]
fn missing_cache_builds_then_hits_without_touching_client_or_rewriting_cache() {
    let fixture = Fixture::new();
    let built = fixture.load();
    assert_eq!(built.status, CacheStatus::Rebuilt);
    let path = fixture.cache_file();
    let cached = std::fs::read(&path).unwrap();
    let modified = std::fs::metadata(&path).unwrap().modified().unwrap();
    let hit = fixture.load();
    assert_eq!(hit.status, CacheStatus::Hit);
    assert_eq!(hit.bundle.data(), built.bundle.data());
    assert_eq!(std::fs::read(&path).unwrap(), cached);
    assert_eq!(
        std::fs::metadata(path).unwrap().modified().unwrap(),
        modified
    );
    assert_eq!(std::fs::read(fixture.archive()).unwrap(), CLIENT);
    assert_eq!(std::fs::read_dir(&fixture.client).unwrap().count(), 0);
    assert_eq!(std::fs::read_dir(&fixture.cache).unwrap().count(), 2);
}

#[test]
fn source_content_changes_invalidate_even_with_identical_size_and_modified_time() {
    let fixture = Fixture::new();
    fixture.load();
    let path = fixture.cache_file();
    let before = std::fs::read(&path).unwrap();
    fixture.change_names();
    let loaded = fixture.load();
    assert_eq!(loaded.status, CacheStatus::Rebuilt);
    assert_eq!(loaded.bundle.data()["botNames"][0], "Changed00");
    assert_ne!(std::fs::read(path).unwrap(), before);
    assert_eq!(fixture.load().status, CacheStatus::Hit);
    assert_eq!(std::fs::read_dir(&fixture.cache).unwrap().count(), 2);
}

#[test]
fn damaged_manifest_payload_digest_or_bundle_rebuilds_atomically() {
    let fixture = Fixture::new();
    fixture.load();
    let path = fixture.cache_file();
    let original = std::fs::read(&path).unwrap();
    let parsed: Value = serde_json::from_slice(&original).unwrap();
    let mut mutations = vec![b"broken".to_vec(), original[..original.len() - 1].to_vec()];
    for key in ["cacheVersion", "identity", "bundleDigest"] {
        let mut value = parsed.clone();
        value[key] = Value::from(0);
        mutations.push(serde_json::to_vec(&value).unwrap());
    }
    let mut changed = parsed.clone();
    changed["bundle"]["botNames"][0] = Value::from("Changed00");
    mutations.push(serde_json::to_vec(&changed).unwrap());
    let mut changed = parsed.clone();
    changed["unknown"] = Value::from(1);
    mutations.push(serde_json::to_vec(&changed).unwrap());
    for bytes in mutations {
        std::fs::write(&path, bytes).unwrap();
        let result = fixture.load();
        assert_eq!(result.status, CacheStatus::Rebuilt);
        assert_eq!(std::fs::read(&path).unwrap(), original);
    }
    let file = OpenOptions::new().write(true).open(&path).unwrap();
    file.set_len((t7r_battle_resources::MAX_BYTES + 4097) as u64)
        .unwrap();
    drop(file);
    assert_eq!(fixture.load().status, CacheStatus::Rebuilt);
    assert_eq!(std::fs::read(&path).unwrap(), original);
}

#[test]
fn versions_and_selected_client_are_separate_cache_identities() {
    let fixture = Fixture::new();
    fixture.load();
    for (rules, resources, version) in [
        (4, 7, "fixture-v1"),
        (3, 8, "fixture-v1"),
        (3, 7, "fixture-v2"),
    ] {
        assert_eq!(
            load_or_build(&fixture.client, &fixture.cache, rules, resources, version)
                .unwrap()
                .status,
            CacheStatus::Rebuilt
        );
        assert_eq!(
            load_or_build(&fixture.client, &fixture.cache, rules, resources, version)
                .unwrap()
                .status,
            CacheStatus::Hit
        );
    }
    let second = fixture.root.join("OtherGame/Bin");
    std::fs::create_dir_all(&second).unwrap();
    std::fs::create_dir(fixture.root.join("OtherGame/vfs")).unwrap();
    std::fs::write(fixture.root.join("OtherGame/vfs/data1.vfs"), CLIENT).unwrap();
    assert_eq!(
        load_or_build(&second, &fixture.cache, 3, 7, "fixture-v1")
            .unwrap()
            .status,
        CacheStatus::Rebuilt
    );
    assert_eq!(std::fs::read_dir(&fixture.cache).unwrap().count(), 10);
}

#[test]
fn a_cache_hit_still_rejects_overlays_and_bad_source_without_destroying_old_cache() {
    let fixture = Fixture::new();
    fixture.load();
    let path = fixture.cache_file();
    let cached = std::fs::read(&path).unwrap();
    for overlay in [
        "vfs/update.vfs",
        "data/config/vfsconfig.cfg",
        "data/table/s_weapon_cli.bin",
    ] {
        let overlay = fixture.root.join("Game").join(overlay);
        std::fs::create_dir_all(overlay.parent().unwrap()).unwrap();
        std::fs::write(&overlay, b"overlay").unwrap();
        let error = load_or_build(&fixture.client, &fixture.cache, 3, 7, "fixture-v1").unwrap_err();
        assert_eq!(error.code, "unsupported_overlay");
        assert_eq!(std::fs::read(&path).unwrap(), cached);
        std::fs::remove_file(overlay).unwrap();
    }
    std::fs::write(fixture.archive(), b"invalid source").unwrap();
    assert!(load_or_build(&fixture.client, &fixture.cache, 3, 7, "fixture-v1").is_err());
    assert_eq!(std::fs::read(path).unwrap(), cached);
}

#[test]
fn invalid_cache_locations_and_versions_do_not_write_into_the_client() {
    let fixture = Fixture::new();
    for cache in [
        Path::new("relative-cache").to_owned(),
        fixture.client.join("cache"),
        fixture.root.join("Game/cache"),
    ] {
        assert!(load_or_build(&fixture.client, &cache, 3, 7, "fixture-v1").is_err());
        assert!(!cache.exists());
    }
    for (rules, resources, version) in [
        (0, 7, "fixture-v1"),
        (3, 0, "fixture-v1"),
        (3, 7, "../fixture"),
    ] {
        assert!(load_or_build(&fixture.client, &fixture.cache, rules, resources, version).is_err());
        assert!(!fixture.cache.exists());
    }
    assert!(
        load_or_build(
            Path::new("relative-client"),
            &fixture.cache,
            3,
            7,
            "fixture-v1"
        )
        .is_err()
    );
}

#[test]
fn concurrent_creators_publish_once_and_read_complete_bundles() {
    let fixture = Fixture::new();
    let barrier = std::sync::Arc::new(std::sync::Barrier::new(6));
    let workers: Vec<_> = (0..6)
        .map(|_| {
            let (client, cache, barrier) = (
                fixture.client.clone(),
                fixture.cache.clone(),
                barrier.clone(),
            );
            std::thread::spawn(move || {
                barrier.wait();
                load_or_build(&client, &cache, 3, 7, "fixture-v1").unwrap()
            })
        })
        .collect();
    let results: Vec<_> = workers
        .into_iter()
        .map(|worker| worker.join().unwrap())
        .collect();
    assert_eq!(
        results
            .iter()
            .filter(|result| result.status == CacheStatus::Rebuilt)
            .count(),
        1
    );
    assert!(
        results
            .iter()
            .all(|result| result.bundle.data() == results[0].bundle.data())
    );
    assert_eq!(std::fs::read_dir(&fixture.cache).unwrap().count(), 2);
    assert_eq!(fixture.load().status, CacheStatus::Hit);
}

#[cfg(windows)]
#[test]
fn failed_windows_atomic_replace_preserves_old_cache_and_cleans_temporary_file() {
    use std::os::windows::fs::OpenOptionsExt;
    let fixture = Fixture::new();
    fixture.load();
    let path = fixture.cache_file();
    let original = std::fs::read(&path).unwrap();
    fixture.change_names();
    let reader = OpenOptions::new()
        .read(true)
        .share_mode(1)
        .open(&path)
        .unwrap();
    let error = load_or_build(&fixture.client, &fixture.cache, 3, 7, "fixture-v1").unwrap_err();
    assert_eq!(error.code, "cache_write");
    assert_eq!(std::fs::read(&path).unwrap(), original);
    assert_eq!(std::fs::read_dir(&fixture.cache).unwrap().count(), 2);
    drop(reader);
    assert_eq!(fixture.load().status, CacheStatus::Rebuilt);
    assert_eq!(fixture.load().status, CacheStatus::Hit);
}

#[test]
fn unreadable_cache_errors_are_explicit_instead_of_silently_bypassed() {
    let fixture = Fixture::new();
    fixture.load();
    let path = fixture.cache_file();
    std::fs::remove_file(&path).unwrap();
    std::fs::create_dir(&path).unwrap();
    assert_eq!(
        load_or_build(&fixture.client, &fixture.cache, 3, 7, "fixture-v1")
            .unwrap_err()
            .code,
        "cache_read"
    );
}
