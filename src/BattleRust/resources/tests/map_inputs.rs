use serde_json::Value;
use std::io::Cursor;
use t7r_battle_resources::vfs::{Archive, ClientResources};
use t7r_battle_resources::{bot_names, navmesh, spawn};

fn hex(value: &str) -> Vec<u8> {
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
        .collect()
}

fn fixture() -> Value {
    serde_json::from_str(include_str!(
        "../../../../tests/fixtures/resources/map-input-v1.json"
    ))
    .unwrap()
}

fn entry(archive: &str, path: &str) -> Vec<u8> {
    let fixture = fixture();
    Archive::from_reader(
        Cursor::new(hex(fixture[archive].as_str().unwrap())),
        "fixture.vfs",
    )
    .unwrap()
    .read_entry(path)
    .unwrap()
}

#[test]
fn original_map_and_names_match_python_export_vectors() {
    let expected = fixture();
    let names = bot_names::parse_names(&entry("namesArchiveHex", bot_names::NAME_PATH)).unwrap();
    assert_eq!(serde_json::json!(names), expected["names"]);
    bot_names::validate_roster(&names, "测试电脑00").unwrap();
    let data = entry("mapArchiveHex", spawn::SPAWN_PATH);
    assert_eq!(
        spawn::parse_spawns(&data).unwrap().to_json(),
        expected["spawns"]
    );
    assert_eq!(
        spawn::parse_respawns(&data).unwrap().to_json(),
        expected["respawns"]
    );
    assert_eq!(
        navmesh::parse_navigation(&entry("mapArchiveHex", navmesh::NAV_PATH))
            .unwrap()
            .data(),
        &expected["navigation"]
    );
}

#[test]
fn npc_table_preserves_order_trims_names_and_bounds_the_roster() {
    let mut data = entry("namesArchiveHex", bot_names::NAME_PATH);
    let names = bot_names::parse_names(&data).unwrap();
    assert_eq!(names.len(), 30);
    assert!(bot_names::validate_roster(&names[..19], "").is_ok());
    assert!(bot_names::validate_roster(&names[..19], "测试电脑00").is_err());
    assert!(bot_names::validate_roster(&names[..19], "[进攻]测试电脑00").is_err());
    assert!(bot_names::validate_roster(&names[..18], "").is_err());
    let mut one = data[..208].to_vec();
    one[12..16].copy_from_slice(&1u32.to_le_bytes());
    one[176..208].fill(0);
    one[176..183].copy_from_slice(b"\x1c name\t");
    assert_eq!(bot_names::parse_names(&one).unwrap(), ["name"]);
    one[176..208].fill(b'A');
    assert!(bot_names::parse_names(&one).is_err());
    one[207] = 0;
    assert_eq!(bot_names::parse_names(&one).unwrap(), ["A".repeat(31)]);
    let mut long_names = names[..18].to_vec();
    long_names.push("A".repeat(26));
    assert!(bot_names::validate_roster(&long_names, "").is_err());
    for byte in [0, 1, 0x7f, 0x80, 0xff] {
        let mut broken = one.clone();
        broken[180] = byte;
        if byte == 0 {
            broken[176] = 0;
        }
        assert!(bot_names::parse_names(&broken).is_err());
    }
    one[144..176].fill(b'X');
    assert!(
        bot_names::parse_names(&one).is_err(),
        "non-NPC rows do not supply fallback names"
    );
    for offset in [0, 4, 8, 12, 64, 132] {
        let mut broken = data.clone();
        broken[offset] ^= 1;
        assert!(bot_names::parse_names(&broken).is_err());
    }
    data.push(0);
    assert!(bot_names::parse_names(&data).is_err());
}

#[test]
fn spawn_xml_rejects_bad_flags_counts_vectors_duplicates_and_markup() {
    let data = entry("mapArchiveHex", spawn::SPAWN_PATH);
    let text = String::from_utf8(data).unwrap();
    for (before, after) in [
        ("SH_RES_ACTOR_SPAWN_GROUP_TAB", "WrongRoot"),
        ("is_enable value=\"1\"", "is_enable value=\"2\""),
        ("is_red value=\"0\"", "is_red value=\"1\""),
        ("point_num value=\"10\"", "point_num value=\"9\""),
        ("dir value=\"0,1,0\"", "dir value=\"0,0,0\""),
        ("pos value=\"0,20,0.2\"", "pos value=\"nan,20,0.2\""),
        ("pos value=\"0,20,0.2\"", "pos value=\"100000,20,0.2\""),
        ("pos value=\"0,20,0.2\"", "pos value=\"0,20\""),
        ("pos value=\"1,20,0.2\"", "pos value=\"0,20,0.2\""),
        ("value=\"10\"", "value=\"&unknown;\""),
    ] {
        assert!(text.contains(before), "missing mutation anchor {before}");
        assert!(
            spawn::parse_spawns(text.replace(before, after).as_bytes()).is_err(),
            "mutation {after}"
        );
    }
    assert!(spawn::parse_spawns(&text.as_bytes()[..text.len() - 2]).is_err());
    assert!(spawn::parse_spawns(format!("{text}<extra/>").as_bytes()).is_err());
    assert!(
        spawn::parse_spawns(
            text.replace("is_enable value=\"1\"", "is_enable value=\"1\" value=\"1\"")
                .as_bytes()
        )
        .is_err()
    );
    let escaped = text.replace("point_num value=\"10\"", "point_num value=\"&#49;0\"");
    assert_eq!(
        spawn::parse_spawns(escaped.as_bytes()).unwrap(),
        spawn::parse_spawns(text.as_bytes()).unwrap()
    );
    let directions = [
        ("1,0,0", 0),
        ("-1,0,0", -180),
        ("1,-1,0", 45),
        ("1,1,0", -45),
    ];
    for (direction, heading) in directions {
        let changed = text.replace("dir value=\"0,1,0\"", &format!("dir value=\"{direction}\""));
        assert_eq!(
            spawn::parse_spawns(changed.as_bytes()).unwrap().camps()[&1][0].1,
            heading
        );
    }
}

#[test]
fn navigation_matches_original_topology_acceptance_including_disconnected_seams() {
    let cases: Value = serde_json::from_str(include_str!(
        "../../../../tests/fixtures/resources/navigation-input-v1.json"
    ))
    .unwrap();
    for case in cases.as_array().unwrap() {
        let result = navmesh::parse_navigation(&hex(case["dataHex"].as_str().unwrap()));
        if case["expected"].is_null() {
            assert!(result.is_err(), "{}", case["name"]);
        } else {
            assert_eq!(
                result.unwrap().data(),
                &case["expected"],
                "{}",
                case["name"]
            );
        }
    }
}

#[test]
fn tagfile_checks_headers_bounds_and_every_truncated_prefix() {
    let data = entry("mapArchiveHex", navmesh::NAV_PATH);
    for length in 0..data.len() {
        assert!(
            navmesh::TagReader::new(&data[..length])
                .unwrap()
                .document()
                .is_err(),
            "prefix={length}"
        );
    }
    let mut trailing = data.clone();
    trailing.push(0);
    assert!(navmesh::parse_navigation(&trailing).is_err());
    for offset in [0, 8, 9] {
        let mut changed = data.clone();
        changed[offset] ^= 1;
        assert!(navmesh::parse_navigation(&changed).is_err());
    }
    assert!(navmesh::TagReader::new(&vec![0; 16 * 1024 * 1024 + 1]).is_err());
    for (bytes, expected) in [
        (vec![0], 0),
        (vec![2], 1),
        (vec![3], -1),
        (vec![0xff, 0xff, 0xff, 0xff, 0x1f], -4294967295),
    ] {
        assert_eq!(
            navmesh::TagReader::new(&bytes).unwrap().integer().unwrap(),
            expected
        );
    }
    for bytes in [vec![], vec![0x80], vec![0x80; 10], vec![0xff; 5]] {
        assert!(navmesh::TagReader::new(&bytes).unwrap().integer().is_err());
    }
}

#[test]
fn selected_layout_checks_overlay_files_without_requiring_runtime_map_reads() {
    let root = std::env::temp_dir().join(format!("t7r-map-resources-{}", std::process::id()));
    std::fs::create_dir(&root).unwrap();
    std::fs::create_dir(root.join("Bin")).unwrap();
    std::fs::create_dir(root.join("vfs")).unwrap();
    let expected = fixture();
    for (name, key) in [
        ("data1.vfs", "namesArchiveHex"),
        ("map.vfs", "mapArchiveHex"),
    ] {
        std::fs::write(
            root.join("vfs").join(name),
            hex(expected[key].as_str().unwrap()),
        )
        .unwrap();
    }
    let mut resources = ClientResources::new(&root.join("Bin")).unwrap();
    resources.check_layout().unwrap();
    assert_eq!(
        serde_json::json!(bot_names::load_names(&mut resources).unwrap()),
        expected["names"]
    );
    let (spawns, respawns) = spawn::read_placements(&mut resources).unwrap();
    assert_eq!(spawns.to_json(), expected["spawns"]);
    assert_eq!(respawns.to_json(), expected["respawns"]);
    assert_eq!(
        navmesh::read_navigation(&mut resources).unwrap_err().code,
        "content_mismatch"
    );
    for name in ["update.vfs", "RemovedREMOVE.TXT"] {
        let path = root.join("vfs").join(name);
        std::fs::write(&path, b"overlay").unwrap();
        assert_eq!(
            resources.check_layout().unwrap_err().code,
            "unsupported_overlay"
        );
        std::fs::remove_file(path).unwrap();
    }
    for path in [
        "data/config/vfsconfig.cfg",
        bot_names::NAME_PATH,
        spawn::SPAWN_PATH,
        navmesh::NAV_PATH,
    ] {
        let path = root.join(path);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, b"overlay").unwrap();
        assert_eq!(
            resources.check_layout().unwrap_err().code,
            "unsupported_overlay"
        );
        std::fs::remove_file(path).unwrap();
    }
    resources.check_layout().unwrap();
    drop(resources);
    for (name, key) in [
        ("data1.vfs", "namesArchiveHex"),
        ("map.vfs", "mapArchiveHex"),
    ] {
        assert_eq!(
            std::fs::read(root.join("vfs").join(name)).unwrap(),
            hex(expected[key].as_str().unwrap())
        );
    }
    std::fs::remove_dir_all(root).unwrap();
}
