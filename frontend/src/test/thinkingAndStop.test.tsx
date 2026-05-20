import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const stopStream = vi.fn().mockReturnValue(true);

vi.mock("@/hooks/useWebSocketContext", () => ({
  useWebSocketContext: () => ({
    sendStreamPrompt: vi.fn().mockReturnValue(true),
    stopStream,
    isStreamWsReady: () => false,
  }),
}));

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

describe("ChatPanel — thinking indicator and Stop button", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useAppStore.getState().reset();
    useAppStore.getState().clearMessages();
    stopStream.mockClear();
    vi.mocked(apiClient.get).mockReset();

    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url === "/api/models") {
        return Promise.resolve({
          data: { ollama: [], openrouter: [{ name: "openai/gpt-4o", size: "" }] },
        }) as ReturnType<typeof apiClient.get>;
      }
      if (url === "/api/presets") {
        return Promise.resolve({ data: { presets: [] } }) as ReturnType<typeof apiClient.get>;
      }
      return Promise.resolve({ data: {} }) as ReturnType<typeof apiClient.get>;
    });
  });

  it("shows the thinking indicator when streaming with empty buffer", () => {
    useAppStore.setState({ isStreaming: true, streamBuffer: "" });

    render(<ChatPanel />, { wrapper });

    const indicator = screen.getByRole("status", {
      name: /assistant is thinking/i,
    });
    expect(indicator).toBeInTheDocument();
  });

  it("hides the thinking indicator once stream tokens arrive", () => {
    useAppStore.setState({ isStreaming: true, streamBuffer: "Hello" });

    render(<ChatPanel />, { wrapper });

    expect(
      screen.queryByRole("status", { name: /assistant is thinking/i })
    ).not.toBeInTheDocument();
  });

  it("renders the Stop button only while streaming", () => {
    useAppStore.setState({ isStreaming: false, streamBuffer: "" });

    const { rerender } = render(<ChatPanel />, { wrapper });
    expect(screen.queryByRole("button", { name: /stop generation/i })).not.toBeInTheDocument();

    useAppStore.setState({ isStreaming: true });
    rerender(<ChatPanel />);
    expect(screen.getByRole("button", { name: /stop generation/i })).toBeInTheDocument();
  });

  it("clicking Stop closes the stream, clears state, and adds a system message", async () => {
    const user = userEvent.setup();
    useAppStore.setState({ isStreaming: true, streamBuffer: "partial" });

    render(<ChatPanel />, { wrapper });

    await user.click(screen.getByRole("button", { name: /stop generation/i }));

    expect(stopStream).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      const state = useAppStore.getState();
      expect(state.isStreaming).toBe(false);
      expect(state.streamBuffer).toBe("");
    });

    const messages = useAppStore.getState().messages;
    expect(messages.some((m) => m.role === "system" && /stopped by user/i.test(String(m.content)))).toBe(true);
  });
});
