import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// --- Mocks ---

vi.mock("@/hooks/useWebSocketContext", () => ({
  useWebSocketContext: () => ({
    sendStreamPrompt: vi.fn().mockReturnValue(false),
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
import { ChatPanel } from "@/components/chat/ChatPanel";
import { useAppStore } from "@/stores/useAppStore";

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("RAG toggle — ChatPanel integration", () => {
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

  it("POST to /api/prompt carries rag_enabled and rag_namespace when RAG is on", async () => {
    const user = userEvent.setup();

    // Enable RAG in the store (simulates user having toggled it on previously)
    useAppStore.getState().setRagEnabled(true);
    useAppStore.getState().setRagNamespace("shared");

    // Mock POST /api/conversation/new (auto-created on first send)
    vi.mocked(apiClient.post).mockImplementation((url: string) => {
      if (url === "/api/conversation/new") {
        return Promise.resolve({ data: { conversation_id: "conv-rag-test" } });
      }
      // /api/prompt — return a success response with a rag field
      return Promise.resolve({
        data: {
          success: true,
          output: "Hello from model",
          rag: {
            namespace: "shared",
            hits: 3,
            embedding_model: "hash-md5",
            scores: [0.9, 0.8, 0.7],
          },
        },
      });
    });

    render(<ChatPanel />, { wrapper });

    // Switch to Simple Prompt mode so we hit /api/prompt (not team/agent)
    const modeSelect = screen.getByTitle("Execution mode");
    await user.selectOptions(modeSelect, "prompt");

    // Type a message and send
    const textarea = screen.getByPlaceholderText("Describe what you need...");
    await user.type(textarea, "What is RAG?");
    await user.keyboard("{Enter}");

    await waitFor(() => {
      const calls = vi.mocked(apiClient.post).mock.calls;
      const promptCall = calls.find(([url]) => url === "/api/prompt");
      expect(promptCall).toBeDefined();
      const body = promptCall![1] as Record<string, unknown>;
      expect(body.rag_enabled).toBe(true);
      expect(body.rag_namespace).toBe("shared");
    });
  });

  it("renders a RAG system bubble before the assistant reply", async () => {
    const user = userEvent.setup();

    useAppStore.getState().setRagEnabled(true);
    useAppStore.getState().setRagNamespace("shared");

    vi.mocked(apiClient.post).mockImplementation((url: string) => {
      if (url === "/api/conversation/new") {
        return Promise.resolve({ data: { conversation_id: "conv-rag-bubble" } });
      }
      return Promise.resolve({
        data: {
          success: true,
          output: "Assistant reply",
          rag: {
            namespace: "shared",
            hits: 3,
            embedding_model: "hash-md5",
            scores: [0.9, 0.8, 0.7],
          },
        },
      });
    });

    render(<ChatPanel />, { wrapper });

    const modeSelect = screen.getByTitle("Execution mode");
    await user.selectOptions(modeSelect, "prompt");

    const textarea = screen.getByPlaceholderText("Describe what you need...");
    await user.type(textarea, "Tell me about RAG");
    await user.keyboard("{Enter}");

    await waitFor(() => {
      // The RAG system bubble must appear
      expect(screen.getByText(/RAG: shared · 3 chunk/)).toBeInTheDocument();
    });
  });

  it("does NOT include rag_enabled in POST when RAG is off", async () => {
    const user = userEvent.setup();

    // Ensure RAG is off
    useAppStore.getState().setRagEnabled(false);

    vi.mocked(apiClient.post).mockImplementation((url: string) => {
      if (url === "/api/conversation/new") {
        return Promise.resolve({ data: { conversation_id: "conv-no-rag" } });
      }
      return Promise.resolve({ data: { success: true, output: "OK" } });
    });

    render(<ChatPanel />, { wrapper });

    const modeSelect = screen.getByTitle("Execution mode");
    await user.selectOptions(modeSelect, "prompt");

    const textarea = screen.getByPlaceholderText("Describe what you need...");
    await user.type(textarea, "No RAG please");
    await user.keyboard("{Enter}");

    await waitFor(() => {
      const calls = vi.mocked(apiClient.post).mock.calls;
      const promptCall = calls.find(([url]) => url === "/api/prompt");
      expect(promptCall).toBeDefined();
      const body = promptCall![1] as Record<string, unknown>;
      expect(body.rag_enabled).toBeUndefined();
    });
  });
});
