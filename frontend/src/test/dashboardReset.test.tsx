import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Mock WebSocket context (DashboardPage → ChatPanel needs it)
vi.mock("@/hooks/useWebSocketContext", () => ({
  useWebSocketContext: () => ({
    sendStreamPrompt: vi.fn().mockReturnValue(true),
    isStreamWsReady: () => false,
  }),
  WebSocketProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock("@/api/client", () => ({
  default: {
    get: vi.fn().mockResolvedValue({ data: {} }),
    post: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
    defaults: { headers: {} },
  },
}));

import apiClient from "@/api/client";
import { DashboardPage } from "@/pages/DashboardPage";
import { useAppStore } from "@/stores/useAppStore";

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("DashboardPage — B full Reset", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useAppStore.getState().reset();
    useAppStore.getState().clearMessages();
    vi.mocked(apiClient.delete).mockClear();
    vi.mocked(apiClient.post).mockClear();
    vi.mocked(apiClient.get).mockReset();

    // Default GETs — return shape-appropriate empty payloads for every URL
    // DashboardPage children query at boot.
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      const empty: Record<string, unknown> = {
        "/api/agents": { agents: [], categories: {} },
        "/api/models": { ollama: [], openrouter: [] },
        "/api/sandbox/status": {
          enabled: false,
          active_sessions: 0,
          max_concurrent: 0,
          session_ids: [],
          allocated_ports: {},
        },
        "/api/usage": {
          total_tokens: 0,
          total_cost_usd: 0,
          avg_speed: 0,
          total_requests: 0,
          db_connected: false,
        },
        "/api/cache/stats": {
          cache: { hits: 0, misses: 0, hit_rate: 0, evictions: 0 },
        },
        "/api/mcp/tools": { tools: [], count: 0 },
        "/api/compaction/stats": {
          summarization_count: 0,
          tokens_saved: 0,
          messages_compacted: 0,
          last_compaction_ratio: 0,
        },
        "/api/presets": { presets: [] },
      };
      return Promise.resolve({
        data: empty[url] ?? {},
      }) as ReturnType<typeof apiClient.get>;
    });
  });

  it("clicking Reset clears chat + attachedFiles + conversationId and DELETEs the conversation", async () => {
    const user = userEvent.setup();
    const store = useAppStore.getState();

    // Seed state as if a conversation were in progress
    store.setConversationId("conv-to-delete");
    store.addMessage({ role: "user", content: "hello", timestamp: 1 });
    store.addMessage({ role: "assistant", content: "world", timestamp: 2 });
    store.addAttachedFile({ path: "doc.md", content: "X" });

    expect(window.localStorage.getItem("ao_conv_id")).toBe("conv-to-delete");

    render(<DashboardPage />, { wrapper });

    await user.click(screen.getByTitle("Reset all state"));

    // Server-side calls
    await vi.waitFor(() => {
      expect(vi.mocked(apiClient.delete)).toHaveBeenCalledWith(
        "/api/conversation/conv-to-delete"
      );
      expect(vi.mocked(apiClient.post)).toHaveBeenCalledWith(
        "/api/graph/reset"
      );
    });

    // Client-side state
    const after = useAppStore.getState();
    expect(after.conversationId).toBeNull();
    expect(after.messages).toEqual([]);
    expect(after.attachedFiles).toEqual([]);
    expect(window.localStorage.getItem("ao_conv_id")).toBeNull();
  });

  it("Reset still clears UI even if DELETE conversation fails", async () => {
    const user = userEvent.setup();
    const store = useAppStore.getState();
    store.setConversationId("conv-broken");
    store.addMessage({ role: "user", content: "hi", timestamp: 1 });

    vi.mocked(apiClient.delete).mockRejectedValueOnce(new Error("boom"));

    render(<DashboardPage />, { wrapper });
    await user.click(screen.getByTitle("Reset all state"));

    await vi.waitFor(() => {
      expect(useAppStore.getState().messages).toEqual([]);
      expect(useAppStore.getState().conversationId).toBeNull();
    });
  });

  it("Reset without an active conversation skips the DELETE call", async () => {
    const user = userEvent.setup();
    // No conversationId set
    render(<DashboardPage />, { wrapper });
    await user.click(screen.getByTitle("Reset all state"));

    await vi.waitFor(() => {
      expect(vi.mocked(apiClient.post)).toHaveBeenCalledWith("/api/graph/reset");
    });
    expect(vi.mocked(apiClient.delete)).not.toHaveBeenCalled();
  });
});
