import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Mock the WebSocket context so ChatPanel doesn't need a real WS.
vi.mock("@/hooks/useWebSocketContext", () => ({
  useWebSocketContext: () => ({
    sendStreamPrompt: vi.fn().mockReturnValue(true),
    isStreamWsReady: () => false, // force HTTP path
  }),
}));

// Mock apiClient before importing the panel.
vi.mock("@/api/client", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
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

describe("ChatPanel — A2 conversation auto-create", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useAppStore.getState().reset();
    useAppStore.getState().clearMessages();
    useAppStore.getState().setConversationId(null);
    vi.mocked(apiClient.get).mockReset();
    vi.mocked(apiClient.post).mockReset();

    // /api/models — invoked by ChatInput's useModels()
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url === "/api/models") {
        return Promise.resolve({
          data: {
            ollama: [],
            openrouter: [{ name: "openai/gpt-4o", size: "" }],
          },
        }) as ReturnType<typeof apiClient.get>;
      }
      if (url === "/api/presets") {
        return Promise.resolve({ data: { presets: [] } }) as ReturnType<typeof apiClient.get>;
      }
      return Promise.resolve({ data: {} }) as ReturnType<typeof apiClient.get>;
    });
  });

  it("creates a conversation on first send and reuses it on the second", async () => {
    const user = userEvent.setup();

    // First call: /api/conversation/new → returns id; second call: /api/prompt
    vi.mocked(apiClient.post)
      .mockResolvedValueOnce({ data: { conversation_id: "conv-xyz" } })
      .mockResolvedValueOnce({
        data: { success: true, output: "first reply", elapsed_s: 0.1 },
      })
      .mockResolvedValueOnce({
        data: { success: true, output: "second reply", elapsed_s: 0.1 },
      });

    render(<ChatPanel />, { wrapper });

    // Switch to "Simple Prompt" mode — multi-agent is the default
    await waitFor(() =>
      expect(screen.getByTitle("Execution mode")).toBeInTheDocument()
    );
    const modeSelect = screen.getByTitle("Execution mode") as HTMLSelectElement;
    await user.selectOptions(modeSelect, "prompt");

    // Type and send
    const textarea = screen.getByPlaceholderText(/Describe what you need/i);
    await user.type(textarea, "first message");
    await user.click(screen.getByTitle("Send (Enter)"));

    // Verify POSTs in order: /api/conversation/new, then /api/prompt with conv id
    await waitFor(() => {
      const calls = vi.mocked(apiClient.post).mock.calls;
      expect(calls[0][0]).toBe("/api/conversation/new");
      expect(calls[1][0]).toBe("/api/prompt");
      expect((calls[1][1] as { conversation_id: string }).conversation_id).toBe(
        "conv-xyz"
      );
    });

    // Store should now have the conversation id
    expect(useAppStore.getState().conversationId).toBe("conv-xyz");
    expect(window.localStorage.getItem("ao_conv_id")).toBe("conv-xyz");

    // Send a second message — should NOT call /api/conversation/new again
    await user.type(textarea, "second message");
    await user.click(screen.getByTitle("Send (Enter)"));

    await waitFor(() => {
      const calls = vi.mocked(apiClient.post).mock.calls;
      // Calls so far: [conv/new, prompt#1, prompt#2]
      expect(calls).toHaveLength(3);
      expect(calls[2][0]).toBe("/api/prompt");
      expect((calls[2][1] as { conversation_id: string }).conversation_id).toBe(
        "conv-xyz"
      );
    });
  });

  it("reuses existing conversation id without calling /api/conversation/new", async () => {
    const user = userEvent.setup();
    useAppStore.getState().setConversationId("preexisting-id");

    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { success: true, output: "reply", elapsed_s: 0.1 },
    });

    render(<ChatPanel />, { wrapper });

    await waitFor(() =>
      expect(screen.getByTitle("Execution mode")).toBeInTheDocument()
    );
    const modeSelect = screen.getByTitle("Execution mode") as HTMLSelectElement;
    await user.selectOptions(modeSelect, "prompt");

    const textarea = screen.getByPlaceholderText(/Describe what you need/i);
    await user.type(textarea, "hello");
    await user.click(screen.getByTitle("Send (Enter)"));

    await waitFor(() => {
      const calls = vi.mocked(apiClient.post).mock.calls;
      expect(calls).toHaveLength(1);
      expect(calls[0][0]).toBe("/api/prompt");
      expect((calls[0][1] as { conversation_id: string }).conversation_id).toBe(
        "preexisting-id"
      );
    });
  });
});
