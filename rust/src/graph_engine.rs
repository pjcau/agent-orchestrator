/// Port of `core/graph.py` topology logic (StateGraph._validate, _find_reachable,
/// _get_next_nodes_fixed, and conditional edge resolution).
///
/// Only the structural / topology operations are ported here; the actual async
/// node execution remains in Python because it requires calling Python callables.
use std::collections::{HashMap, HashSet, VecDeque};

use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::types::{EdgeData, EdgeKind, END, START};

/// Topology of a StateGraph — nodes and edges without Python callables.
///
/// This struct mirrors the structural data held by `StateGraph` in Python and
/// exposes methods needed for validation and graph traversal.
#[pyclass]
pub struct GraphTopology {
    nodes: HashSet<String>,
    edges: Vec<EdgeData>,
}

#[pymethods]
impl GraphTopology {
    /// Create an empty topology.
    #[new]
    pub fn new() -> Self {
        GraphTopology {
            nodes: HashSet::new(),
            edges: Vec::new(),
        }
    }

    /// Register a user-defined node.
    ///
    /// Raises `ValueError` if the name is the reserved sentinel `__start__` /
    /// `__end__`, or if a node with that name was already added.
    pub fn add_node(&mut self, name: String) -> PyResult<()> {
        if name == START || name == END {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "Cannot use reserved name: {name}"
            )));
        }
        if self.nodes.contains(&name) {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "Node already exists: {name}"
            )));
        }
        self.nodes.insert(name);
        Ok(())
    }

    /// Add a fixed (unconditional) edge between two nodes.
    pub fn add_edge(&mut self, source: String, target: String) -> PyResult<()> {
        self.edges.push(EdgeData {
            source,
            target: Some(target),
            kind: EdgeKind::Fixed,
            route_map: None,
        });
        Ok(())
    }

    /// Add a conditional edge.
    ///
    /// `route_map` maps router-return-values to node names.  When `None` the
    /// router is expected to return node names directly (not via a map).
    pub fn add_conditional_edge(
        &mut self,
        source: String,
        route_map: Option<HashMap<String, String>>,
    ) -> PyResult<()> {
        self.edges.push(EdgeData {
            source,
            target: None,
            kind: EdgeKind::Conditional,
            route_map,
        });
        Ok(())
    }

    /// Validate graph structure.
    ///
    /// Replicates `StateGraph._validate()`:
    /// 1. START must have at least one outgoing edge.
    /// 2. Every edge source/target must be a valid node name (or a sentinel).
    /// 3. For conditional edges with a `route_map`, every target in the map
    ///    must be a valid node name.
    /// 4. Every user-defined node must be reachable from START.
    pub fn validate(&self) -> PyResult<()> {
        // Rule 1 — START must have at least one outgoing edge.
        let has_start_edge = self.edges.iter().any(|e| e.source == START);
        if !has_start_edge {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "Graph must have at least one edge from START",
            ));
        }

        // Rule 2 — all sources and fixed targets must be valid.
        let valid_names: HashSet<&str> = self
            .nodes
            .iter()
            .map(|s| s.as_str())
            .chain([START, END])
            .collect();

        for edge in &self.edges {
            if !valid_names.contains(edge.source.as_str()) {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "Edge source not found: {}",
                    edge.source
                )));
            }
            if edge.kind == EdgeKind::Fixed {
                if let Some(target) = &edge.target {
                    if !valid_names.contains(target.as_str()) {
                        return Err(pyo3::exceptions::PyValueError::new_err(format!(
                            "Edge target not found: {target}"
                        )));
                    }
                }
            }
            // Rule 3 — conditional edge route_map targets.
            if edge.kind == EdgeKind::Conditional {
                if let Some(rm) = &edge.route_map {
                    for target in rm.values() {
                        if !valid_names.contains(target.as_str()) {
                            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                                "Route target not found: {target}"
                            )));
                        }
                    }
                }
            }
        }

        // Rule 4 — all user-defined nodes must be reachable from START.
        let reachable = self.find_reachable(START);
        let unreachable: HashSet<&String> = self.nodes.iter().filter(|n| !reachable.contains(*n)).collect();
        if !unreachable.is_empty() {
            // Sort for deterministic error messages (matches Python set repr style).
            let mut names: Vec<&String> = unreachable.into_iter().collect();
            names.sort();
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "Unreachable nodes: {:?}",
                names
            )));
        }

        Ok(())
    }

    /// BFS from `start`, returning all reachable user-defined nodes.
    ///
    /// Replicates `StateGraph._find_reachable()`:
    /// - Follows fixed edges directly.
    /// - For conditional edges with a `route_map`, all map values are enqueued.
    /// - END is never entered or returned.
    /// - The start sentinel itself is excluded from the result.
    pub fn find_reachable(&self, start: &str) -> HashSet<String> {
        let mut visited: HashSet<String> = HashSet::new();
        let mut queue: VecDeque<String> = VecDeque::new();
        queue.push_back(start.to_string());

        while let Some(current) = queue.pop_front() {
            if visited.contains(&current) || current == END {
                continue;
            }
            visited.insert(current.clone());

            for edge in &self.edges {
                if edge.source != current {
                    continue;
                }
                match edge.kind {
                    EdgeKind::Fixed => {
                        if let Some(target) = &edge.target {
                            queue.push_back(target.clone());
                        }
                    }
                    EdgeKind::Conditional => {
                        if let Some(rm) = &edge.route_map {
                            for target in rm.values() {
                                queue.push_back(target.clone());
                            }
                        }
                    }
                }
            }
        }

        // Exclude the start sentinel itself.
        visited.remove(start);
        visited
    }

    /// Return the targets of all *fixed* edges outgoing from `current`.
    ///
    /// Used when the caller wants to enumerate statically-known next nodes
    /// without invoking Python router callables.
    pub fn get_next_nodes_fixed(&self, current: &str) -> Vec<String> {
        self.edges
            .iter()
            .filter(|e| e.source == current && e.kind == EdgeKind::Fixed)
            .filter_map(|e| e.target.clone())
            .collect()
    }

    /// Resolve conditional edges given pre-computed router results.
    ///
    /// The Python `_get_next_nodes()` calls the Python router callable and
    /// then resolves the result through `route_map`.  Since Rust cannot call
    /// Python callables synchronously here, the caller (Python side) must
    /// provide the raw router return values in `route_results`.
    ///
    /// For each result key: if the edge has a `route_map` and the key is in
    /// it, the mapped node name is used; otherwise the key itself is used
    /// directly (provided it is a known node or END).
    pub fn resolve_conditional(
        &self,
        current: &str,
        route_results: Vec<String>,
    ) -> Vec<String> {
        let mut resolved: Vec<String> = Vec::new();

        for edge in &self.edges {
            if edge.source != current || edge.kind != EdgeKind::Conditional {
                continue;
            }
            for key in &route_results {
                if let Some(rm) = &edge.route_map {
                    if let Some(target) = rm.get(key) {
                        resolved.push(target.clone());
                        continue;
                    }
                }
                // No route_map or key not in map — use key directly if valid.
                if self.nodes.contains(key) || key == END {
                    resolved.push(key.clone());
                }
            }
        }

        resolved
    }

    /// Return graph structure as a Python dict for visualization/debugging.
    ///
    /// Replicates `CompiledGraph.get_graph_info()`.
    pub fn get_graph_info(&self, py: Python<'_>) -> PyResult<PyObject> {
        let dict = PyDict::new_bound(py);

        let nodes_list: Vec<&String> = self.nodes.iter().collect();
        dict.set_item("nodes", nodes_list)?;

        let edges_list: Vec<PyObject> = self
            .edges
            .iter()
            .map(|e| {
                let edge_dict = PyDict::new_bound(py);
                edge_dict.set_item("source", &e.source).unwrap();
                edge_dict
                    .set_item("target", e.target.as_deref())
                    .unwrap();
                edge_dict
                    .set_item(
                        "type",
                        match e.kind {
                            EdgeKind::Fixed => "fixed",
                            EdgeKind::Conditional => "conditional",
                        },
                    )
                    .unwrap();
                let routes: Option<Vec<&String>> =
                    e.route_map.as_ref().map(|rm| rm.keys().collect());
                edge_dict.set_item("routes", routes).unwrap();
                edge_dict.into()
            })
            .collect();
        dict.set_item("edges", edges_list)?;

        Ok(dict.into())
    }
}

impl Default for GraphTopology {
    fn default() -> Self {
        Self::new()
    }
}
