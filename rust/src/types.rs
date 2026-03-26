/// Shared type definitions used across modules.
///
/// Sentinel node names matching the Python `core/graph.py` constants.
pub const START: &str = "__start__";
pub const END: &str = "__end__";

/// Edge type variants — mirrors Python `EdgeType` enum.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EdgeKind {
    Fixed,
    Conditional,
}

/// Internal representation of a graph edge.
#[derive(Debug, Clone)]
pub struct EdgeData {
    pub source: String,
    /// `None` for conditional edges (target is resolved at runtime via the router).
    pub target: Option<String>,
    pub kind: EdgeKind,
    /// For conditional edges: maps router return values to node names.
    pub route_map: Option<std::collections::HashMap<String, String>>,
}
