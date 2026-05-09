import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/hooks/useWebSocketContext", () => ({
  useWebSocketContext: () => ({
    sendStreamPrompt: vi.fn().mockReturnValue(true),
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

describe("ChatPanel — D 'Sent with N files' system bubble", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useAppStore.getState().reset();
    useAppStore.getState().clearMessages();
    vi.mocked(apiClient.get).mockReset();
    vi.mocked(apiClient.post).mockReset();

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

  it("emits a system 'Sent with …' bubble before the user message when files are attached", async () => {
    const user = userEvent.setup();

    // Set up an existing conversation id so handleSend skips auto-create
    useAppStore.getState().setConversationId("conv-1");
    useAppStore.getState().addAttachedFile({
      path: "report.pdf",
      content: "x",
      source: "upload",
      kind: "pdf",
      bytes: 2048,
    });

    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { success: true, output: "ok", elapsed_s: 0.1 },
    });

    render(<ChatPanel />, { wrapper });

    // Switch to Simple Prompt
    await waitFor(() =>
      expect(screen.getByTitle("Execution mode")).toBeInTheDocument()
    );
    await user.selectOptions(screen.getByTitle("Execution mode"), "prompt");

    const textarea = screen.getByPlaceholderText(/Describe what you need/i);
    await user.type(textarea, "summarise this");
    await user.click(screen.getByTitle("Send (Enter)"));

    await waitFor(() => {
      const messages = useAppStore.getState().messages;
      // Order: system 'Sent with...', then user, then assistant
      expect(messages.length).toBeGreaterThanOrEqual(2);
      expect(messages[0].role).toBe("system");
      expect(messages[0].content).toMatch(
        /Sent with 1 file: report\.pdf \(2\.0 KB\) \[upload\]/
      );
      expect(messages[1].role).toBe("user");
      expect(messages[1].content).toBe("summarise this");
    });
  });

  it("does NOT emit the bubble when no files are attached", async () => {
    const user = userEvent.setup();
    useAppStore.getState().setConversationId("conv-2");

    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { success: true, output: "ok", elapsed_s: 0.1 },
    });

    render(<ChatPanel />, { wrapper });

    await waitFor(() =>
      expect(screen.getByTitle("Execution mode")).toBeInTheDocument()
    );
    await user.selectOptions(screen.getByTitle("Execution mode"), "prompt");

    const textarea = screen.getByPlaceholderText(/Describe what you need/i);
    await user.type(textarea, "no files");
    await user.click(screen.getByTitle("Send (Enter)"));

    await waitFor(() => {
      const messages = useAppStore.getState().messages;
      expect(messages.find((m) => m.role === "user")).toBeTruthy();
      // None of the system messages should mention "Sent with"
      const sentMatches = messages.filter(
        (m) => m.role === "system" && typeof m.content === "string" &&
          (m.content as string).includes("Sent with")
      );
      expect(sentMatches).toHaveLength(0);
    });
  });
});
