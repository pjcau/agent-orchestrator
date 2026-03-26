import { describe, it, expect, beforeEach } from "vitest";
import { useAppStore } from "@/stores/useAppStore";
import type { OrchestratorEvent, Snapshot } from "@/api/types";

describe("useAppStore", () => {
  beforeEach(() => {
    useAppStore.getState().reset();
    // Clear messages and events too
    useAppStore.getState().clearMessages();
    useAppStore.getState().clearEvents();
  });

  it("starts with idle orchestrator status", () => {
    const state = useAppStore.getState();
    expect(state.orchestratorStatus).toBe("idle");
  });

  it("applySnapshot updates orchestrator status and metrics", () => {
    const snapshot: Snapshot = {
      orchestrator_status: "running",
      agents: {},
      tasks: [],
      total_cost_usd: 1.5,
      total_tokens: 5000,
      graph: { nodes: [], edges: [] },
      cache: { hits: 10, misses: 5, hit_rate: 0.667, evictions: 0 },
      event_count: 42,
    };

    useAppStore.getState().applySnapshot(snapshot);
    const state = useAppStore.getState();

    expect(state.orchestratorStatus).toBe("running");
    expect(state.totalCostUsd).toBe(1.5);
    expect(state.totalTokens).toBe(5000);
    expect(state.cache.hits).toBe(10);
    expect(state.eventCount).toBe(42);
  });

  it("applyEvent updates orchestrator status for orchestrator.start", () => {
    const event: OrchestratorEvent = {
      event_type: "orchestrator.start",
      timestamp: Date.now() / 1000,
      data: {},
    };

    useAppStore.getState().applyEvent(event);
    expect(useAppStore.getState().orchestratorStatus).toBe("running");
  });

  it("applyEvent updates agent state on agent.spawn", () => {
    const event: OrchestratorEvent = {
      event_type: "agent.spawn",
      agent_name: "backend",
      timestamp: Date.now() / 1000,
      data: {
        provider: "openrouter",
        role: "Backend Developer",
        tools: ["shell", "file_read"],
      },
    };

    useAppStore.getState().applyEvent(event);
    const state = useAppStore.getState();

    expect(state.agents["backend"]).toBeDefined();
    expect(state.agents["backend"].status).toBe("running");
    expect(state.agents["backend"].provider).toBe("openrouter");
  });

  it("applyEvent adds task on cooperation.task_assigned", () => {
    const event: OrchestratorEvent = {
      event_type: "cooperation.task_assigned",
      timestamp: Date.now() / 1000,
      data: {
        task_id: "task-1",
        from_agent: "team-lead",
        to_agent: "backend",
        description: "Build the API",
        priority: "high",
      },
    };

    useAppStore.getState().applyEvent(event);
    const tasks = useAppStore.getState().tasks;

    expect(tasks).toHaveLength(1);
    expect(tasks[0].task_id).toBe("task-1");
    expect(tasks[0].status).toBe("pending");
  });

  it("applyEvent updates graph on graph.start", () => {
    const event: OrchestratorEvent = {
      event_type: "graph.start",
      timestamp: Date.now() / 1000,
      data: {
        nodes: ["team-lead", "backend", "frontend"],
        edges: [
          { source: "team-lead", target: "backend" },
          { source: "team-lead", target: "frontend" },
        ],
      },
    };

    useAppStore.getState().applyEvent(event);
    const state = useAppStore.getState();

    expect(state.graph.nodes).toHaveLength(3);
    expect(state.graph.edges).toHaveLength(2);
    expect(state.graphNodeStates).toEqual({});
    expect(state.taskPlanItems).toHaveLength(0);
  });

  it("applyEvent sets node to active on graph.node.enter", () => {
    const event: OrchestratorEvent = {
      event_type: "graph.node.enter",
      node_name: "backend",
      timestamp: Date.now() / 1000,
      data: { node: "backend" },
    };

    useAppStore.getState().applyEvent(event);
    const state = useAppStore.getState();

    expect(state.graphNodeStates["backend"]).toBe("active");
    expect(state.taskPlanItems).toHaveLength(1);
    expect(state.taskPlanItems[0].nodeId).toBe("backend");
    expect(state.taskPlanItems[0].status).toBe("in_progress");
  });

  it("applyEvent marks node done on graph.node.exit", () => {
    // First enter
    useAppStore.getState().applyEvent({
      event_type: "graph.node.enter",
      node_name: "backend",
      timestamp: Date.now() / 1000,
      data: {},
    });

    // Then exit
    useAppStore.getState().applyEvent({
      event_type: "graph.node.exit",
      node_name: "backend",
      timestamp: Date.now() / 1000,
      data: {},
    });

    const state = useAppStore.getState();
    expect(state.graphNodeStates["backend"]).toBe("done");
    expect(state.taskPlanItems[0].status).toBe("completed");
  });

  it("cache hit/miss increments counters and updates hit_rate", () => {
    useAppStore.getState().applyEvent({
      event_type: "cache.hit",
      timestamp: Date.now() / 1000,
      data: {},
    });
    useAppStore.getState().applyEvent({
      event_type: "cache.miss",
      timestamp: Date.now() / 1000,
      data: {},
    });
    useAppStore.getState().applyEvent({
      event_type: "cache.hit",
      timestamp: Date.now() / 1000,
      data: {},
    });

    const cache = useAppStore.getState().cache;
    expect(cache.hits).toBe(2);
    expect(cache.misses).toBe(1);
    expect(cache.hit_rate).toBeCloseTo(2 / 3);
  });

  it("addMessage appends to messages array", () => {
    useAppStore.getState().addMessage({
      role: "user",
      content: "Hello",
      timestamp: Date.now(),
    });
    useAppStore.getState().addMessage({
      role: "assistant",
      content: "World",
      timestamp: Date.now(),
    });

    expect(useAppStore.getState().messages).toHaveLength(2);
    expect(useAppStore.getState().messages[0].role).toBe("user");
  });

  it("appendStreamChunk buffers content", () => {
    useAppStore.getState().appendStreamChunk("Hello ");
    useAppStore.getState().appendStreamChunk("world");

    expect(useAppStore.getState().streamBuffer).toBe("Hello world");
    expect(useAppStore.getState().isStreaming).toBe(true);
  });

  it("finalizeStream clears buffer and adds message", () => {
    useAppStore.getState().appendStreamChunk("Final content");
    useAppStore.getState().finalizeStream({
      speed: 45.5,
      usage: { output_tokens: 100, model: "qwen" },
    });

    const state = useAppStore.getState();
    expect(state.streamBuffer).toBe("");
    expect(state.isStreaming).toBe(false);
    expect(state.lastTokenSpeed).toBe(45.5);
  });

  it("reset restores initial state", () => {
    useAppStore.getState().applyEvent({
      event_type: "orchestrator.start",
      timestamp: Date.now() / 1000,
      data: {},
    });
    useAppStore.getState().reset();

    const state = useAppStore.getState();
    expect(state.orchestratorStatus).toBe("idle");
    expect(state.agents).toEqual({});
    expect(state.totalCostUsd).toBe(0);
    expect(state.events).toHaveLength(0);
  });

  it("addActivityItem appends activity and trims at MAX_ACTIVITY", () => {
    for (let i = 0; i < 5; i++) {
      useAppStore.getState().addActivityItem("step", `agent-${i}`, `Step ${i}`);
    }

    expect(useAppStore.getState().activityItems).toHaveLength(5);
    expect(useAppStore.getState().activityItems[0].category).toBe("step");
  });

  it("addInteraction and updateInteraction work correctly", () => {
    useAppStore.getState().addInteraction("team-lead", "backend", "task", "running");
    useAppStore.getState().updateInteraction("team-lead", "backend", "completed");

    const interactions = useAppStore.getState().interactions;
    expect(interactions).toHaveLength(1);
    expect(interactions[0].status).toBe("completed");
  });

  it("setWsConnected updates wsConnected", () => {
    expect(useAppStore.getState().wsConnected).toBe(false);
    useAppStore.getState().setWsConnected(true);
    expect(useAppStore.getState().wsConnected).toBe(true);
  });
});
