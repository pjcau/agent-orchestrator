import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PricingPanel } from "@/components/pricing/PricingPanel";

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

const PRICING_RESP = {
  data: {
    models: [
      { id: "openai/gpt-4o", name: "GPT-4o", input_per_m: 5.0, output_per_m: 15.0, is_free: false },
      { id: "meta/llama-3:free", name: "Llama 3 (free)", input_per_m: 0, output_per_m: 0, is_free: true },
      { id: "anthropic/claude-3-haiku", name: "Claude Haiku", input_per_m: 0.25, output_per_m: 1.25, is_free: false },
    ],
  },
};

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("PricingPanel", () => {
  beforeEach(() => {
    vi.mocked(apiClient.get).mockResolvedValue(PRICING_RESP);
  });

  it("renders all models after load", async () => {
    render(<PricingPanel />, { wrapper });
    await waitFor(() =>
      expect(screen.getByText("GPT-4o")).toBeInTheDocument()
    );
    expect(screen.getByText("Llama 3 (free)")).toBeInTheDocument();
    expect(screen.getByText("Claude Haiku")).toBeInTheDocument();
  });

  it("marks free models with free badge", async () => {
    render(<PricingPanel />, { wrapper });
    await waitFor(() =>
      expect(screen.getByText("Llama 3 (free)")).toBeInTheDocument()
    );
    const freeBadges = screen.getAllByText("free");
    // The free-badge span inside pricing-model plus "free" cost cells
    expect(freeBadges.length).toBeGreaterThan(0);
  });

  it("filters models by search input", async () => {
    const user = userEvent.setup();
    render(<PricingPanel />, { wrapper });
    await waitFor(() =>
      expect(screen.getByText("GPT-4o")).toBeInTheDocument()
    );

    const searchInput = screen.getByRole("searchbox");
    await user.type(searchInput, "haiku");

    expect(screen.getByText("Claude Haiku")).toBeInTheDocument();
    expect(screen.queryByText("GPT-4o")).not.toBeInTheDocument();
    expect(screen.queryByText("Llama 3 (free)")).not.toBeInTheDocument();
  });

  it("shows no models message when filter matches nothing", async () => {
    const user = userEvent.setup();
    render(<PricingPanel />, { wrapper });
    await waitFor(() =>
      expect(screen.getByText("GPT-4o")).toBeInTheDocument()
    );

    await user.type(screen.getByRole("searchbox"), "zzznomatch");

    expect(screen.getByText("No models found")).toBeInTheDocument();
  });
});
