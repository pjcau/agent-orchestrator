import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChatMessageItem } from "@/components/chat/ChatMessage";
import type { ChatMessage, AssistantContent } from "@/api/types";

// MarkdownRenderer needs no async data and HITLButtons hit the API client.
// Stub the API client so HITL components don't crash when imported.
vi.mock("@/api/client", () => ({
  default: {
    post: vi.fn().mockResolvedValue({ data: {} }),
    get: vi.fn().mockResolvedValue({ data: {} }),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
    defaults: { headers: {} },
  },
}));

describe("ChatMessage meta footer (assistant bubble)", () => {
  it("shows model, elapsed and cost for a plain assistant response", () => {
    const msg: ChatMessage = {
      role: "assistant",
      content: "Hello world",
      model: "inclusionai/ling-2.6-flash",
      elapsed_s: 1.56,
      cost_usd: 0.0023,
      timestamp: Date.now(),
    };
    render(<ChatMessageItem message={msg} />);
    expect(screen.getByText("inclusionai/ling-2.6-flash")).toBeInTheDocument();
    expect(screen.getByText("1.6s")).toBeInTheDocument();
    expect(screen.getByText("$0.0023")).toBeInTheDocument();
  });

  it("renders '<$0.0001' instead of '$0' for sub-cent costs", () => {
    const msg: ChatMessage = {
      role: "assistant",
      content: "Hi",
      model: "ling",
      elapsed_s: 0.4,
      cost_usd: 1e-6,
      timestamp: Date.now(),
    };
    render(<ChatMessageItem message={msg} />);
    expect(screen.getByText("<$0.0001")).toBeInTheDocument();
    // elapsed under 1s shows as ms
    expect(screen.getByText("400ms")).toBeInTheDocument();
  });

  it("renders '$0' for a free/local model with zero cost", () => {
    const msg: ChatMessage = {
      role: "assistant",
      content: "Yes",
      model: "ollama:llama",
      elapsed_s: 2,
      cost_usd: 0,
      timestamp: Date.now(),
    };
    render(<ChatMessageItem message={msg} />);
    expect(screen.getByText("$0")).toBeInTheDocument();
    expect(screen.getByText("2.0s")).toBeInTheDocument();
  });

  it("hides cost span when cost_usd is undefined", () => {
    const msg: ChatMessage = {
      role: "assistant",
      content: "no cost field",
      model: "x",
      elapsed_s: 1,
      timestamp: Date.now(),
    };
    const { container } = render(<ChatMessageItem message={msg} />);
    expect(container.querySelector(".chat-bubble__meta-cost")).toBeNull();
    expect(container.querySelector(".chat-bubble__meta-time")).not.toBeNull();
  });

  it("omits the whole meta footer when no metadata is set", () => {
    const msg: ChatMessage = {
      role: "assistant",
      content: "nada",
      timestamp: Date.now(),
    };
    const { container } = render(<ChatMessageItem message={msg} />);
    expect(container.querySelector(".chat-bubble__meta")).toBeNull();
  });

  it("uses usage.cost_usd in the agent-step footer when agent_costs is empty", () => {
    const content: AssistantContent = {
      steps: [{ node: "agent", output: "ok" }],
      usage: { output_tokens: 12, model: "ling", cost_usd: 0.005 },
      elapsed_s: 3.2,
    };
    const msg: ChatMessage = {
      role: "assistant",
      content,
      timestamp: Date.now(),
    };
    const { container } = render(<ChatMessageItem message={msg} />);
    const usage = container.querySelector(".chat-usage");
    expect(usage).not.toBeNull();
    expect(usage!.textContent).toContain("$0.005");
    expect(usage!.textContent).toContain("ling");
  });
});
