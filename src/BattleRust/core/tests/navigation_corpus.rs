use serde_json::{Value, json};
use t7r_battle_core::navigation::{
    NavLink, NavMesh, NavigationEdge, NavigationFace, NavigationResource, Point, SpawnCandidates,
    SpawnReference, SpawnSelection,
};

fn point(value: &Value) -> Point {
    std::array::from_fn(|i| value[i].as_f64().unwrap())
}

fn mesh(case: &Value) -> NavMesh {
    if let Some(resource) = case.get("resource") {
        return NavMesh::from_resource(&NavigationResource {
            vertices: resource["vertices"]
                .as_array()
                .unwrap()
                .iter()
                .map(point)
                .collect(),
            edges: resource["edges"]
                .as_array()
                .unwrap()
                .iter()
                .map(|edge| NavigationEdge {
                    a: edge["a"].as_i64().unwrap() as i32,
                    b: edge["b"].as_i64().unwrap() as i32,
                    opposite_edge: edge["oppositeEdge"].as_i64().unwrap() as i32,
                    opposite_face: edge["oppositeFace"].as_i64().unwrap() as i32,
                })
                .collect(),
            faces: resource["faces"]
                .as_array()
                .unwrap()
                .iter()
                .map(|face| NavigationFace {
                    start_edge_index: face["startEdgeIndex"].as_i64().unwrap() as i32,
                    num_edges: face["numEdges"].as_i64().unwrap() as i32,
                    num_user_edges: face["numUserEdges"].as_i64().unwrap() as i32,
                })
                .collect(),
            streaming_sets: resource["streamingSets"].as_array().unwrap().len(),
        })
        .unwrap();
    }
    let polygons = case["polygons"]
        .as_array()
        .unwrap()
        .iter()
        .map(|polygon| polygon.as_array().unwrap().iter().map(point).collect())
        .collect();
    if let Some(links) = case.get("links") {
        NavMesh::new(
            polygons,
            links
                .as_array()
                .unwrap()
                .iter()
                .map(|row| {
                    row.as_array()
                        .unwrap()
                        .iter()
                        .map(|link| NavLink {
                            neighbor: link[0].as_u64().unwrap() as usize,
                            a: point(&link[1]),
                            b: point(&link[2]),
                        })
                        .collect()
                })
                .collect(),
        )
        .unwrap()
    } else {
        NavMesh::from_polygons(polygons).unwrap()
    }
}

fn assert_json_near(actual: &Value, expected: &Value, location: &str) {
    match (actual, expected) {
        (Value::Number(a), Value::Number(b)) => assert!(
            (a.as_f64().unwrap() - b.as_f64().unwrap()).abs() <= 1e-9,
            "{location}: {actual} != {expected}"
        ),
        (Value::Array(a), Value::Array(b)) => {
            assert_eq!(a.len(), b.len(), "{location}");
            for (i, (a, b)) in a.iter().zip(b).enumerate() {
                assert_json_near(a, b, &format!("{location}/{i}"));
            }
        }
        (Value::Object(a), Value::Object(b)) => {
            assert_eq!(a.len(), b.len(), "{location}");
            for (key, a) in a {
                assert_json_near(a, &b[key], &format!("{location}/{key}"));
            }
        }
        _ => assert_eq!(actual, expected, "{location}"),
    }
}

#[test]
fn frozen_python_paths_and_spawns_match() {
    let corpus: Value = serde_json::from_str(include_str!(
        "../../../../tests/fixtures/navigation/navigation-v1.json"
    ))
    .unwrap();
    assert_eq!(corpus["version"], 1);
    for case in corpus["cases"].as_array().unwrap() {
        let nav = mesh(case);
        let mut result = json!({"locate": [], "project": [], "paths": []});
        for location in case["locate"].as_array().into_iter().flatten() {
            result["locate"]
                .as_array_mut()
                .unwrap()
                .push(json!(nav.locate(point(location))));
            result["project"]
                .as_array_mut()
                .unwrap()
                .push(json!(nav.project(point(location))));
        }
        for query in case["paths"].as_array().into_iter().flatten() {
            let path = nav
                .find_path(
                    point(&query["start"]),
                    point(&query["target"]),
                    query["radius"].as_f64().unwrap_or(0.35),
                )
                .unwrap();
            result["paths"].as_array_mut().unwrap().push(
                json!({"status": path.status.as_str(), "faces": path.faces, "points": path.points}),
            );
        }
        if let Some(spawn) = case.get("spawn") {
            let references: Vec<_> = spawn["references"]
                .as_array()
                .unwrap()
                .iter()
                .map(|entry| SpawnReference {
                    position: point(&entry[0]),
                    heading: entry[1].as_f64().unwrap(),
                })
                .collect();
            let candidates =
                SpawnCandidates::prewarm(&nav, &references, point(&spawn["anchor"])).unwrap();
            result["spawn"] = json!(
                candidates
                    .groups()
                    .iter()
                    .map(|group| group
                        .iter()
                        .map(|p| (p.position, p.heading))
                        .collect::<Vec<_>>())
                    .collect::<Vec<_>>()
            );
            assert_eq!(
                candidates,
                SpawnCandidates::prewarm(&nav, &references, point(&spawn["anchor"])).unwrap()
            );
            if let Some(queries) = case.get("choose") {
                result["choose"] = json!(queries.as_array().unwrap().iter().map(|query| {
                    let neighbors: Vec<_> = query["neighbors"].as_array().unwrap().iter().map(point).collect();
                    match candidates.choose(query["actorId"].as_u64().unwrap(), query["diedAt"].as_u64().unwrap(), &neighbors).unwrap() {
                        SpawnSelection::Ready { reference, spawn } => json!({"status":"ready", "reference":reference, "spawn":(spawn.position, spawn.heading)}),
                        SpawnSelection::NoCandidates => json!({"status":"respawn-no-battlefield-point"}),
                        SpawnSelection::Occupied => json!({"status":"respawn-occupied"}),
                    }
                }).collect::<Vec<_>>());
            }
        }
        assert_json_near(&result, &case["expected"], case["name"].as_str().unwrap());
    }
}
