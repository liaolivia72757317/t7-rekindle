use t7r_battle_resources::{ResourceBundle, match_maps};

#[test]
fn all_twelve_maps_have_independent_valid_navigation_and_placements() {
    let original = ResourceBundle::from_slice(include_bytes!(
        "../../../../tests/fixtures/resources/bundle-v1.json"
    ))
    .unwrap();
    assert_eq!(match_maps::MAP_IDS.len(), 12);
    let counts = [
        (19, 18),
        (10, 10),
        (24, 24),
        (14, 18),
        (20, 17),
        (15, 17),
        (16, 13),
        (18, 17),
        (16, 15),
        (5, 5),
        (10, 10),
        (30, 30),
    ];
    for (id, (blue, red)) in match_maps::MAP_IDS.into_iter().zip(counts) {
        let selected = match_maps::select(&original, id).unwrap();
        assert_eq!(selected.map_id(), id);
        for (camp, count) in [("1", blue), ("2", red)] {
            assert_eq!(
                selected.data()["map"]["spawns"][camp]
                    .as_array()
                    .unwrap()
                    .len(),
                count
            );
            assert!(
                !selected.data()["map"]["respawns"][camp]
                    .as_array()
                    .unwrap()
                    .is_empty()
            );
        }
        assert_eq!(selected.data()["rules"], original.data()["rules"]);
    }
    assert_eq!(original.map_id(), 1028);
    assert!(match_maps::select(&original, 0).is_err());
    assert!(match_maps::select(&original, 9999).is_err());
}
