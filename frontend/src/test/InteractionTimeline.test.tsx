import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { InteractionTimeline } from "@/components/graph/InteractionTimeline";
import { useAppStore } from "@/stores/useAppStore";
import type { InteractionItem } from "@/api/types";

function setInteractions(items: InteractionItem[]) {
  useAppStore.setState({ interactions: items });
}

describe("InteractionTimeline", () => {
  it("shows empty state when there are no interactions", () => {
    setInteractions([]);
    render(<InteractionTimeline />);
    expect(screen.getByText("No interactions yet")).toBeInTheDocument();
  });

  it("renders interaction items", () => {
    setInteractions([
      {
        from: "team-lead",
        to: "data-analyst",
        desc: "delegated task",
        status: "running",
        time: Date.now(),
      },
    ]);
    render(<InteractionTimeline />);
    expect(screen.getByText("team-lead")).toBeInTheDocument();
    expect(screen.getByText("data-analyst")).toBeInTheDocument();
    expect(screen.getByText("delegated task")).toBeInTheDocument();
  });

  it("renders multiple items in order", () => {
    setInteractions([
      { from: "a", to: "b", desc: "first", status: "completed", time: 1 },
      { from: "b", to: "c", desc: "second", status: "failed", time: 2 },
    ]);
    render(<InteractionTimeline />);
    const items = screen.getAllByRole("log")[0].querySelectorAll(
      ".interaction-item"
    );
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveClass("interaction-item--completed");
    expect(items[1]).toHaveClass("interaction-item--failed");
  });

  it("applies correct status class to each item", () => {
    const statuses: InteractionItem["status"][] = [
      "pending",
      "running",
      "completed",
      "failed",
    ];
    setInteractions(
      statuses.map((s) => ({
        from: "x",
        to: "y",
        desc: s,
        status: s,
        time: Date.now(),
      }))
    );
    render(<InteractionTimeline />);
    statuses.forEach((s) => {
      const el = document.querySelector(`.interaction-item--${s}`);
      expect(el).not.toBeNull();
    });
  });

  it("truncates long descriptions at 50 chars", () => {
    const longDesc = "a".repeat(60);
    setInteractions([
      { from: "x", to: "y", desc: longDesc, status: "completed", time: Date.now() },
    ]);
    render(<InteractionTimeline />);
    // Should be truncated version, not the full 60-char string
    expect(screen.queryByText(longDesc)).not.toBeInTheDocument();
    expect(screen.getByText(`${"a".repeat(50)}...`)).toBeInTheDocument();
  });
});
