/**
 * Regression test: when the user submits while the mic is still listening,
 * `handleSend` must call `stopListening()`. Otherwise the recognizer keeps
 * running, appends late "final" chunks into the now-empty textarea, and the
 * red "listening" indicator stays on — which users perceive as a stuck mic.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Stub useSpeechRecognition: we control `isListening` from the test and
// expose the `stop` spy so we can assert it was called.
// ---------------------------------------------------------------------------

const stopSpy = vi.fn();
let listeningState = false;

vi.mock("@/hooks/useSpeechRecognition", () => ({
  useSpeechRecognition: () => ({
    isSupported: true,
    isListening: listeningState,
    interim: "",
    error: null,
    start: vi.fn(),
    stop: stopSpy,
    reset: vi.fn(),
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

import { ChatInput } from "@/components/chat/ChatInput";

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

const baseModels = {
  ollama: [],
  openrouter: [{ name: "openai/gpt-4o", size: "" }],
};

describe("ChatInput — mic auto-stop on send", () => {
  beforeEach(() => {
    stopSpy.mockClear();
  });

  it("stops the mic when the user clicks Send while listening", async () => {
    listeningState = true;
    const onSend = vi.fn();

    render(
      <ChatInput models={baseModels} isDisabled={false} onSend={onSend} onNewChat={vi.fn()} />,
      { wrapper }
    );

    const textarea = screen.getByPlaceholderText(/Describe what you need/i);
    const user = userEvent.setup();
    await user.type(textarea, "hello via mic");
    await user.click(screen.getByTitle(/Send/i));

    expect(stopSpy).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend.mock.calls[0][0].text).toBe("hello via mic");
  });

  it("does NOT call stop when the mic is already off", async () => {
    listeningState = false;
    const onSend = vi.fn();

    render(
      <ChatInput models={baseModels} isDisabled={false} onSend={onSend} onNewChat={vi.fn()} />,
      { wrapper }
    );

    const textarea = screen.getByPlaceholderText(/Describe what you need/i);
    const user = userEvent.setup();
    await user.type(textarea, "hello typed");
    await user.click(screen.getByTitle(/Send/i));

    expect(stopSpy).not.toHaveBeenCalled();
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("also stops the mic when the user submits with Enter", async () => {
    listeningState = true;
    const onSend = vi.fn();

    render(
      <ChatInput models={baseModels} isDisabled={false} onSend={onSend} onNewChat={vi.fn()} />,
      { wrapper }
    );

    const textarea = screen.getByPlaceholderText(/Describe what you need/i);
    const user = userEvent.setup();
    await user.type(textarea, "via enter{Enter}");

    expect(stopSpy).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledTimes(1);
  });
});
