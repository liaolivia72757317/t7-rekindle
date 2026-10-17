use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use t7r_battle_resources::bot_names::NAME_PATH;
use t7r_battle_resources::catalogue::{
    CATEGORY_PATH, Catalogue, FIST_ID, GROUP_PATH, HERO_PATH, UNLOCK_PATH, WEAPON_PATH,
};
use t7r_battle_resources::frozen::FrozenData;

fn baseline() -> Value {
    serde_json::from_slice(include_bytes!("../data/battle-baseline-v1.json")).unwrap()
}

fn names() -> Vec<String> {
    (0..30).map(|i| format!("Fixture{i}")).collect()
}

fn put_u16(data: &mut [u8], offset: usize, value: u16) {
    data[offset..offset + 2].copy_from_slice(&value.to_le_bytes());
}
fn put_u32(data: &mut [u8], offset: usize, value: u32) {
    data[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
}

fn table(width: usize, rows: &[Vec<u8>]) -> Vec<u8> {
    let mut data = vec![0; 140];
    data[..4].copy_from_slice(b"MSES");
    put_u32(&mut data, 4, 7);
    put_u32(&mut data, 8, width as u32);
    put_u32(&mut data, 12, rows.len() as u32);
    data[64..68].copy_from_slice(b"GBK\0");
    put_u32(&mut data, 132, 140);
    for row in rows {
        assert_eq!(row.len(), width);
        data.extend_from_slice(row);
    }
    data
}

fn catalogue_tables(baseline: &Value, selected: &[(u32, Vec<u32>)]) -> [Vec<u8>; 5] {
    let (mut heroes, mut unlocks, mut groups) = (Vec::new(), Vec::new(), Vec::new());
    let mut owned = BTreeSet::from([FIST_ID]);
    for (hero_id, weapons) in selected {
        owned.extend(weapons);
        let mut hero = vec![0; 7286];
        put_u32(&mut hero, 0, *hero_id);
        hero[4..11].copy_from_slice(b"Fixture");
        hero[34] = 1;
        hero[163..167].copy_from_slice(b"\xcf\xc8\xb7\xe6");
        put_u16(&mut hero, 4209, 8);
        for (index, weapons) in weapons.chunks(6).enumerate() {
            let group_id = hero_id * 10 + index as u32;
            put_u32(&mut hero, 4211 + index * 12, group_id);
            put_u32(&mut hero, 4215 + index * 12, weapons[0]);
            let mut group = vec![0; 1434];
            put_u32(&mut group, 0, group_id);
            put_u16(&mut group, 64, weapons.len() as u16);
            for (index, &weapon) in weapons.iter().enumerate() {
                put_u32(&mut group, 66 + index * 228, weapon);
            }
            groups.push(group);
        }
        heroes.push(hero);
        let mut unlock = vec![0; 300];
        put_u16(&mut unlock, 36, 1);
        put_u32(&mut unlock, 42, *hero_id);
        unlocks.push(unlock);
    }
    let (mut weapons, mut categories) = (Vec::new(), BTreeMap::new());
    for weapon_id in owned {
        let key = weapon_id.to_string();
        let profile = baseline["combatProfiles"]
            .as_object()
            .unwrap()
            .values()
            .find_map(|hero| hero.get(&key));
        let (category, hand, family) = profile.map_or((32, 3, 2), |profile| {
            (
                profile["weaponCategory"].as_u64().unwrap() as u16,
                profile["hand"].as_u64().unwrap() as u16,
                if profile["shield"] == true { 12 } else { 2 },
            )
        });
        let mut weapon = vec![0; 2829];
        put_u32(&mut weapon, 0, weapon_id);
        put_u16(&mut weapon, 56, category);
        weapon[16..23].copy_from_slice(b"Fixture");
        weapons.push(weapon);
        let mut spec = vec![0; 550];
        put_u16(&mut spec, 0, category);
        put_u16(&mut spec, 2, family);
        put_u16(&mut spec, 46, hand);
        categories.insert(category, spec);
    }
    [
        table(7286, &heroes),
        table(300, &unlocks),
        table(1434, &groups),
        table(2829, &weapons),
        table(550, &categories.into_values().collect::<Vec<_>>()),
    ]
}

fn catalogue(baseline: &Value, selected: &[(u32, Vec<u32>)]) -> Catalogue {
    let [heroes, unlocks, groups, weapons, categories] = catalogue_tables(baseline, selected);
    Catalogue::parse(&heroes, &unlocks, &groups, &weapons, &categories).unwrap()
}

#[test]
fn assembly_covers_every_frozen_hero_and_weapon_without_client_combat_reads() {
    let baseline = baseline();
    let frozen = FrozenData::embedded().unwrap();
    let mut count = 0;
    for (hero, profiles) in baseline["combatProfiles"].as_object().unwrap() {
        let hero_id = hero.parse::<u32>().unwrap();
        let weapons = profiles
            .as_object()
            .unwrap()
            .keys()
            .map(|key| key.parse::<u32>().unwrap())
            .filter(|id| *id != FIST_ID)
            .collect();
        let mut selected = vec![(hero_id, weapons)];
        if hero_id != 1101 {
            selected.push((1101, vec![1030411]));
        }
        let catalogue = catalogue(&baseline, &selected);
        let result = frozen
            .assemble(&catalogue, &names(), 3, 7, "fixture-v1")
            .unwrap();
        assert_eq!(result.data()["combatProfiles"][hero], *profiles);
        assert_eq!(
            result.data()["damageProfiles"][hero],
            baseline["damageProfiles"][hero]
        );
        assert_eq!(result.data()["catalogue"], catalogue.to_json());
        assert_eq!(result.data()["rules"], baseline["rules"]);
        assert_eq!(result.data()["map"], baseline["map"]);
        assert_eq!(
            result.data()["botNames"],
            serde_json::to_value(names()).unwrap()
        );
        assert_eq!(
            (
                result.rules_version(),
                result.resource_version(),
                result.map_id()
            ),
            (3, 7, 1028)
        );
        count += profiles.as_object().unwrap().len();
    }
    assert_eq!(count, 286);
}

#[test]
fn selected_weapons_are_filtered_per_hero_and_fists_are_always_included() {
    let baseline = baseline();
    let catalogue = catalogue(&baseline, &[(1101, vec![1030411]), (2102, vec![1030121])]);
    let frozen = FrozenData::embedded().unwrap();
    let first = frozen
        .assemble(&catalogue, &names(), 1, 2, "fixture-v1")
        .unwrap();
    let second = frozen
        .assemble(&catalogue, &names(), 1, 2, "fixture-v1")
        .unwrap();
    assert_eq!(first.data(), second.data());
    for hero in ["1101", "2102"] {
        assert_eq!(
            first.data()["combatProfiles"][hero]
                .as_object()
                .unwrap()
                .len(),
            2
        );
        assert_eq!(
            first.data()["damageProfiles"][hero]["weapons"]
                .as_object()
                .unwrap()
                .len(),
            2
        );
        assert_eq!(
            first.data()["combatProfiles"][hero]["100000"],
            baseline["combatProfiles"][hero]["100000"]
        );
    }
    assert_ne!(
        first.data()["combatProfiles"]["1101"]["100000"],
        first.data()["combatProfiles"]["2102"]["100000"]
    );
}

#[test]
fn unsupported_hero_weapon_and_cross_hero_binding_fail_explicitly() {
    let baseline = baseline();
    let frozen = FrozenData::embedded().unwrap();
    for (selected, expected) in [
        (
            vec![(1101, vec![1030411]), (9999, vec![1030121])],
            "hero 9999",
        ),
        (vec![(1101, vec![999999])], "hero=1101 weapon=999999"),
        (vec![(1101, vec![1030121])], "hero=1101 weapon=1030121"),
    ] {
        let error = frozen
            .assemble(
                &catalogue(&baseline, &selected),
                &names(),
                1,
                1,
                "fixture-v1",
            )
            .unwrap_err();
        assert_eq!(error.kind(), std::io::ErrorKind::InvalidData);
        assert!(error.to_string().contains(expected), "{error}");
    }
}

#[test]
fn invalid_metadata_and_name_sets_fail_without_touching_a_client_directory() {
    let baseline = baseline();
    let catalogue = catalogue(&baseline, &[(1101, vec![1030411])]);
    let frozen = FrozenData::embedded().unwrap();
    for (rules, resources, version) in [
        (0, 1, "fixture"),
        (1, 0, "fixture"),
        (1, 1, ""),
        (1, 1, "../fixture"),
        (1, 1, "测试"),
        (1, 1, &"x".repeat(65)),
    ] {
        assert!(
            frozen
                .assemble(&catalogue, &names(), rules, resources, version)
                .is_err()
        );
        let error = frozen
            .load_client(Path::new("not-a-client"), rules, resources, version)
            .unwrap_err();
        assert_eq!(error.code, "content_mismatch");
    }
    for names in [
        Vec::new(),
        vec!["Fixture".to_owned(); 30],
        names()[..18].to_vec(),
        vec!["😀".to_owned(); 30],
    ] {
        assert!(
            frozen
                .assemble(&catalogue, &names, 1, 1, "fixture")
                .is_err()
        );
    }
    assert_eq!(
        frozen
            .load_client(Path::new("not-a-client"), 1, 1, "fixture")
            .unwrap_err()
            .code,
        "archive_read"
    );
}

fn archive(entries: &[(&str, Vec<u8>)]) -> Vec<u8> {
    let mut data = vec![0; 2048];
    data[..4].copy_from_slice(&[0x90, 0x10, 0xba, 0x22]);
    let (mut names, mut directory) = (Vec::new(), Vec::new());
    for (path, bytes) in entries {
        let path = format!("../{path}");
        let fields = [
            1,
            data.len() as u32,
            bytes.len() as u32,
            bytes.len() as u32,
            0,
            0,
            0,
            0,
            0,
            path.len() as u32,
            names.len() as u32,
        ];
        let raw: Vec<_> = fields.into_iter().flat_map(u32::to_le_bytes).collect();
        directory.extend(
            raw.iter()
                .enumerate()
                .map(|(index, byte)| ((byte << 1) | (raw[(index + 1) % 44] >> 7)) ^ 0xcc),
        );
        data.extend(bytes);
        names.extend(path.bytes().chain([0]));
    }
    data.extend(names);
    data.extend(directory);
    data
}

struct TemporaryClient(std::path::PathBuf);

impl TemporaryClient {
    fn new() -> Self {
        static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);
        let root = std::env::temp_dir().join(format!(
            "t7r-frozen-client-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        std::fs::create_dir(&root).unwrap();
        std::fs::create_dir(root.join("Bin")).unwrap();
        std::fs::create_dir(root.join("vfs")).unwrap();
        Self(root)
    }
}

impl Drop for TemporaryClient {
    fn drop(&mut self) {
        assert!(
            self.0
                .parent()
                .is_some_and(|parent| parent == std::env::temp_dir())
        );
        std::fs::remove_dir_all(&self.0).unwrap();
    }
}

#[test]
fn original_archive_to_bundle_chain_is_read_only_and_rejects_overlay() {
    let baseline = baseline();
    let selected = [(1101, vec![1030411]), (2102, vec![1030121])];
    let paths = [
        HERO_PATH,
        UNLOCK_PATH,
        GROUP_PATH,
        WEAPON_PATH,
        CATEGORY_PATH,
    ];
    let mut entries: Vec<_> = paths
        .into_iter()
        .zip(catalogue_tables(&baseline, &selected))
        .collect();
    let rows: Vec<_> = names()
        .iter()
        .map(|name| {
            let mut row = vec![0; 68];
            row[4..7].copy_from_slice(b"npc");
            row[36..36 + name.len()].copy_from_slice(name.as_bytes());
            row
        })
        .collect();
    entries.push((NAME_PATH, table(68, &rows)));
    let original = archive(&entries);
    let directory = TemporaryClient::new();
    let archive_path = directory.0.join("vfs/data1.vfs");
    std::fs::write(&archive_path, &original).unwrap();
    let frozen = FrozenData::embedded().unwrap();
    let loaded = frozen
        .load_client(&directory.0.join("Bin"), 3, 7, "fixture-v1")
        .unwrap();
    let expected = frozen
        .assemble(
            &catalogue(&baseline, &selected),
            &names(),
            3,
            7,
            "fixture-v1",
        )
        .unwrap();
    assert_eq!(loaded.data(), expected.data());
    assert_eq!(std::fs::read(&archive_path).unwrap(), original);
    assert_eq!(std::fs::read_dir(&directory.0).unwrap().count(), 2);
    assert_eq!(
        std::fs::read_dir(directory.0.join("vfs")).unwrap().count(),
        1
    );
    assert_eq!(
        std::fs::read_dir(directory.0.join("Bin")).unwrap().count(),
        0
    );
    let output = directory.0.join("export.json");
    let export = || {
        std::process::Command::new(env!("CARGO_BIN_EXE_t7r-battle-resources"))
            .arg("export")
            .arg("--client-directory")
            .arg(directory.0.join("Bin"))
            .arg("--output")
            .arg(&output)
            .args([
                "--rules-version",
                "3",
                "--resource-version",
                "7",
                "--client-version",
                "fixture-v1",
            ])
            .output()
            .unwrap()
    };
    let result = export();
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    let exported = t7r_battle_resources::ResourceBundle::load(&output).unwrap();
    assert_eq!(exported.data(), loaded.data());
    let bytes = std::fs::read(&output).unwrap();
    assert!(!export().status.success());
    assert_eq!(std::fs::read(&output).unwrap(), bytes);
    assert_eq!(std::fs::read(&archive_path).unwrap(), original);
    std::fs::write(directory.0.join("vfs/update.vfs"), b"fixture overlay").unwrap();
    let error = frozen
        .load_client(&directory.0.join("Bin"), 3, 7, "fixture-v1")
        .unwrap_err();
    assert_eq!(error.code, "unsupported_overlay");
    assert_eq!(std::fs::read(&archive_path).unwrap(), original);
}
