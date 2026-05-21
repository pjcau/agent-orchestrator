import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PresetsBar } from "@/components/prompts/PresetsBar";
import { useAppStore } from "@/stores/useAppStore";

// Mock apiClient so no real network calls are made
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

describe("PresetsBar", () => {
  beforeEach(() => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        presets: [
          { label: "Summarise", prompt: "Summarise: {context}", graph: "chat", icon: "S" },
          { label: "Translate", prompt: "Translate this: {context}", graph: "chat", icon: "T" },
          { label: "Hello", prompt: "Hello world", graph: "chat", icon: "H" },
        ],
      },
    });
    // Reset the UI toggle so tests that don't touch it see the default state.
    useAppStore.setState({ presetsHidden: false });
  });

  it("renders preset buttons after load", async () => {
    render(<PresetsBar onApply={vi.fn()} fileContext="" />, { wrapper });
    await waitFor(() =>
      expect(screen.getByText("Summarise")).toBeInTheDocument()
    );
    expect(screen.getByText("Translate")).toBeInTheDocument();
    expect(screen.getByText("Hello")).toBeInTheDocument();
  });

  it("calls onApply with substituted text when fileContext is present", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(<PresetsBar onApply={onApply} fileContext="my file content" />, {
      wrapper,
    });
    await waitFor(() =>
      expect(screen.getByText("Summarise")).toBeInTheDocument()
    );
    await user.click(screen.getByText("Summarise"));
    expect(onApply).toHaveBeenCalledWith("Summarise: my file content");
  });

  it("shows inline notice and does NOT call onApply when fileContext empty and prompt needs context", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(<PresetsBar onApply={onApply} fileContext="" />, { wrapper });
    await waitFor(() =>
      expect(screen.getByText("Summarise")).toBeInTheDocument()
    );
    await user.click(screen.getByText("Summarise"));
    expect(onApply).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent("Attach a file first");
  });

  it("calls onApply for a preset without {context} even without fileContext", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(<PresetsBar onApply={onApply} fileContext="" />, { wrapper });
    await waitFor(() =>
      expect(screen.getByText("Hello")).toBeInTheDocument()
    );
    await user.click(screen.getByText("Hello"));
    expect(onApply).toHaveBeenCalledWith("Hello world");
  });

  it("renders nothing when presetsHidden is true", () => {
    useAppStore.setState({ presetsHidden: true });
    const { container } = render(
      <PresetsBar onApply={vi.fn()} fileContext="" />,
      { wrapper }
    );
    // No preset buttons rendered at all
    expect(container.querySelector(".presets-bar")).toBeNull();
    expect(screen.queryByText("Summarise")).toBeNull();
  });
});
