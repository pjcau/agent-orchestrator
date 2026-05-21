import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/hooks/useWebSocketContext", () => ({
  useWebSocketContext: () => ({
    sendStreamPrompt: vi.fn().mockReturnValue(false),
    isStreamWsReady: () => false,
    stopStream: vi.fn(),
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
import { ChatPanel } from "@/components/chat/ChatPanel";
import { useAppStore } from "@/stores/useAppStore";

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

const agentsPayload = {
  agents: [
    { name: "team-lead", description: "", category: "general", provider: "" },
    { name: "diagnostician", description: "", category: "healthcare", provider: "" },
    { name: "medical-advisor", description: "", category: "healthcare", provider: "" },
  ],
  categories: {
    general: [{ name: "team-lead", description: "", category: "general", provider: "" }],
    healthcare: [
      { name: "diagnostician", description: "", category: "healthcare", provider: "" },
      { name: "medical-advisor", description: "", category: "healthcare", provider: "" },
    ],
  },
  skills: [],
};

describe("Agent picker — ChatPanel integration", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useAppStore.getState().reset();
    useAppStore.getState().clearMessages();
    vi.mocked(apiClient.post).mockClear();
    vi.mocked(apiClient.get).mockClear();

    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      const payloads: Record<string, unknown> = {
        "/api/models": {
          ollama: [],
          openrouter: [{ name: "openai/gpt-4o-mini", size: "small" }],
        },
        "/api/agents": agentsPayload,
        "/api/presets": { presets: [] },
        "/api/usage": {
          total_tokens: 0,
          total_cost_usd: 0,
          avg_speed: 0,
          total_requests: 0,
          db_connected: false,
        },
      };
      return Promise.resolve({ data: payloads[url] ?? {} });
    });
  });

  it("/api/agent/run uses the agent selected in the dropdown (not hardcoded team-lead)", async () => {
    const user = userEvent.setup();

    vi.mocked(apiClient.post).mockImplementation((url: string) => {
      if (url === "/api/conversation/new") {
        return Promise.resolve({ data: { conversation_id: "conv-agent-test" } });
      }
      return Promise.resolve({
        data: { success: true, output: "Hello from diagnostician" },
      });
    });

    render(<ChatPanel />, { wrapper });

    // Switch to Single Agent mode — this reveals the agent picker.
    const modeSelect = screen.getByTitle("Execution mode");
    await user.selectOptions(modeSelect, "agent");

    // The picker should appear and be populated from /api/agents.
    const agentSelect = await screen.findByTitle("Agent");
    await user.selectOptions(agentSelect, "diagnostician");

    const textarea = screen.getByPlaceholderText("Describe what you need...");
    await user.type(textarea, "Caso clinico");
    await user.keyboard("{Enter}");

    await waitFor(() => {
      const calls = vi.mocked(apiClient.post).mock.calls;
      const runCall = calls.find(([url]) => url === "/api/agent/run");
      expect(runCall).toBeDefined();
      const body = runCall![1] as Record<string, unknown>;
      expect(body.agent).toBe("diagnostician");
    });
  });

  it("defaults to team-lead when the user doesn't change the picker", async () => {
    const user = userEvent.setup();

    vi.mocked(apiClient.post).mockImplementation((url: string) => {
      if (url === "/api/conversation/new") {
        return Promise.resolve({ data: { conversation_id: "conv-default" } });
      }
      return Promise.resolve({ data: { success: true, output: "ok" } });
    });

    render(<ChatPanel />, { wrapper });

    const modeSelect = screen.getByTitle("Execution mode");
    await user.selectOptions(modeSelect, "agent");

    const textarea = screen.getByPlaceholderText("Describe what you need...");
    await user.type(textarea, "Plain run");
    await user.keyboard("{Enter}");

    await waitFor(() => {
      const calls = vi.mocked(apiClient.post).mock.calls;
      const runCall = calls.find(([url]) => url === "/api/agent/run");
      expect(runCall).toBeDefined();
      const body = runCall![1] as Record<string, unknown>;
      expect(body.agent).toBe("team-lead");
    });
  });

  it("hides the agent picker when not in single-agent mode", async () => {
    render(<ChatPanel />, { wrapper });

    // Default mode is multi-agent — picker must not be in the DOM.
    expect(screen.queryByTitle("Agent")).toBeNull();
  });
});
