import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

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

import { Sidebar } from "@/components/layout/Sidebar";
import { useAppStore } from "@/stores/useAppStore";
import type { OrchestratorEvent } from "@/api/types";

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

/** Helper that builds a minimal OrchestratorEvent */
function makeEvent(event_type: string, extra: Partial<OrchestratorEvent> = {}): OrchestratorEvent {
  return {
    event_type,
    timestamp: Date.now() / 1000,
    data: {},
    ...extra,
  };
}

describe("Knowledge event log", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useAppStore.getState().reset();
    useAppStore.getState().clearEvents();
  });

  it("knowledge.retrieved event appears with event-icon--knowledge class", () => {
    // Drive the store directly
    useAppStore.getState().applyEvent(
      makeEvent("knowledge.retrieved", { data: { namespace: "shared", hits: 5 } })
    );

    render(<Sidebar />, { wrapper });

    // The event row should show the event type text
    expect(screen.getByText("knowledge.retrieved")).toBeInTheDocument();

    // The icon element should have the knowledge CSS class
    const iconEl = document.querySelector(".event-icon--knowledge");
    expect(iconEl).not.toBeNull();
    expect(iconEl?.textContent?.trim()).toBe("K");
  });

  it("Knowledge filter keeps knowledge events visible", async () => {
    const user = userEvent.setup();

    useAppStore.getState().applyEvent(makeEvent("knowledge.retrieved"));
    useAppStore.getState().applyEvent(makeEvent("agent.spawn", { agent_name: "test-agent" }));

    render(<Sidebar />, { wrapper });

    // Initially both event types visible
    expect(screen.getByText("knowledge.retrieved")).toBeInTheDocument();
    expect(screen.getByText("agent.spawn")).toBeInTheDocument();

    // The logs-filter select has a "knowledge" option — locate it via CSS class
    const logsFilter = document.querySelector<HTMLSelectElement>("select.logs-filter");
    expect(logsFilter).not.toBeNull();
    await user.selectOptions(logsFilter!, "knowledge");

    // knowledge.retrieved should still be visible
    expect(screen.getByText("knowledge.retrieved")).toBeInTheDocument();
    // agent.spawn should be hidden
    expect(screen.queryByText("agent.spawn")).toBeNull();
  });

  it("Agent filter hides knowledge events", async () => {
    const user = userEvent.setup();

    useAppStore.getState().applyEvent(makeEvent("knowledge.ingested"));
    useAppStore.getState().applyEvent(makeEvent("agent.complete", { agent_name: "bot" }));

    render(<Sidebar />, { wrapper });

    const logsFilter = document.querySelector<HTMLSelectElement>("select.logs-filter");
    expect(logsFilter).not.toBeNull();
    await user.selectOptions(logsFilter!, "agent");

    expect(screen.queryByText("knowledge.ingested")).toBeNull();
    expect(screen.getByText("agent.complete")).toBeInTheDocument();
  });

  it("Sidebar renders the Knowledge filter option in the dropdown", () => {
    render(<Sidebar />, { wrapper });

    const logsFilter = document.querySelector<HTMLSelectElement>("select.logs-filter");
    expect(logsFilter).not.toBeNull();

    const knowledgeOption = Array.from(logsFilter!.options).find(
      (o) => o.value === "knowledge"
    );
    expect(knowledgeOption).toBeDefined();
    expect(knowledgeOption?.textContent).toBe("Knowledge");
  });
});
