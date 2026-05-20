import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ComparePanel } from "@/components/compare/ComparePanel";
import { useAppStore } from "@/stores/useAppStore";

vi.mock("@/api/client", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
    defaults: { headers: {} },
  },
}));

import apiClient from "@/api/client";

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

const MODELS_RESP = {
  data: {
    openrouter: [
      { name: "openai/gpt-4o", size: "large" },
      { name: "anthropic/claude-3-haiku", size: "small" },
    ],
    ollama: [],
  },
};

describe("ComparePanel", () => {
  beforeEach(() => {
    // Seed store with a user message
    useAppStore.setState({
      messages: [{ role: "user", content: "What is the capital of France?", timestamp: Date.now() }],
    });

    vi.mocked(apiClient.get).mockResolvedValue(MODELS_RESP);
    vi.mocked(apiClient.post).mockImplementation(async (_url, body) => {
      const req = body as { model: string };
      return {
        data: {
          success: true,
          output: `Response from ${req.model}`,
          elapsed_s: 2,
          usage: { output_tokens: 50 },
        },
      };
    });
  });

  it("renders model selects and Go button", async () => {
    render(<ComparePanel />, { wrapper });
    await waitFor(() =>
      expect(screen.getAllByRole("combobox").length).toBeGreaterThanOrEqual(2)
    );
    expect(screen.getByRole("button", { name: /go/i })).toBeInTheDocument();
  });

  it("shows both model outputs side by side after clicking Go", async () => {
    const user = userEvent.setup();
    render(<ComparePanel />, { wrapper });

    // Wait for models to load
    await waitFor(() =>
      expect(
        screen.getAllByRole("option", { name: /openai\/gpt-4o/i }).length
      ).toBeGreaterThan(0)
    );

    const [selectA, selectB] = screen.getAllByRole("combobox");
    await user.selectOptions(selectA, "openai/gpt-4o");
    await user.selectOptions(selectB, "anthropic/claude-3-haiku");

    await user.click(screen.getByRole("button", { name: /go/i }));

    await waitFor(() =>
      expect(
        screen.getByText(/Response from openai\/gpt-4o/i)
      ).toBeInTheDocument()
    );
    expect(
      screen.getByText(/Response from anthropic\/claude-3-haiku/i)
    ).toBeInTheDocument();
  });

  it("shows error when no user message in store", async () => {
    useAppStore.setState({ messages: [] });
    const user = userEvent.setup();
    render(<ComparePanel />, { wrapper });

    await waitFor(() =>
      expect(
        screen.getAllByRole("option", { name: /openai\/gpt-4o/i }).length
      ).toBeGreaterThan(0)
    );

    const [selectA, selectB] = screen.getAllByRole("combobox");
    await user.selectOptions(selectA, "openai/gpt-4o");
    await user.selectOptions(selectB, "anthropic/claude-3-haiku");

    await user.click(screen.getByRole("button", { name: /go/i }));

    await waitFor(() =>
      expect(screen.getByText(/Send a message first/i)).toBeInTheDocument()
    );
  });
});
