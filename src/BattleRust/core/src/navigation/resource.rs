use super::{NavLink, NavMesh, NavigationError, Point, geometry::distance_xy};

#[derive(Clone, Debug)]
pub struct NavigationResource {
    pub vertices: Vec<Point>,
    pub edges: Vec<NavigationEdge>,
    pub faces: Vec<NavigationFace>,
    pub streaming_sets: usize,
}

#[derive(Clone, Copy, Debug)]
pub struct NavigationEdge {
    pub a: i32,
    pub b: i32,
    pub opposite_edge: i32,
    pub opposite_face: i32,
}

#[derive(Clone, Copy, Debug)]
pub struct NavigationFace {
    pub start_edge_index: i32,
    pub num_edges: i32,
    pub num_user_edges: i32,
}

impl NavMesh {
    pub fn from_resource(resource: &NavigationResource) -> Result<Self, NavigationError> {
        let NavigationResource {
            vertices,
            edges,
            faces,
            streaming_sets,
        } = resource;
        if vertices.is_empty() || edges.is_empty() || faces.is_empty() || *streaming_sets != 0 {
            return Err(NavigationError(
                "navigation requires a single nonempty mesh",
            ));
        }
        let mut polygons = Vec::with_capacity(faces.len());
        let mut links = vec![Vec::new(); faces.len()];
        let mut owners = vec![None; edges.len()];
        for (index, face) in faces.iter().enumerate() {
            let start = usize::try_from(face.start_edge_index)
                .map_err(|_| NavigationError("invalid navigation face edge range"))?;
            let count = usize::try_from(face.num_edges)
                .map_err(|_| NavigationError("invalid navigation face edge range"))?;
            if count < 3
                || start.checked_add(count).is_none_or(|end| end > edges.len())
                || face.num_user_edges != 0
            {
                return Err(NavigationError("invalid navigation face edge range"));
            }
            let mut polygon = Vec::with_capacity(count);
            for ordinal in start..start + count {
                if owners[ordinal].replace(index).is_some() {
                    return Err(NavigationError("navigation edge has multiple owners"));
                }
                let edge = edges[ordinal];
                if edge.a < 0
                    || edge.b < 0
                    || edge.a as usize >= vertices.len()
                    || edge.b as usize >= vertices.len()
                {
                    return Err(NavigationError("navigation vertex index invalid"));
                }
                if edge.b != edges[start + (ordinal - start + 1) % count].a {
                    return Err(NavigationError("navigation polygon is not closed"));
                }
                polygon.push(vertices[edge.a as usize]);
            }
            polygons.push(polygon);
        }
        if owners.iter().any(Option::is_none) {
            return Err(NavigationError("unowned navigation edges"));
        }
        for (ordinal, edge) in edges.iter().enumerate() {
            let (opposite, neighbor) = (edge.opposite_edge, edge.opposite_face);
            if opposite == -1 && neighbor == -1 {
                continue;
            }
            if opposite < 0
                || neighbor < 0
                || opposite as usize >= edges.len()
                || neighbor as usize >= faces.len()
            {
                return Err(NavigationError("navigation adjacency outside mesh"));
            }
            let reverse = edges[opposite as usize];
            if owners[opposite as usize] != Some(neighbor as usize)
                || reverse.opposite_edge != ordinal as i32
                || Some(reverse.opposite_face as usize) != owners[ordinal]
            {
                return Err(NavigationError("navigation adjacency is not reciprocal"));
            }
            let (a, b) = (vertices[edge.a as usize], vertices[edge.b as usize]);
            let (ra, rb) = (vertices[reverse.b as usize], vertices[reverse.a as usize]);
            // Small original mesh seams/steps connect; large gaps do not become walkable.
            if [(a, ra), (b, rb)]
                .iter()
                .any(|&(p, q)| distance_xy(p, q) > 0.01 || (p[2] - q[2]).abs() > 0.4)
            {
                continue;
            }
            links[owners[ordinal].expect("validated owner")].push(NavLink {
                neighbor: neighbor as usize,
                a,
                b,
            });
        }
        Self::new(polygons, links)
    }
}
