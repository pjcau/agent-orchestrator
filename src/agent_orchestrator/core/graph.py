"""StateGraph — LangGraph-inspired graph engine, provider-agnostic.

Core concepts:
- State: TypedDict with optional reducer functions per key
- Node: async function that takes state and returns partial state updates
- Edge: fixed or conditional transitions between nodes
- Graph: compiles nodes + edges, executes with checkpointing
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator  # noqa: F401 (used in type annotation)
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable

from .checkpoint import Checkpoint, Checkpointer
from .channels import BaseChannel, ChannelManager, BinaryOperatorChannel


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
    enable_parallel: bool = True  # Run independent nodes in parallel


class InterruptType(str, Enum):
    HUMAN_INPUT = "human_input"
    APPROVAL = "approval"
    CUSTOM = "custom"


@dataclass
class Interrupt:
    """Request to pause graph execution for external input."""

    interrupt_type: InterruptType
    message: str
    node: str
    options: list[str] | None = None  # For approval: ["approve", "reject"]
    metadata: dict[str, Any] = field(default_factory=dict)


class GraphInterrupt(Exception):
    """Raised by a node to pause execution and wait for input."""

    def __init__(self, interrupt: Interrupt):
        self.interrupt = interrupt
        super().__init__(f"Graph interrupted at '{interrupt.node}': {interrupt.message}")


@dataclass
class GraphResult:
    state: State
    steps: list[StepRecord]
    success: bool
    error: str | None = None
    interrupted: Interrupt | None = None  # Set when graph pauses for input


@dataclass
class StepRecord:
    node: str
    state_before: State
    state_after: State
    step_index: int
    parallel_group: list[str] | None = None  # Nodes executed in parallel


class StreamEventType(str, Enum):
    """Event types emitted during graph streaming."""

    GRAPH_START = "graph_start"
    NODE_START = "node_start"
    NODE_END = "node_end"
    NODE_ERROR = "node_error"
    GRAPH_END = "graph_end"


@dataclass
class StreamEvent:
    """Event yielded by CompiledGraph.astream() during execution.

    Provides real-time visibility into graph execution without polling.
    Each event contains the current state, which node triggered it,
    timing information, and any state delta produced by the node.
    """

    event_type: StreamEventType
    node: str | None
    step_index: int
    state: State
    timestamp: float = field(default_factory=time.time)
    delta: State | None = None  # State changes from this node
    parallel_group: list[str] | None = None
    error: str | None = None
    interrupted: Interrupt | None = None
    elapsed_ms: float = 0.0  # Time taken by this node


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

    def __init__(
        self,
        reducers: dict[str, Reducer] | None = None,
        channel_config: dict[str, BaseChannel] | None = None,
    ):
        self._nodes: dict[str, NodeConfig] = {}
        self._edges: list[Edge] = []
        self._reducers: dict[str, Reducer] = reducers or {}
        self._channel_config: dict[str, BaseChannel] = channel_config or {}
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
        # Build ChannelManager from channel_config
        channel_manager = ChannelManager()
        for key, channel in self._channel_config.items():
            channel_manager.register(key, channel)
        # Auto-create BinaryOperatorChannel for reducers without explicit channels
        for key, reducer in self._reducers.items():
            if key not in self._channel_config:
                channel_manager.register(key, BinaryOperatorChannel(reducer))
        return CompiledGraph(
            nodes=dict(self._nodes),
            edges=list(self._edges),
            reducers=dict(self._reducers),
            checkpointer=checkpointer,
            config=config or GraphConfig(),
            channel_manager=channel_manager,
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
        channel_manager: ChannelManager | None = None,
    ):
        self._nodes = nodes
        self._edges = edges
        self._reducers = reducers
        self._checkpointer = checkpointer
        self._config = config
        self._channel_manager = channel_manager or ChannelManager()

    async def invoke(
        self,
        initial_state: State,
        thread_id: str | None = None,
        resume_from: str | None = None,
        human_input: dict[str, Any] | None = None,
    ) -> GraphResult:
        """Execute the graph from START to END.

        Args:
            initial_state: Starting state dict
            thread_id: Optional thread ID for checkpointing
            resume_from: Optional checkpoint ID to resume from
            human_input: Input to provide when resuming from a human-in-the-loop
                        interrupt. Merged into state before continuing.
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
                step_index = checkpoint.step_index
                thread_id = checkpoint.thread_id  # Preserve original thread
                # Merge human input into state when resuming from interrupt
                if human_input:
                    state = self._apply_update(state, human_input)
            else:
                raise ValueError(f"Checkpoint not found: {resume_from}")
        else:
            state = dict(initial_state)
            # Seed channels with initial state values
            for key, value in state.items():
                channel = self._channel_manager.get_channel(key)
                if channel is not None:
                    channel.update([value])
            current_nodes = self._get_next_nodes(START, state)

        while current_nodes and step_index < self._config.recursion_limit:
            # Check for END in the node list
            if END in current_nodes:
                return GraphResult(state=state, steps=steps, success=True)

            # Filter to actual executable nodes
            exec_nodes = [n for n in current_nodes if n != END and n in self._nodes]
            if not exec_nodes:
                return GraphResult(
                    state=state,
                    steps=steps,
                    success=False,
                    error=f"No executable nodes found in: {current_nodes}",
                )

            # Execute nodes — parallel if multiple and enabled, else sequential
            if len(exec_nodes) > 1 and self._config.enable_parallel:
                result = await self._execute_parallel(
                    exec_nodes, state, steps, step_index, thread_id
                )
            else:
                result = await self._execute_single(
                    exec_nodes[0], state, steps, step_index, thread_id
                )

            if result.interrupted or not result.success:
                return result

            state = result.state
            steps = result.steps
            step_index += 1

            # Determine next nodes from all executed nodes
            all_next: list[str] = []
            executed = (
                exec_nodes
                if len(exec_nodes) > 1 and self._config.enable_parallel
                else [exec_nodes[0]]
            )
            for node_name in executed:
                all_next.extend(self._get_next_nodes(node_name, state))
            # Deduplicate while preserving order
            seen: set[str] = set()
            current_nodes = []
            for n in all_next:
                if n not in seen:
                    seen.add(n)
                    current_nodes.append(n)

        if step_index >= self._config.recursion_limit:
            return GraphResult(
                state=state,
                steps=steps,
                success=False,
                error=f"Recursion limit reached ({self._config.recursion_limit})",
            )

        return GraphResult(state=state, steps=steps, success=True)

    async def astream(
        self,
        initial_state: State,
        thread_id: str | None = None,
        resume_from: str | None = None,
        human_input: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream graph execution, yielding a StreamEvent at each step.

        Same execution logic as invoke(), but yields events in real-time
        as nodes start, complete, or fail. Callers can forward these events
        to WebSocket clients, SSE endpoints, or logging pipelines.

        Usage:
            compiled = graph.compile()
            async for event in compiled.astream({"input": "hello"}):
                print(f"{event.event_type}: {event.node}")
                if event.event_type == StreamEventType.GRAPH_END:
                    final_state = event.state
        """
        thread_id = thread_id or str(uuid.uuid4())
        steps: list[StepRecord] = []
        step_index = 0
        graph_start = time.time()

        # Resume from checkpoint if requested
        if resume_from and self._checkpointer:
            checkpoint = await self._checkpointer.get(resume_from)
            if checkpoint:
                state = dict(checkpoint.state)
                current_nodes = checkpoint.next_nodes
                step_index = checkpoint.step_index
                thread_id = checkpoint.thread_id
                if human_input:
                    state = self._apply_update(state, human_input)
            else:
                yield StreamEvent(
                    event_type=StreamEventType.NODE_ERROR,
                    node=None,
                    step_index=0,
                    state=initial_state,
                    error=f"Checkpoint not found: {resume_from}",
                )
                return
        else:
            state = dict(initial_state)
            for key, value in state.items():
                channel = self._channel_manager.get_channel(key)
                if channel is not None:
                    channel.update([value])
            current_nodes = self._get_next_nodes(START, state)

        yield StreamEvent(
            event_type=StreamEventType.GRAPH_START,
            node=None,
            step_index=0,
            state=dict(state),
        )

        while current_nodes and step_index < self._config.recursion_limit:
            if END in current_nodes:
                yield StreamEvent(
                    event_type=StreamEventType.GRAPH_END,
                    node=None,
                    step_index=step_index,
                    state=dict(state),
                    elapsed_ms=(time.time() - graph_start) * 1000,
                )
                return

            exec_nodes = [n for n in current_nodes if n != END and n in self._nodes]
            if not exec_nodes:
                yield StreamEvent(
                    event_type=StreamEventType.NODE_ERROR,
                    node=None,
                    step_index=step_index,
                    state=dict(state),
                    error=f"No executable nodes found in: {current_nodes}",
                )
                return

            # Execute nodes and yield events for each
            is_parallel = len(exec_nodes) > 1 and self._config.enable_parallel

            if is_parallel:
                # Yield NODE_START for all parallel nodes
                for node_name in exec_nodes:
                    yield StreamEvent(
                        event_type=StreamEventType.NODE_START,
                        node=node_name,
                        step_index=step_index,
                        state=dict(state),
                        parallel_group=exec_nodes,
                    )

                result = await self._execute_parallel(
                    exec_nodes, state, steps, step_index, thread_id
                )

                # Yield NODE_END/NODE_ERROR for parallel group
                if result.success:
                    # Compute delta from the parallel step
                    step_record = result.steps[-1] if result.steps else None
                    delta = (
                        {
                            k: v
                            for k, v in step_record.state_after.items()
                            if step_record.state_before.get(k) != v
                        }
                        if step_record
                        else None
                    )
                    for node_name in exec_nodes:
                        yield StreamEvent(
                            event_type=StreamEventType.NODE_END,
                            node=node_name,
                            step_index=step_index,
                            state=dict(result.state),
                            delta=delta,
                            parallel_group=exec_nodes,
                        )
                else:
                    yield StreamEvent(
                        event_type=StreamEventType.NODE_ERROR,
                        node=",".join(exec_nodes),
                        step_index=step_index,
                        state=dict(result.state),
                        error=result.error,
                        interrupted=result.interrupted,
                        parallel_group=exec_nodes,
                    )
                    return
            else:
                node_name = exec_nodes[0]
                yield StreamEvent(
                    event_type=StreamEventType.NODE_START,
                    node=node_name,
                    step_index=step_index,
                    state=dict(state),
                )

                node_start = time.time()
                result = await self._execute_single(node_name, state, steps, step_index, thread_id)
                node_elapsed = (time.time() - node_start) * 1000

                if result.success:
                    step_record = result.steps[-1] if result.steps else None
                    delta = (
                        {
                            k: v
                            for k, v in step_record.state_after.items()
                            if step_record.state_before.get(k) != v
                        }
                        if step_record
                        else None
                    )
                    yield StreamEvent(
                        event_type=StreamEventType.NODE_END,
                        node=node_name,
                        step_index=step_index,
                        state=dict(result.state),
                        delta=delta,
                        elapsed_ms=node_elapsed,
                    )
                else:
                    yield StreamEvent(
                        event_type=StreamEventType.NODE_ERROR,
                        node=node_name,
                        step_index=step_index,
                        state=dict(result.state),
                        error=result.error,
                        interrupted=result.interrupted,
                        elapsed_ms=node_elapsed,
                    )
                    return

            state = result.state
            steps = result.steps
            step_index += 1

            all_next: list[str] = []
            executed = exec_nodes if is_parallel else [exec_nodes[0]]
            for n in executed:
                all_next.extend(self._get_next_nodes(n, state))
            seen: set[str] = set()
            current_nodes = []
            for n in all_next:
                if n not in seen:
                    seen.add(n)
                    current_nodes.append(n)

        if step_index >= self._config.recursion_limit:
            yield StreamEvent(
                event_type=StreamEventType.NODE_ERROR,
                node=None,
                step_index=step_index,
                state=dict(state),
                error=f"Recursion limit reached ({self._config.recursion_limit})",
                elapsed_ms=(time.time() - graph_start) * 1000,
            )
            return

        yield StreamEvent(
            event_type=StreamEventType.GRAPH_END,
            node=None,
            step_index=step_index,
            state=dict(state),
            elapsed_ms=(time.time() - graph_start) * 1000,
        )

    async def _execute_single(
        self,
        node_name: str,
        state: State,
        steps: list[StepRecord],
        step_index: int,
        thread_id: str,
    ) -> GraphResult:
        """Execute a single node."""
        from .tracing import get_tracer

        tracer = get_tracer()
        node_span = tracer.start_span("graph.node")
        node_span.set_attribute("graph.node.name", node_name)
        node_span.set_attribute("graph.step", step_index)

        node = self._nodes[node_name]
        state_before = dict(state)

        try:
            update = await asyncio.wait_for(
                node.func(state),
                timeout=self._config.timeout_seconds,
            )
        except GraphInterrupt as gi:
            node_span.end()
            # Human-in-the-loop: save checkpoint and return interrupt
            if self._checkpointer:
                next_nodes = [node_name]  # Re-execute this node on resume
                await self._checkpointer.save(
                    Checkpoint(
                        checkpoint_id=f"{thread_id}:{step_index}",
                        thread_id=thread_id,
                        state=dict(state),
                        next_nodes=next_nodes,
                        step_index=step_index,
                        metadata={"interrupt": gi.interrupt.message},
                    )
                )
            return GraphResult(
                state=state,
                steps=steps,
                success=False,
                interrupted=gi.interrupt,
            )
        except asyncio.TimeoutError:
            node_span.record_exception(
                Exception(f"Node '{node_name}' timed out after {self._config.timeout_seconds}s")
            )
            node_span.set_status("ERROR", f"timeout after {self._config.timeout_seconds}s")
            node_span.end()
            return GraphResult(
                state=state,
                steps=steps,
                success=False,
                error=f"Node '{node_name}' timed out after {self._config.timeout_seconds}s",
            )
        except Exception as e:
            node_span.record_exception(e)
            node_span.set_status("ERROR", str(e))
            node_span.end()
            return GraphResult(
                state=state,
                steps=steps,
                success=False,
                error=f"Node '{node_name}' failed: {e}",
            )

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

        # Checkpoint
        if self._checkpointer:
            next_nodes = self._get_next_nodes(node_name, state)
            await self._checkpointer.save(
                Checkpoint(
                    checkpoint_id=f"{thread_id}:{step_index}",
                    thread_id=thread_id,
                    state=dict(state),
                    next_nodes=next_nodes,
                    step_index=step_index,
                )
            )

        node_span.end()
        return GraphResult(state=state, steps=steps, success=True)

    async def _execute_parallel(
        self,
        node_names: list[str],
        state: State,
        steps: list[StepRecord],
        step_index: int,
        thread_id: str,
    ) -> GraphResult:
        """Execute multiple nodes in parallel, then merge their updates."""
        from .tracing import get_tracer

        state_before = dict(state)

        async def run_node(name: str) -> tuple[str, State | None, Exception | None]:
            tracer = get_tracer()
            node_span = tracer.start_span("graph.node")
            node_span.set_attribute("graph.node.name", name)
            node_span.set_attribute("graph.step", step_index)
            node = self._nodes[name]
            try:
                update = await asyncio.wait_for(
                    node.func(dict(state)),  # Each node gets a copy
                    timeout=self._config.timeout_seconds,
                )
                node_span.end()
                return (name, update, None)
            except GraphInterrupt as gi:
                node_span.end()
                return (name, None, gi)
            except Exception as e:
                node_span.record_exception(e)
                node_span.set_status("ERROR", str(e))
                node_span.end()
                return (name, None, e)

        results = await asyncio.gather(*[run_node(n) for n in node_names])

        # Check for interrupts or errors
        for name, update, error in results:
            if isinstance(error, GraphInterrupt):
                if self._checkpointer:
                    await self._checkpointer.save(
                        Checkpoint(
                            checkpoint_id=f"{thread_id}:{step_index}",
                            thread_id=thread_id,
                            state=dict(state),
                            next_nodes=[name],
                            step_index=step_index,
                            metadata={"interrupt": error.interrupt.message},
                        )
                    )
                return GraphResult(
                    state=state,
                    steps=steps,
                    success=False,
                    interrupted=error.interrupt,
                )
            if error is not None:
                return GraphResult(
                    state=state,
                    steps=steps,
                    success=False,
                    error=f"Node '{name}' failed: {error}",
                )

        # Merge all updates into state
        merged_state = dict(state)
        for name, update, _ in results:
            if update:
                merged_state = self._apply_update(merged_state, update)

        steps.append(
            StepRecord(
                node=",".join(node_names),
                state_before=state_before,
                state_after=dict(merged_state),
                step_index=step_index,
                parallel_group=node_names,
            )
        )

        # Checkpoint
        if self._checkpointer:
            all_next: list[str] = []
            for name in node_names:
                all_next.extend(self._get_next_nodes(name, merged_state))
            await self._checkpointer.save(
                Checkpoint(
                    checkpoint_id=f"{thread_id}:{step_index}",
                    thread_id=thread_id,
                    state=dict(merged_state),
                    next_nodes=list(set(all_next)),
                    step_index=step_index,
                )
            )

        return GraphResult(state=merged_state, steps=steps, success=True)

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
        """Apply a partial state update, using channels or reducers where defined."""
        new_state = dict(state)
        for key, value in update.items():
            channel = self._channel_manager.get_channel(key)
            if channel is not None:
                # Use channel-based update
                channel.update([value])
                new_state[key] = channel.get()
            elif key in self._reducers:
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
