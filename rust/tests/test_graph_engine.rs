/// Unit tests for the GraphTopology Rust type.
///
/// Each test case mirrors the equivalent behaviour of Python's StateGraph.
use agent_orchestrator_rust::*;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn build_simple_graph() -> GraphTopology {
    let mut g = GraphTopology::new();
    g.add_node("a".to_string()).unwrap();
    g.add_node("b".to_string()).unwrap();
    g.add_edge(START.to_string(), "a".to_string()).unwrap();
    g.add_edge("a".to_string(), "b".to_string()).unwrap();
    g.add_edge("b".to_string(), END.to_string()).unwrap();
    g
}

// ---------------------------------------------------------------------------
// add_node
// ---------------------------------------------------------------------------

#[test]
fn test_add_node_basic() {
    let mut g = GraphTopology::new();
    g.add_node("foo".to_string()).expect("should add node");
}

#[test]
fn test_add_node_rejects_start_sentinel() {
    let mut g = GraphTopology::new();
    let err = g.add_node(START.to_string()).unwrap_err();
    assert!(err.to_string().contains("Cannot use reserved name"));
}

#[test]
fn test_add_node_rejects_end_sentinel() {
    let mut g = GraphTopology::new();
    let err = g.add_node(END.to_string()).unwrap_err();
    assert!(err.to_string().contains("Cannot use reserved name"));
}

#[test]
fn test_add_node_rejects_duplicate() {
    let mut g = GraphTopology::new();
    g.add_node("dup".to_string()).unwrap();
    let err = g.add_node("dup".to_string()).unwrap_err();
    assert!(err.to_string().contains("already exists"));
}

// ---------------------------------------------------------------------------
// add_edge / add_conditional_edge
// ---------------------------------------------------------------------------

#[test]
fn test_add_edge_does_not_error() {
    let mut g = GraphTopology::new();
    g.add_node("x".to_string()).unwrap();
    g.add_edge(START.to_string(), "x".to_string()).unwrap();
    g.add_edge("x".to_string(), END.to_string()).unwrap();
}

#[test]
fn test_add_conditional_edge_without_route_map() {
    let mut g = GraphTopology::new();
    g.add_node("x".to_string()).unwrap();
    g.add_edge(START.to_string(), "x".to_string()).unwrap();
    // No route_map — router returns node names directly.
    g.add_conditional_edge("x".to_string(), None).unwrap();
}

#[test]
fn test_add_conditional_edge_with_route_map() {
    let mut g = GraphTopology::new();
    g.add_node("yes".to_string()).unwrap();
    g.add_node("no".to_string()).unwrap();
    g.add_edge(START.to_string(), "yes".to_string()).unwrap();
    let mut rm = std::collections::HashMap::new();
    rm.insert("go".to_string(), "yes".to_string());
    rm.insert("stop".to_string(), END.to_string());
    g.add_conditional_edge("yes".to_string(), Some(rm)).unwrap();
}

// ---------------------------------------------------------------------------
// validate
// ---------------------------------------------------------------------------

#[test]
fn test_validate_valid_simple_graph() {
    let g = build_simple_graph();
    g.validate().expect("simple graph should be valid");
}

#[test]
fn test_validate_no_start_edge() {
    let mut g = GraphTopology::new();
    g.add_node("x".to_string()).unwrap();
    let err = g.validate().unwrap_err();
    assert!(err.to_string().contains("edge from START"));
}

#[test]
fn test_validate_invalid_edge_source() {
    let mut g = GraphTopology::new();
    g.add_node("a".to_string()).unwrap();
    // "missing" has not been added as a node.
    g.add_edge("missing".to_string(), "a".to_string()).unwrap();
    g.add_edge(START.to_string(), "a".to_string()).unwrap();
    g.add_edge("a".to_string(), END.to_string()).unwrap();
    let err = g.validate().unwrap_err();
    assert!(err.to_string().contains("Edge source not found"));
}

#[test]
fn test_validate_invalid_fixed_edge_target() {
    let mut g = GraphTopology::new();
    g.add_node("a".to_string()).unwrap();
    g.add_edge(START.to_string(), "a".to_string()).unwrap();
    g.add_edge("a".to_string(), "nonexistent".to_string()).unwrap();
    let err = g.validate().unwrap_err();
    assert!(err.to_string().contains("Edge target not found"));
}

#[test]
fn test_validate_invalid_conditional_route_map_target() {
    let mut g = GraphTopology::new();
    g.add_node("a".to_string()).unwrap();
    g.add_edge(START.to_string(), "a".to_string()).unwrap();
    let mut rm = std::collections::HashMap::new();
    rm.insert("go".to_string(), "does_not_exist".to_string());
    g.add_conditional_edge("a".to_string(), Some(rm)).unwrap();
    let err = g.validate().unwrap_err();
    assert!(err.to_string().contains("Route target not found"));
}

#[test]
fn test_validate_unreachable_node() {
    let mut g = GraphTopology::new();
    g.add_node("reachable".to_string()).unwrap();
    g.add_node("orphan".to_string()).unwrap();
    g.add_edge(START.to_string(), "reachable".to_string()).unwrap();
    g.add_edge("reachable".to_string(), END.to_string()).unwrap();
    // "orphan" has no incoming edges from START.
    let err = g.validate().unwrap_err();
    assert!(err.to_string().contains("Unreachable nodes"));
}

// ---------------------------------------------------------------------------
// find_reachable
// ---------------------------------------------------------------------------

#[test]
fn test_find_reachable_simple_chain() {
    let g = build_simple_graph();
    let reachable = g.find_reachable(START);
    assert!(reachable.contains("a"));
    assert!(reachable.contains("b"));
    // START is excluded from the result set.
    assert!(!reachable.contains(START));
    // END is never entered.
    assert!(!reachable.contains(END));
}

#[test]
fn test_find_reachable_branching_graph() {
    let mut g = GraphTopology::new();
    g.add_node("split".to_string()).unwrap();
    g.add_node("left".to_string()).unwrap();
    g.add_node("right".to_string()).unwrap();
    g.add_node("merge".to_string()).unwrap();
    g.add_edge(START.to_string(), "split".to_string()).unwrap();
    g.add_edge("split".to_string(), "left".to_string()).unwrap();
    g.add_edge("split".to_string(), "right".to_string()).unwrap();
    g.add_edge("left".to_string(), "merge".to_string()).unwrap();
    g.add_edge("right".to_string(), "merge".to_string()).unwrap();
    g.add_edge("merge".to_string(), END.to_string()).unwrap();

    let reachable = g.find_reachable(START);
    assert_eq!(reachable.len(), 4);
    assert!(reachable.contains("split"));
    assert!(reachable.contains("left"));
    assert!(reachable.contains("right"));
    assert!(reachable.contains("merge"));
}

#[test]
fn test_find_reachable_via_conditional_route_map() {
    let mut g = GraphTopology::new();
    g.add_node("router_node".to_string()).unwrap();
    g.add_node("branch_a".to_string()).unwrap();
    g.add_node("branch_b".to_string()).unwrap();
    g.add_edge(START.to_string(), "router_node".to_string()).unwrap();
    g.add_edge("branch_a".to_string(), END.to_string()).unwrap();
    g.add_edge("branch_b".to_string(), END.to_string()).unwrap();
    let mut rm = std::collections::HashMap::new();
    rm.insert("a".to_string(), "branch_a".to_string());
    rm.insert("b".to_string(), "branch_b".to_string());
    g.add_conditional_edge("router_node".to_string(), Some(rm)).unwrap();

    let reachable = g.find_reachable(START);
    assert!(reachable.contains("router_node"));
    assert!(reachable.contains("branch_a"));
    assert!(reachable.contains("branch_b"));
}

// ---------------------------------------------------------------------------
// get_next_nodes_fixed
// ---------------------------------------------------------------------------

#[test]
fn test_get_next_nodes_fixed_single() {
    let g = build_simple_graph();
    let next = g.get_next_nodes_fixed(START);
    assert_eq!(next, vec!["a"]);
}

#[test]
fn test_get_next_nodes_fixed_multiple() {
    let mut g = GraphTopology::new();
    g.add_node("x".to_string()).unwrap();
    g.add_node("y".to_string()).unwrap();
    g.add_edge(START.to_string(), "x".to_string()).unwrap();
    g.add_edge(START.to_string(), "y".to_string()).unwrap();
    g.add_edge("x".to_string(), END.to_string()).unwrap();
    g.add_edge("y".to_string(), END.to_string()).unwrap();

    let mut next = g.get_next_nodes_fixed(START);
    next.sort();
    assert_eq!(next, vec!["x", "y"]);
}

#[test]
fn test_get_next_nodes_fixed_excludes_conditional() {
    let mut g = GraphTopology::new();
    g.add_node("n".to_string()).unwrap();
    g.add_edge(START.to_string(), "n".to_string()).unwrap();
    // Add a conditional edge from "n" — should NOT appear in get_next_nodes_fixed.
    g.add_conditional_edge("n".to_string(), None).unwrap();

    // Fixed next from "n" is empty; only the START->n edge is fixed.
    let next_from_n = g.get_next_nodes_fixed("n");
    assert!(next_from_n.is_empty());
}

// ---------------------------------------------------------------------------
// resolve_conditional
// ---------------------------------------------------------------------------

#[test]
fn test_resolve_conditional_with_route_map() {
    let mut g = GraphTopology::new();
    g.add_node("yes".to_string()).unwrap();
    g.add_node("no".to_string()).unwrap();
    g.add_edge(START.to_string(), "yes".to_string()).unwrap();
    g.add_edge("yes".to_string(), END.to_string()).unwrap();
    let mut rm = std::collections::HashMap::new();
    rm.insert("go".to_string(), "yes".to_string());
    rm.insert("stop".to_string(), END.to_string());
    g.add_conditional_edge("yes".to_string(), Some(rm)).unwrap();

    let resolved = g.resolve_conditional("yes", vec!["go".to_string()]);
    assert_eq!(resolved, vec!["yes"]);

    let resolved_stop = g.resolve_conditional("yes", vec!["stop".to_string()]);
    assert_eq!(resolved_stop, vec![END]);
}

#[test]
fn test_resolve_conditional_direct_node_name() {
    let mut g = GraphTopology::new();
    g.add_node("target".to_string()).unwrap();
    g.add_edge(START.to_string(), "target".to_string()).unwrap();
    g.add_edge("target".to_string(), END.to_string()).unwrap();
    // No route_map — router returns node names directly.
    g.add_conditional_edge("target".to_string(), None).unwrap();

    let resolved = g.resolve_conditional("target", vec!["target".to_string()]);
    assert_eq!(resolved, vec!["target"]);
}

#[test]
fn test_resolve_conditional_unknown_key_ignored() {
    let mut g = GraphTopology::new();
    g.add_node("n".to_string()).unwrap();
    g.add_edge(START.to_string(), "n".to_string()).unwrap();
    g.add_edge("n".to_string(), END.to_string()).unwrap();
    let mut rm = std::collections::HashMap::new();
    rm.insert("known".to_string(), "n".to_string());
    g.add_conditional_edge("n".to_string(), Some(rm)).unwrap();

    // "unknown" is not in the route_map and not a node name.
    let resolved = g.resolve_conditional("n", vec!["unknown".to_string()]);
    assert!(resolved.is_empty());
}

// ---------------------------------------------------------------------------
// Sentinel constants
// ---------------------------------------------------------------------------

#[test]
fn test_sentinels_match_python_values() {
    assert_eq!(START, "__start__");
    assert_eq!(END, "__end__");
}
