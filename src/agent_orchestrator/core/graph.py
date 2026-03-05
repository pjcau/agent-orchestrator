"""StateGraph — LangGraph-inspired graph engine, provider-agnostic.

Core concepts:
- State: TypedDict with optional reducer functions per key
- Node: async function that takes state and returns partial state updates
- Edge: fixed or conditional transitions between nodes
- Graph: compiles nodes + edges, executes with checkpointing
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable

from .checkpoint import Checkpoint, Checkpointer


# Sentinel nodes
START = "__start__"
END = "__end__"

# Type aliases
State = dict[str, Any]
NodeFunc = Callable[[State], Awaitable[State | None]]
RouterFunc = Callable[[State], str | list[str]]
Reducer = Callable[[Any, Any], Any]


class EdgeType(str, Enum):
    FIXED = "fixed"
    CONDITIONAL = "conditional"


@dataclass
class Edge:
    source: str
    target: str | None = None  # None for conditional edges
    edge_type: EdgeType = EdgeType.FIXED
    router: RouterFunc | None = None
    route_map: dict[str, str] | None = None


@dataclass
class NodeConfig:
    name: str
    func: NodeFunc
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphConfig:
    recursion_limit: int = 25
    timeout_seconds: float = 300.0


@dataclass
class GraphResult:
    state: State
    steps: list[StepRecord]
    success: bool
    error: str | None = None


@dataclass
class StepRecord:
    node: str
    state_before: State
    state_after: State
    step_index: int


class StateGraph:
    """Build and execute a directed graph of state-transforming nodes.

    Inspired by LangGraph's StateGraph but provider-agnostic.

    Usage:
        graph = StateGraph()
        graph.add_node("analyze", analyze_fn)
        graph.add_node("decide", decide_fn)
        graph.add_edge(START, "analyze")
        graph.add_conditional_edges("analyze", router_fn, {"go": "decide", "stop": END})
        graph.add_edge("decide", END)

        compiled = graph.compile()
        result = await compiled.invoke({"input": "hello"})
    """

    def __init__(self, reducers: dict[str, Reducer] | None = None):
        self._nodes: dict[str, NodeConfig] = {}
        self._edges: list[Edge] = []
        self._reducers: dict[str, Reducer] = reducers or {}
        self._compiled = False

    def add_node(self, name: str, func: NodeFunc, **metadata: Any) -> StateGraph:
        if name in (START, END):
            raise ValueError(f"Cannot use reserved name: {name}")
        if name in self._nodes:
            raise ValueError(f"Node already exists: {name}")
        self._nodes[name] = NodeConfig(name=name, func=func, metadata=metadata)
        return self

    def add_edge(self, source: str, target: str) -> StateGraph:
        self._edges.append(Edge(source=source, target=target, edge_type=EdgeType.FIXED))
        return self

    def add_conditional_edges(
        self,
        source: str,
        router: RouterFunc,
        route_map: dict[str, str] | None = None,
    ) -> StateGraph:
        self._edges.append(
            Edge(
                source=source,
                target=None,
                edge_type=EdgeType.CONDITIONAL,
                router=router,
                route_map=route_map,
            )
        )
        return self

    def compile(
        self,
        checkpointer: Checkpointer | None = None,
        config: GraphConfig | None = None,
    ) -> CompiledGraph:
        self._validate()
        self._compiled = True
        return CompiledGraph(
            nodes=dict(self._nodes),
            edges=list(self._edges),
            reducers=dict(self._reducers),
            checkpointer=checkpointer,
            config=config or GraphConfig(),
        )

    def _validate(self) -> None:
        """Validate graph structure before compilation."""
        # Check that START has at least one outgoing edge
        start_edges = [e for e in self._edges if e.source == START]
        if not start_edges:
            raise ValueError("Graph must have at least one edge from START")

        # Check that all edge sources/targets reference valid nodes
        valid_names = set(self._nodes.keys()) | {START, END}
        for edge in self._edges:
            if edge.source not in valid_names:
                raise ValueError(f"Edge source not found: {edge.source}")
            if edge.edge_type == EdgeType.FIXED and edge.target not in valid_names:
                raise ValueError(f"Edge target not found: {edge.target}")
            if edge.edge_type == EdgeType.CONDITIONAL:
                if edge.route_map:
                    for target in edge.route_map.values():
                        if target not in valid_names:
                            raise ValueError(f"Route target not found: {target}")

        # Check all nodes are reachable from START
        reachable = self._find_reachable(START)
        unreachable = set(self._nodes.keys()) - reachable
        if unreachable:
            raise ValueError(f"Unreachable nodes: {unreachable}")

    def _find_reachable(self, start: str) -> set[str]:
        """BFS to find all reachable nodes from start."""
        visited: set[str] = set()
        queue = [start]
        while queue:
            current = queue.pop(0)
            if current in visited or current == END:
                continue
            visited.add(current)
            for edge in self._edges:
                if edge.source == current:
                    if edge.edge_type == EdgeType.FIXED and edge.target:
                        queue.append(edge.target)
                    elif edge.route_map:
                        queue.extend(edge.route_map.values())
        visited.discard(START)
        return visited


class CompiledGraph:
    """An executable graph. Created by StateGraph.compile()."""

    def __init__(
        self,
        nodes: dict[str, NodeConfig],
        edges: list[Edge],
        reducers: dict[str, Reducer],
        checkpointer: Checkpointer | None,
        config: GraphConfig,
    ):
        self._nodes = nodes
        self._edges = edges
        self._reducers = reducers
        self._checkpointer = checkpointer
        self._config = config

    async def invoke(
        self,
        initial_state: State,
        thread_id: str | None = None,
        resume_from: str | None = None,
    ) -> GraphResult:
        """Execute the graph from START to END.

        Args:
            initial_state: Starting state dict
            thread_id: Optional thread ID for checkpointing
            resume_from: Optional checkpoint ID to resume from
        """
        thread_id = thread_id or str(uuid.uuid4())
        steps: list[StepRecord] = []
        step_index = 0

        # Resume from checkpoint if requested
        if resume_from and self._checkpointer:
            checkpoint = await self._checkpointer.get(resume_from)
            if checkpoint:
                state = dict(checkpoint.state)
                current_nodes = checkpoint.next_nodes
            else:
                raise ValueError(f"Checkpoint not found: {resume_from}")
        else:
            state = dict(initial_state)
            current_nodes = self._get_next_nodes(START, state)

        while current_nodes and step_index < self._config.recursion_limit:
            for node_name in current_nodes:
                if node_name == END:
                    return GraphResult(state=state, steps=steps, success=True)

                node = self._nodes.get(node_name)
                if not node:
                    return GraphResult(
                        state=state,
                        steps=steps,
                        success=False,
                        error=f"Node not found: {node_name}",
                    )

                state_before = dict(state)

                # Execute node with timeout
                try:
                    update = await asyncio.wait_for(
                        node.func(state),
                        timeout=self._config.timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    return GraphResult(
                        state=state,
                        steps=steps,
                        success=False,
                        error=f"Node '{node_name}' timed out after {self._config.timeout_seconds}s",
                    )
                except Exception as e:
                    return GraphResult(
                        state=state,
                        steps=steps,
                        success=False,
                        error=f"Node '{node_name}' failed: {e}",
                    )

                # Apply state update via reducers
                if update:
                    state = self._apply_update(state, update)

                steps.append(
                    StepRecord(
                        node=node_name,
                        state_before=state_before,
                        state_after=dict(state),
                        step_index=step_index,
                    )
                )

                # Checkpoint after each node
                next_nodes = self._get_next_nodes(node_name, state)
                if self._checkpointer:
                    await self._checkpointer.save(
                        Checkpoint(
                            checkpoint_id=f"{thread_id}:{step_index}",
                            thread_id=thread_id,
                            state=dict(state),
                            next_nodes=next_nodes,
                            step_index=step_index,
                        )
                    )

                current_nodes = next_nodes
                step_index += 1
                break  # Process one node per step (sequential execution)

        if step_index >= self._config.recursion_limit:
            return GraphResult(
                state=state,
                steps=steps,
                success=False,
                error=f"Recursion limit reached ({self._config.recursion_limit})",
            )

        return GraphResult(state=state, steps=steps, success=True)

    def _get_next_nodes(self, current: str, state: State) -> list[str]:
        """Determine next nodes based on edges from current node."""
        next_nodes: list[str] = []
        for edge in self._edges:
            if edge.source != current:
                continue
            if edge.edge_type == EdgeType.FIXED:
                if edge.target:
                    next_nodes.append(edge.target)
            elif edge.edge_type == EdgeType.CONDITIONAL and edge.router:
                result = edge.router(state)
                if isinstance(result, str):
                    result = [result]
                for route_key in result:
                    if edge.route_map and route_key in edge.route_map:
                        next_nodes.append(edge.route_map[route_key])
                    elif route_key in self._nodes or route_key == END:
                        next_nodes.append(route_key)
        return next_nodes

    def _apply_update(self, state: State, update: State) -> State:
        """Apply a partial state update, using reducers where defined."""
        new_state = dict(state)
        for key, value in update.items():
            if key in self._reducers:
                new_state[key] = self._reducers[key](state.get(key), value)
            else:
                new_state[key] = value
        return new_state

    def get_graph_info(self) -> dict[str, Any]:
        """Return graph structure for visualization/debugging."""
        return {
            "nodes": list(self._nodes.keys()),
            "edges": [
                {
                    "source": e.source,
                    "target": e.target,
                    "type": e.edge_type.value,
                    "routes": list(e.route_map.keys()) if e.route_map else None,
                }
                for e in self._edges
            ],
        }
