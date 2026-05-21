import { create } from "zustand";
import { BP } from "@/lib/breakpoints";
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

export interface AttachedFile {
  /** Display label (filename or workspace path). */
  path: string;
  /** Markdown / text content sent to the model. */
  content: string;
  /**
   * Origin: where the file came from.
   * - "upload": local file uploaded via /api/upload (text or document → markdown).
   * - "workspace": picked from the server-side workspace via /api/file.
   */
  source?: "upload" | "workspace";
  /** MIME type or file extension category (for the UI badge). */
  kind?: string;
  /** Original byte size, when known. */
  bytes?: number;
  /** True when content was clipped by a server-side limit. */
  truncated?: boolean;
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
  attachedFiles: AttachedFile[];

  // UI state
  sidebarOpen: boolean;
  /** Hide the PresetsBar (Explain / Review / …). Toggleable from ChatInput. */
  presetsHidden: boolean;
  /** Workspace file picker open state. Lifted from ChatInput so the mobile
   *  nav drawer can trigger it without keeping a duplicate B button next to
   *  the textarea. */
  browseOpen: boolean;

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

  // RAG preferences (persisted in localStorage, survive Reset)
  ragEnabled: boolean;
  ragNamespace: string;

  // Per-message feedback (persisted in localStorage). Keyed by message timestamp.
  messageFeedback: Record<string, "up" | "down">;

  // Actions
  setWsConnected: (connected: boolean) => void;
  setSseMode: (mode: boolean) => void;
  applySnapshot: (snapshot: Snapshot) => void;
  applyEvent: (event: OrchestratorEvent) => void;
  addMessage: (msg: ChatMessage) => void;
  appendStreamChunk: (chunk: string) => void;
  finalizeStream: (data?: {
    output?: string;
    usage?: { output_tokens?: number; model?: string; cost_usd?: number };
    elapsed_s?: number;
    cost_usd?: number;
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
  setAttachedFiles: (files: AttachedFile[]) => void;
  addAttachedFile: (file: AttachedFile) => void;
  removeAttachedFileAt: (index: number) => void;
  clearAttachedFiles: () => void;
  setRagEnabled: (b: boolean) => void;
  setRagNamespace: (s: string) => void;
  /**
   * Toggle thumbs-up/down on an assistant message. Re-clicking the same kind
   * clears the rating. Persisted in localStorage so reload preserves it.
   */
  setMessageFeedback: (messageId: string, kind: "up" | "down") => void;
  /** Remove a message at a given index — used by the Regenerate action. */
  removeMessageAt: (index: number) => void;
  /**
   * Drop all messages with index >= `index`. Used by Regenerate to wipe the
   * previous user message + any intermediate system bubbles + the assistant
   * reply so the resend can repopulate them cleanly.
   */
  truncateMessagesFrom: (index: number) => void;
  setPresetsHidden: (hidden: boolean) => void;
  togglePresetsHidden: () => void;
  setBrowseOpen: (open: boolean) => void;
  /** Full Reset: graph + chat + attachments + conversation id (caller is
   *  responsible for the server-side DELETE /api/conversation/{id}).
   *  RAG preferences are intentionally NOT cleared — they are user settings. */
  reset: () => void;
}

const MAX_EVENTS = 500;
const MAX_ACTIVITY = 200;
const MAX_INTERACTIONS = 50;

/** localStorage key used to persist the active conversation id across reloads. */
export const STORAGE_KEY_CONVERSATION_ID = "ao_conv_id";

/** localStorage key used to persist the RAG enabled preference. */
export const STORAGE_KEY_RAG_ENABLED = "ao_rag_enabled";

/** localStorage key used to persist the RAG namespace preference. */
export const STORAGE_KEY_RAG_NAMESPACE = "ao_rag_namespace";

/** localStorage key used to persist per-message thumbs feedback. */
export const STORAGE_KEY_MESSAGE_FEEDBACK = "ao_msg_feedback";

/** Read the persisted conversation id from localStorage, or null. */
function readPersistedConversationId(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY_CONVERSATION_ID);
  } catch {
    return null;
  }
}

/** Persist (or clear) the conversation id in localStorage. */
function writePersistedConversationId(id: string | null): void {
  try {
    if (id) {
      window.localStorage.setItem(STORAGE_KEY_CONVERSATION_ID, id);
    } else {
      window.localStorage.removeItem(STORAGE_KEY_CONVERSATION_ID);
    }
  } catch {
    /* localStorage unavailable (private mode, SSR) — fail silently */
  }
}

/** Read the persisted RAG enabled flag from localStorage, or false. */
function readPersistedRagEnabled(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY_RAG_ENABLED) === "true";
  } catch {
    return false;
  }
}

/** Read the persisted RAG namespace from localStorage, or "shared". */
function readPersistedRagNamespace(): string {
  try {
    return window.localStorage.getItem(STORAGE_KEY_RAG_NAMESPACE) ?? "shared";
  } catch {
    return "shared";
  }
}

/** Read the persisted per-message feedback map, or an empty record. */
function readPersistedMessageFeedback(): Record<string, "up" | "down"> {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY_MESSAGE_FEEDBACK);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (parsed && typeof parsed === "object") {
      // Trust shape — we control both ends. Drop any non up/down values defensively.
      const out: Record<string, "up" | "down"> = {};
      for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
        if (v === "up" || v === "down") out[k] = v;
      }
      return out;
    }
    return {};
  } catch {
    return {};
  }
}

/** Persist the per-message feedback map. */
function writePersistedMessageFeedback(map: Record<string, "up" | "down">): void {
  try {
    window.localStorage.setItem(STORAGE_KEY_MESSAGE_FEEDBACK, JSON.stringify(map));
  } catch {
    /* fail silently */
  }
}

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
  // Hydrate conversationId from localStorage so a reload preserves the thread.
  conversationId: readPersistedConversationId(),
  lastTokenSpeed: 0,
  attachedFiles: [],

  // UI state — start with the right rail collapsed on small screens so the
  // mobile drawer (managed in CSS via compact breakpoint) doesn't open over the
  // chat on first load. SSR-safe: `window` may be undefined at hydration.
  sidebarOpen:
    typeof window === "undefined"
    || window.matchMedia(`(max-width: ${BP.compact}px)`).matches
      ? false
      : true,

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

  // RAG preferences (persisted, survive Reset)
  ragEnabled: readPersistedRagEnabled(),
  ragNamespace: readPersistedRagNamespace(),

  // Per-message thumbs feedback (persisted in localStorage)
  messageFeedback: readPersistedMessageFeedback(),

  // UI toggles (volatile — not persisted)
  presetsHidden: false,
  browseOpen: false,

  // --- Actions ---

  setWsConnected: (connected) => set({ wsConnected: connected }),

  setSseMode: (mode) => set({ sseMode: mode }),

  setPresetsHidden: (hidden) => set({ presetsHidden: hidden }),
  togglePresetsHidden: () => set((s) => ({ presetsHidden: !s.presetsHidden })),

  setBrowseOpen: (open) => set({ browseOpen: open }),

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
        elapsed_s: data?.elapsed_s,
        cost_usd: data?.cost_usd ?? data?.usage?.cost_usd,
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

  setConversationId: (id) => {
    writePersistedConversationId(id);
    set({ conversationId: id });
  },

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

  setAttachedFiles: (files) => set({ attachedFiles: files }),
  addAttachedFile: (file) =>
    set((state) => {
      // De-duplicate by path
      const filtered = state.attachedFiles.filter((f) => f.path !== file.path);
      return { attachedFiles: [...filtered, file] };
    }),
  removeAttachedFileAt: (index) =>
    set((state) => ({
      attachedFiles: state.attachedFiles.filter((_, i) => i !== index),
    })),
  clearAttachedFiles: () => set({ attachedFiles: [] }),

  setRagEnabled: (b) => {
    try {
      window.localStorage.setItem(STORAGE_KEY_RAG_ENABLED, String(b));
    } catch {
      /* fail silently */
    }
    set({ ragEnabled: b });
  },

  setRagNamespace: (s) => {
    try {
      window.localStorage.setItem(STORAGE_KEY_RAG_NAMESPACE, s);
    } catch {
      /* fail silently */
    }
    set({ ragNamespace: s });
  },

  setMessageFeedback: (messageId, kind) =>
    set((state) => {
      const current = state.messageFeedback[messageId];
      const next = { ...state.messageFeedback };
      if (current === kind) {
        delete next[messageId];
      } else {
        next[messageId] = kind;
      }
      writePersistedMessageFeedback(next);
      return { messageFeedback: next };
    }),

  removeMessageAt: (index) =>
    set((state) => ({
      messages: state.messages.filter((_, i) => i !== index),
    })),

  truncateMessagesFrom: (index) =>
    set((state) => ({
      messages: state.messages.slice(0, Math.max(0, index)),
    })),

  reset: () => {
    // Persist: clear localStorage too
    writePersistedConversationId(null);
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
      // Chat-related state
      messages: [],
      isStreaming: false,
      streamBuffer: "",
      conversationId: null,
      attachedFiles: [],
    });
  },
}));
