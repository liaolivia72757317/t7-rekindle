use t7r_battle_core::navigation::{
    NavLink, NavMesh, NavigationEdge, NavigationFace, NavigationResource, PathStatus, Point,
    SpawnCandidates, SpawnReference, SpawnSelection,
};

fn square() -> Vec<Point> {
    vec![
        [0.0, 0.0, 0.0],
        [4.0, 0.0, 0.0],
        [4.0, 4.0, 0.0],
        [0.0, 4.0, 0.0],
    ]
}

#[test]
fn original_navigation_coordinates_survive_json_without_ulp_drift() {
    let values: Vec<f64> = serde_json::from_str("[262.6947937011719,227.8170928955078,0.061538487672805786,260.26422119140625,229.37750244140625,0.09409894794225693,260.369384765625,229.41867065429688,0.08591124415397644]").unwrap();
    let expected = [
        0x40706b1de0000000,
        0x406c7a25a0000000,
        0x3faf81f900000000,
        0x4070443a40000000,
        0x406cac1480000000,
        0x3fb816de60000000,
        0x407045e900000000,
        0x406cad65c0000000,
        0x3fb5fe4780000000,
    ];
    assert_eq!(
        values.iter().map(|v| v.to_bits()).collect::<Vec<_>>(),
        expected
    );
    let repeated: Vec<f64> = serde_json::from_slice(&serde_json::to_vec(&values).unwrap()).unwrap();
    assert_eq!(
        repeated.iter().map(|v| v.to_bits()).collect::<Vec<_>>(),
        expected
    );
}

fn resource() -> NavigationResource {
    let mut vertices = square();
    vertices.extend(square().iter().map(|p| [p[0] + 4.0, p[1], p[2]]));
    let mut edges = Vec::new();
    for face in 0..2 {
        for edge in 0..4 {
            edges.push(NavigationEdge {
                a: face * 4 + edge,
                b: face * 4 + (edge + 1) % 4,
                opposite_edge: -1,
                opposite_face: -1,
            });
        }
    }
    edges[1].opposite_edge = 7;
    edges[1].opposite_face = 1;
    edges[7].opposite_edge = 1;
    edges[7].opposite_face = 0;
    NavigationResource {
        vertices,
        edges,
        faces: vec![
            NavigationFace {
                start_edge_index: 0,
                num_edges: 4,
                num_user_edges: 0,
            },
            NavigationFace {
                start_edge_index: 4,
                num_edges: 4,
                num_user_edges: 0,
            },
        ],
        streaming_sets: 0,
    }
}

#[test]
fn resource_topology_is_validated_before_navigation() {
    let source = resource();
    assert!(NavMesh::from_resource(&source).is_ok());
    let changes: Vec<fn(&mut NavigationResource)> = vec![
        |r| r.vertices.clear(),
        |r| r.edges.clear(),
        |r| r.faces.clear(),
        |r| r.streaming_sets = 1,
        |r| r.faces[0].start_edge_index = -1,
        |r| r.faces[0].num_edges = -1,
        |r| r.faces[0].num_edges = 2,
        |r| r.faces[0].num_edges = i32::MAX,
        |r| r.faces[0].num_user_edges = 1,
        |r| r.faces[1].start_edge_index = 0,
        |r| r.edges.push(r.edges[0]),
        |r| r.edges[0].a = -1,
        |r| r.edges[0].b = 99,
        |r| r.edges[0].b = 2,
        |r| r.edges[1].opposite_face = -1,
        |r| r.edges[1].opposite_edge = 99,
        |r| r.edges[1].opposite_face = 99,
        |r| r.edges[1].opposite_edge = 6,
        |r| r.edges[7].opposite_face = 1,
        |r| r.edges[7].opposite_edge = 2,
        |r| r.vertices[0][2] = f64::NAN,
    ];
    for (index, change) in changes.into_iter().enumerate() {
        let mut resource = source.clone();
        change(&mut resource);
        assert!(NavMesh::from_resource(&resource).is_err(), "case {index}");
    }
}

#[test]
fn invalid_polygons_links_and_extents_return_errors() {
    assert!(NavMesh::from_polygons(vec![vec![[0.0; 3]; 2]]).is_err());
    assert!(NavMesh::from_polygons(vec![vec![[0.0; 3]; 129]]).is_err());
    for invalid in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        let mut polygon = square();
        polygon[0][0] = invalid;
        assert!(NavMesh::from_polygons(vec![polygon]).is_err());
    }
    assert!(NavMesh::new(vec![square()], Vec::new()).is_err());
    assert!(
        NavMesh::new(
            vec![square()],
            vec![vec![NavLink {
                neighbor: 1,
                a: [0.0; 3],
                b: [1.0; 3]
            }]]
        )
        .is_err()
    );
    assert!(
        NavMesh::new(
            vec![square()],
            vec![vec![NavLink {
                neighbor: 0,
                a: [f64::NAN; 3],
                b: [1.0; 3]
            }]]
        )
        .is_err()
    );
    let huge: Vec<_> = square()
        .iter()
        .map(|p| [p[0] * 1e10, p[1] * 1e10, 0.0])
        .collect();
    assert!(NavMesh::from_polygons(vec![huge]).is_err());
    let extreme: Vec<_> = square().iter().map(|p| [p[0], p[1], f64::MAX]).collect();
    assert!(NavMesh::from_polygons(vec![extreme]).is_err());
}

#[test]
fn query_validation_preserves_outside_mesh_before_radius_validation() {
    let nav = NavMesh::from_polygons(vec![square()]).unwrap();
    for invalid in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        assert_eq!(nav.locate([invalid, 1.0, 0.0]), None);
        assert_eq!(nav.project([1.0, 1.0, invalid]), None);
    }
    for radius in [-1.0, f64::NAN, f64::INFINITY] {
        assert!(
            nav.find_path([1.0, 1.0, 0.0], [2.0, 2.0, 0.0], radius)
                .is_err()
        );
        assert_eq!(
            nav.find_path([100.0, 1.0, 0.0], [2.0, 2.0, 0.0], radius)
                .unwrap()
                .status,
            PathStatus::OutsideMesh
        );
    }
}

#[test]
fn spawn_preflight_and_selection_validate_inputs_without_fabricating_candidates() {
    let nav = NavMesh::from_polygons(vec![square()]).unwrap();
    let reference = SpawnReference {
        position: [1.0, 1.0, 0.2],
        heading: 90.0,
    };
    let prepared = SpawnCandidates::prewarm(&nav, &[reference], [2.0, 2.0, 0.2]).unwrap();
    assert!(!prepared.groups()[0].is_empty());
    assert!(prepared.choose(2, 0, &[[f64::NAN; 3]]).is_err());
    assert!(
        SpawnCandidates::prewarm(
            &nav,
            &[SpawnReference {
                heading: f64::INFINITY,
                ..reference
            }],
            [2.0, 2.0, 0.2]
        )
        .is_err()
    );
    assert!(SpawnCandidates::prewarm(&nav, &[reference], [f64::NAN; 3]).is_err());
    assert!(
        SpawnCandidates::prewarm(
            &nav,
            &[SpawnReference {
                position: [f64::NAN; 3],
                ..reference
            }],
            [2.0, 2.0, 0.2]
        )
        .is_err()
    );
    let empty = SpawnCandidates::prewarm(&nav, &[], [2.0, 2.0, 0.2]).unwrap();
    assert_eq!(
        empty.choose(u64::MAX, u64::MAX, &[]).unwrap(),
        SpawnSelection::NoCandidates
    );
}
