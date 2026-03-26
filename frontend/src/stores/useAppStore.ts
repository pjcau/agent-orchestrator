import { create } from "zustand";
import type {
  AgentSnapshot,
  TaskSnapshot,
  GraphData,
  CacheStats,
  OrchestratorEvent,
  Snapshot,
  ChatMessage,
  UsageStats,
  TaskPlanItem,
  InteractionItem,
} from "@/api/types";

export type OrchestratorStatus = "idle" | "running" | "completed" | "failed" | "error";

export interface ActivityItem {
  id: number;
  category: "spawn" | "step" | "tool" | "task" | "complete" | "error";
  agent: string;
  desc: string;
  detail?: string;
  time: number;
}

interface AppState {
  // Connection state
  wsConnected: boolean;
  sseMode: boolean;

  // Orchestrator state (from snapshots & events)
  orchestratorStatus: OrchestratorStatus;
  agents: Record<string, AgentSnapshot>;
  tasks: TaskSnapshot[];
  totalCostUsd: number;
  totalTokens: number;
  graph: GraphData;
  cache: CacheStats;
  eventCount: number;

  // Graph node execution states (active/done/error)
  graphNodeStates: Record<string, "active" | "done" | "error">;

  // Chat state
  messages: ChatMessage[];
  isStreaming: boolean;
  streamBuffer: string;
  conversationId: string | null;
  lastTokenSpeed: number;

  // UI state
  sidebarOpen: boolean;

  // Task plan
  taskPlanItems: TaskPlanItem[];

  // Cumulative DB usage
  cumulativeUsage: UsageStats | null;

  // Event log
  events: OrchestratorEvent[];

  // Agent activity log
  activityItems: ActivityItem[];
  activityCounter: number;

  // Agent interactions for timeline
  interactions: InteractionItem[];

  // Pending team job
  pendingTeamJobId: string | null;
  pendingTeamModel: string | null;

  // Actions
  setWsConnected: (connected: boolean) => void;
  setSseMode: (mode: boolean) => void;
  applySnapshot: (snapshot: Snapshot) => void;
  applyEvent: (event: OrchestratorEvent) => void;
  addMessage: (msg: ChatMessage) => void;
  appendStreamChunk: (chunk: string) => void;
  finalizeStream: (data?: {
    output?: string;
    usage?: { output_tokens?: number; model?: string };
    elapsed_s?: number;
    speed?: number;
  }) => void;
  clearStreamBuffer: () => void;
  setConversationId: (id: string | null) => void;
  setLastTokenSpeed: (speed: number) => void;
  setSidebarOpen: (open: boolean) => void;
  setCumulativeUsage: (usage: UsageStats | null) => void;
  clearMessages: () => void;
  clearEvents: () => void;
  clearTaskPlan: () => void;
  clearActivityItems: () => void;
  clearInteractions: () => void;
  addActivityItem: (
    category: ActivityItem["category"],
    agent: string,
    desc: string,
    detail?: string
  ) => void;
  addInteraction: (
    from: string,
    to: string,
    desc: string,
    status: InteractionItem["status"]
  ) => void;
  updateInteraction: (
    from: string,
    to: string,
    status: InteractionItem["status"]
  ) => void;
  setPendingTeamJob: (jobId: string | null, model: string | null) => void;
  reset: () => void;
}

const MAX_EVENTS = 500;
const MAX_ACTIVITY = 200;
const MAX_INTERACTIONS = 50;

const initialCacheState: CacheStats = {
  hits: 0,
  misses: 0,
  hit_rate: 0,
  evictions: 0,
  entries: 0,
  total_saved_tokens: 0,
};

const initialGraphState: GraphData = {
  nodes: [],
  edges: [],
};

export const useAppStore = create<AppState>((set, get) => ({
  // Connection state
  wsConnected: false,
  sseMode: false,

  // Orchestrator state
  orchestratorStatus: "idle",
  agents: {},
  tasks: [],
  totalCostUsd: 0,
  totalTokens: 0,
  graph: initialGraphState,
  cache: initialCacheState,
  eventCount: 0,

  // Graph node states
  graphNodeStates: {},

  // Chat state
  messages: [],
  isStreaming: false,
  streamBuffer: "",
  conversationId: null,
  lastTokenSpeed: 0,

  // UI state
  sidebarOpen: true,

  // Task plan
  taskPlanItems: [],

  // Cumulative usage
  cumulativeUsage: null,

  // Event log
  events: [],

  // Activity log
  activityItems: [],
  activityCounter: 0,

  // Interactions
  interactions: [],

  // Pending team job
  pendingTeamJobId: null,
  pendingTeamModel: null,

  // --- Actions ---

  setWsConnected: (connected) => set({ wsConnected: connected }),

  setSseMode: (mode) => set({ sseMode: mode }),

  applySnapshot: (snapshot) =>
    set({
      orchestratorStatus: snapshot.orchestrator_status as OrchestratorStatus,
      agents: snapshot.agents,
      tasks: snapshot.tasks,
      totalCostUsd: snapshot.total_cost_usd,
      totalTokens: snapshot.total_tokens,
      graph: snapshot.graph,
      cache: snapshot.cache,
      eventCount: snapshot.event_count,
    }),

  applyEvent: (event) => {
    const state = get();

    // Append to event log
    const events = [...state.events, event];
    const trimmedEvents =
      events.length > MAX_EVENTS ? events.slice(-MAX_EVENTS) : events;

    // Compute state updates from event type
    const updates: Partial<AppState> = { events: trimmedEvents };

    const t = event.event_type;
    const agentName = event.agent_name ?? "";
    const d = event.data;

    // Event count
    updates.eventCount = (state.eventCount || 0) + 1;

    if (t === "orchestrator.start") {
      updates.orchestratorStatus = "running";
    } else if (t === "orchestrator.end") {
      const success = (d as { success?: boolean }).success;
      updates.orchestratorStatus = success ? "completed" : "failed";
    } else if (t === "agent.spawn") {
      const payload = d as {
        provider?: string;
        role?: string;
        tools?: string[];
      };
      updates.agents = {
        ...state.agents,
        [agentName]: {
          name: agentName,
          status: "running",
          steps: 0,
          tokens: 0,
          cost_usd: 0,
          provider: payload.provider ?? "",
          role: payload.role ?? "",
          tools: payload.tools ?? [],
        },
      };
    } else if (t === "agent.step") {
      const existing = state.agents[agentName];
      if (existing) {
        updates.agents = {
          ...state.agents,
          [agentName]: { ...existing, steps: (existing.steps ?? 0) + 1 },
        };
      }
    } else if (t === "agent.complete") {
      const existing = state.agents[agentName];
      if (existing) {
        updates.agents = {
          ...state.agents,
          [agentName]: { ...existing, status: "completed" },
        };
      }
    } else if (t === "agent.error" || t === "agent.stalled") {
      const existing = state.agents[agentName];
      if (existing) {
        updates.agents = {
          ...state.agents,
          [agentName]: { ...existing, status: "error" },
        };
      }
    } else if (t === "cooperation.task_assigned") {
      const payload = d as {
        task_id?: string;
        from_agent?: string;
        to_agent?: string;
        description?: string;
        priority?: string;
      };
      updates.tasks = [
        ...state.tasks,
        {
          task_id: payload.task_id,
          from_agent: payload.from_agent,
          to_agent: payload.to_agent,
          description: payload.description ?? "",
          status: "pending",
          priority: payload.priority ?? "normal",
        },
      ];
    } else if (t === "cooperation.task_completed") {
      const payload = d as { task_id?: string; success?: boolean };
      updates.tasks = state.tasks.map((task) =>
        task.task_id === payload.task_id
          ? { ...task, status: payload.success ? "completed" : "failed" }
          : task
      );
    } else if (t === "cache.hit") {
      const newHits = (state.cache.hits || 0) + 1;
      const total = newHits + (state.cache.misses || 0);
      updates.cache = {
        ...state.cache,
        hits: newHits,
        hit_rate: total > 0 ? newHits / total : 0,
      };
    } else if (t === "cache.miss") {
      const newMisses = (state.cache.misses || 0) + 1;
      const total = (state.cache.hits || 0) + newMisses;
      updates.cache = {
        ...state.cache,
        misses: newMisses,
        hit_rate: total > 0 ? (state.cache.hits || 0) / total : 0,
      };
    } else if (t === "cache.stats") {
      const payload = d as { cache_stats?: CacheStats };
      if (payload.cache_stats) {
        updates.cache = payload.cache_stats;
      }
    } else if (t === "metrics.cost_update") {
      const payload = d as { total_cost_usd?: number };
      if (payload.total_cost_usd !== undefined) {
        updates.totalCostUsd = payload.total_cost_usd;
      }
    } else if (t === "metrics.token_update") {
      const payload = d as {
        total_tokens?: number;
        agent_tokens?: number;
        agent_cost_usd?: number;
      };
      if (payload.total_tokens !== undefined) {
        updates.totalTokens = payload.total_tokens;
      }
      if (agentName && state.agents[agentName] && payload.agent_tokens !== undefined) {
        updates.agents = {
          ...state.agents,
          [agentName]: {
            ...state.agents[agentName],
            tokens: payload.agent_tokens,
            cost_usd: payload.agent_cost_usd ?? state.agents[agentName].cost_usd,
          },
        };
      }
    } else if (t === "graph.start") {
      const payload = d as { nodes?: string[]; edges?: GraphData["edges"] };
      updates.graph = {
        nodes: payload.nodes ?? [],
        edges: payload.edges ?? [],
      };
      updates.graphNodeStates = {};
      updates.taskPlanItems = [];
    } else if (t === "graph.node.enter") {
      const nodeName = event.node_name ?? (d as { node?: string }).node ?? "";
      if (nodeName) {
        updates.graphNodeStates = { ...state.graphNodeStates, [nodeName]: "active" };
        // Upsert task plan item
        const existing = state.taskPlanItems.find((i) => i.nodeId === nodeName);
        if (!existing) {
          updates.taskPlanItems = [
            ...state.taskPlanItems,
            { nodeId: nodeName, status: "in_progress", startedAt: Date.now(), elapsed: null },
          ];
        } else {
          updates.taskPlanItems = state.taskPlanItems.map((i) =>
            i.nodeId === nodeName ? { ...i, status: "in_progress" } : i
          );
        }
      }
    } else if (t === "graph.node.exit") {
      const nodeName = event.node_name ?? (d as { node?: string }).node ?? "";
      if (nodeName) {
        updates.graphNodeStates = { ...state.graphNodeStates, [nodeName]: "done" };
        updates.taskPlanItems = state.taskPlanItems.map((i) => {
          if (i.nodeId !== nodeName) return i;
          const elapsed = i.startedAt
            ? Math.round((Date.now() - i.startedAt) / 100) / 10
            : null;
          return { ...i, status: "completed", elapsed };
        });
      }
    } else if (t === "graph.node.error") {
      const nodeName = event.node_name ?? (d as { node?: string }).node ?? "";
      if (nodeName) {
        updates.graphNodeStates = { ...state.graphNodeStates, [nodeName]: "error" };
        updates.taskPlanItems = state.taskPlanItems.map((i) => {
          if (i.nodeId !== nodeName) return i;
          const elapsed = i.startedAt
            ? Math.round((Date.now() - i.startedAt) / 100) / 10
            : null;
          return { ...i, status: "failed", elapsed };
        });
      }
    } else if (t === "graph.end") {
      // Mark all still-active nodes as done
      const newNodeStates: Record<string, "active" | "done" | "error"> = {};
      for (const [k, v] of Object.entries(state.graphNodeStates)) {
        newNodeStates[k] = v === "active" ? "done" : v;
      }
      updates.graphNodeStates = newNodeStates;
    }

    set(updates);
  },

  addMessage: (msg) =>
    set((state) => ({
      messages: [...state.messages, msg],
    })),

  appendStreamChunk: (chunk) =>
    set((state) => ({
      streamBuffer: state.streamBuffer + chunk,
      isStreaming: true,
    })),

  finalizeStream: (data) =>
    set((state) => {
      const finalBuffer = state.streamBuffer || "";
      const newMessages: ChatMessage[] = [...state.messages];

      // Find and finalize streaming message or add new one
      const streamingIdx = newMessages.findIndex(
        (m) => m.role === "assistant" && (m as ChatMessage & { streaming?: boolean }).streaming
      );

      const completedMsg: ChatMessage = {
        role: "assistant",
        content: finalBuffer,
        model: data?.usage?.model,
        timestamp: Date.now(),
      };

      if (streamingIdx >= 0) {
        newMessages[streamingIdx] = completedMsg;
      } else {
        newMessages.push(completedMsg);
      }

      return {
        messages: newMessages,
        streamBuffer: "",
        isStreaming: false,
        lastTokenSpeed: data?.speed ?? state.lastTokenSpeed,
        totalTokens:
          state.totalTokens + (data?.usage?.output_tokens ?? 0),
      };
    }),

  clearStreamBuffer: () => set({ streamBuffer: "", isStreaming: false }),

  setConversationId: (id) => set({ conversationId: id }),

  setLastTokenSpeed: (speed) => set({ lastTokenSpeed: speed }),

  setSidebarOpen: (open) => set({ sidebarOpen: open }),

  setCumulativeUsage: (usage) => set({ cumulativeUsage: usage }),

  clearMessages: () => set({ messages: [] }),

  clearEvents: () => set({ events: [] }),

  clearTaskPlan: () => set({ taskPlanItems: [] }),

  clearActivityItems: () => set({ activityItems: [], activityCounter: 0 }),

  clearInteractions: () => set({ interactions: [] }),

  addActivityItem: (category, agent, desc, detail) =>
    set((state) => {
      const item: ActivityItem = {
        id: state.activityCounter + 1,
        category,
        agent,
        desc,
        detail,
        time: Date.now(),
      };
      const items = [...state.activityItems, item];
      return {
        activityItems: items.length > MAX_ACTIVITY ? items.slice(-MAX_ACTIVITY) : items,
        activityCounter: state.activityCounter + 1,
      };
    }),

  addInteraction: (from, to, desc, status) =>
    set((state) => {
      const item: InteractionItem = { from, to, desc, status, time: Date.now() };
      const items = [...state.interactions, item];
      return {
        interactions: items.length > MAX_INTERACTIONS ? items.slice(-MAX_INTERACTIONS) : items,
      };
    }),

  updateInteraction: (from, to, status) =>
    set((state) => ({
      interactions: state.interactions.map((i) =>
        i.from === from && i.to === to && i.status === "running"
          ? { ...i, status }
          : i
      ),
    })),

  setPendingTeamJob: (jobId, model) =>
    set({ pendingTeamJobId: jobId, pendingTeamModel: model }),

  reset: () =>
    set({
      orchestratorStatus: "idle",
      agents: {},
      tasks: [],
      totalCostUsd: 0,
      totalTokens: 0,
      graph: initialGraphState,
      cache: initialCacheState,
      eventCount: 0,
      graphNodeStates: {},
      taskPlanItems: [],
      events: [],
      activityItems: [],
      activityCounter: 0,
      interactions: [],
      pendingTeamJobId: null,
      pendingTeamModel: null,
    }),
}));
