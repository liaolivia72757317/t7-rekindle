use serde_json::Value;
use std::io::Cursor;
use std::path::Path;
use t7r_battle_resources::catalogue::{
    CATEGORY_PATH, Catalogue, GROUP_PATH, HERO_PATH, UNLOCK_PATH, WEAPON_PATH,
};
use t7r_battle_resources::vfs::{Archive, ClientResources};

const PATHS: [&str; 5] = [
    HERO_PATH,
    UNLOCK_PATH,
    GROUP_PATH,
    WEAPON_PATH,
    CATEGORY_PATH,
];

fn hex(value: &str) -> Vec<u8> {
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|bytes| u8::from_str_radix(std::str::from_utf8(bytes).unwrap(), 16).unwrap())
        .collect()
}

fn fixture() -> Value {
    serde_json::from_str(include_str!(
        "../../../../tests/fixtures/resources/catalogue-v1.json"
    ))
    .unwrap()
}

fn tables() -> [Vec<u8>; 5] {
    let fixture = fixture();
    let mut archive = Archive::from_reader(
        Cursor::new(hex(fixture["archiveHex"].as_str().unwrap())),
        "data1.vfs",
    )
    .unwrap();
    PATHS.map(|path| archive.read_entry(path).unwrap())
}

fn parse(tables: &[Vec<u8>; 5]) -> std::io::Result<Catalogue> {
    let [heroes, unlocks, groups, weapons, categories] = tables;
    Catalogue::parse(heroes, unlocks, groups, weapons, categories)
}

fn put_u16(data: &mut [u8], offset: usize, value: u16) {
    data[offset..offset + 2].copy_from_slice(&value.to_le_bytes());
}

fn put_u32(data: &mut [u8], offset: usize, value: u32) {
    data[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
}

#[test]
fn vfs_matches_python_vectors_for_repacking_chunks_paths_and_corruption() {
    let cases: Value = serde_json::from_str(include_str!(
        "../../../../tests/fixtures/resources/vfs-v1.json"
    ))
    .unwrap();
    for case in cases.as_array().unwrap() {
        let result = Archive::from_reader(
            Cursor::new(hex(case["archiveHex"].as_str().unwrap())),
            "fixture.vfs",
        );
        if case["openError"] == true {
            assert_eq!(
                result.err().unwrap().code,
                case["error"],
                "{}",
                case["name"]
            );
            continue;
        }
        let mut archive = result.unwrap_or_else(|error| panic!("{}: {error}", case["name"]));
        assert!(archive.paths().all(|path| path.starts_with("data/")));
        let result = archive.read_entry(case["path"].as_str().unwrap());
        if let Some(error) = case["error"].as_str() {
            assert_eq!(result.unwrap_err().code, error, "{}", case["name"]);
        } else {
            assert_eq!(
                result.unwrap(),
                hex(case["unitHex"].as_str().unwrap())
                    .repeat(case["repeat"].as_u64().unwrap() as usize),
                "{}",
                case["name"]
            );
        }
    }
}

#[test]
fn catalogue_matches_original_unlock_graph_supported_grid_and_defaults() {
    let catalogue = parse(&tables()).unwrap();
    assert_eq!(catalogue.to_json(), fixture()["expected"]);
    assert_eq!(catalogue.position(1101).unwrap(), 1);
    assert_eq!(catalogue.reference(2102, 2).unwrap(), 2102);
    assert!(catalogue.reference(2102, 1).is_err());
    assert!(catalogue.position(9999).is_err());
    let hero = &catalogue.heroes()[&1101];
    assert_eq!(&hero.native_groups[0][..3], &[701, 799, 703]);
    assert_eq!(&hero.groups[0][..3], &[701, 0, 703]);
    assert_eq!(hero.defaults, [(0, 0)]);
    assert_eq!(hero.weapon(0, 0).unwrap(), 701);
    for (group, index) in [(0, 1), (8, 0), (0, 6), (usize::MAX, 0)] {
        assert!(hero.weapon(group, index).is_err());
    }
    assert!(catalogue.weapons()[&703].shield());
    assert!(!catalogue.weapons().contains_key(&799));
    assert!(!catalogue.heroes().contains_key(&9900));
}

#[test]
fn catalogue_rejects_malformed_tables_before_using_offsets() {
    for table in 0..5 {
        let original = tables();
        for length in [0, 15, 139, original[table].len() - 1] {
            let mut changed = original.clone();
            changed[table].truncate(length);
            assert!(parse(&changed).is_err());
        }
        for offset in [0, 4, 8, 12, 64, 132] {
            let mut changed = original.clone();
            changed[table][offset] ^= 1;
            assert!(parse(&changed).is_err(), "table={table} offset={offset}");
        }
    }
}

#[test]
fn catalogue_rejects_duplicate_keys_bad_graphs_and_invalid_strings() {
    for (table, width, short_key) in [
        (0, 7286, false),
        (2, 1434, false),
        (3, 2829, false),
        (4, 550, true),
    ] {
        let mut changed = tables();
        let row = changed[table][140..140 + width].to_vec();
        let count = (changed[table].len() - 140) / width;
        changed[table].extend_from_slice(&row);
        put_u32(&mut changed[table], 12, count as u32 + 1);
        assert!(parse(&changed).is_err(), "duplicate key short={short_key}");
    }
    for (table, offset, value) in [(0, 140 + 4209, 7), (1, 140 + 36, 2), (2, 140 + 64, 7)] {
        let mut changed = tables();
        put_u16(&mut changed[table], offset, value);
        assert!(parse(&changed).is_err());
    }
    for (table, offset) in [(0, 140 + 4211), (1, 140 + 42), (2, 140 + 66)] {
        let mut changed = tables();
        put_u32(&mut changed[table], offset, 999999);
        assert!(parse(&changed).is_err());
    }
    for (table, offset, size) in [(0, 140 + 4, 30), (0, 140 + 163, 12), (3, 140 + 16, 40)] {
        let mut changed = tables();
        changed[table][offset..offset + size].fill(b'A');
        assert!(parse(&changed).is_err());
        changed[table][offset] = 0xff;
        changed[table][offset + 1] = 0;
        assert!(parse(&changed).is_err());
    }
    let mut changed = tables();
    put_u16(&mut changed[3], 140 + 56, 999);
    assert!(
        parse(&changed).is_err(),
        "referenced unknown weapon category"
    );
    let mut changed = tables();
    put_u16(&mut changed[1], 140 + 300 + 36, 0);
    assert!(parse(&changed).is_err(), "missing default hero");
    let mut changed = tables();
    put_u32(&mut changed[3], 140 + 2829, 999999);
    assert!(parse(&changed).is_err(), "missing fists");
    let mut changed = tables();
    put_u32(&mut changed[2], 140 + 66, 703);
    assert!(parse(&changed).is_err(), "shield-only hero");
    let mut changed = tables();
    put_u32(&mut changed[2], 140 + 66, 0);
    put_u32(&mut changed[3], 140, 0);
    assert!(
        parse(&changed).is_err(),
        "empty zero slot is not a melee weapon"
    );
}

struct TemporaryClient(std::path::PathBuf);

impl TemporaryClient {
    fn new() -> Self {
        static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);
        let path = std::env::temp_dir().join(format!(
            "t7r-resources-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        std::fs::create_dir(&path).unwrap();
        std::fs::create_dir(path.join("Bin")).unwrap();
        std::fs::create_dir(path.join("vfs")).unwrap();
        Self(path)
    }
}

impl Drop for TemporaryClient {
    fn drop(&mut self) {
        std::fs::remove_dir_all(&self.0).unwrap();
    }
}

#[test]
fn selected_installation_reads_archives_only_and_leaves_disk_unchanged() {
    assert!(ClientResources::new(Path::new("relative/Bin")).is_err());
    let client = TemporaryClient::new();
    let path = client.0.join("vfs/data1.vfs");
    let data = hex(fixture()["archiveHex"].as_str().unwrap());
    std::fs::write(&path, &data).unwrap();
    let mut resources = ClientResources::new(&client.0.join("Bin")).unwrap();
    assert_eq!(
        Catalogue::load(&mut resources).unwrap().to_json(),
        fixture()["expected"]
    );
    assert_eq!(std::fs::read(&path).unwrap(), data);
    assert_eq!(
        resources.read("absent.vfs", HERO_PATH).unwrap_err().code,
        "archive_read"
    );
    assert_eq!(
        resources.read("../data1.vfs", HERO_PATH).unwrap_err().code,
        "archive_read"
    );
    assert_eq!(
        resources
            .read("data1.vfs", "data/../file")
            .unwrap_err()
            .code,
        "content_mismatch"
    );
    let overlay = client.0.join(HERO_PATH);
    std::fs::create_dir_all(overlay.parent().unwrap()).unwrap();
    std::fs::write(overlay, b"overlay").unwrap();
    assert_eq!(
        Catalogue::load(&mut resources).unwrap_err().code,
        "unsupported_overlay"
    );
    drop(resources);
    assert_eq!(std::fs::read(&path).unwrap(), data);
}
