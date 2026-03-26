import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StatusBadge } from "@/components/common/StatusBadge";
import { ThinkingAccordion, extractThinkingBlocks } from "@/components/common/ThinkingAccordion";
import { HITLOptions, HITLInterrupt } from "@/components/common/HITLButtons";

// Mock axios for HITLButtons
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

describe("StatusBadge", () => {
  it("renders idle status", () => {
    render(<StatusBadge status="idle" />);
    expect(screen.getByText("IDLE")).toBeInTheDocument();
  });

  it("renders running status", () => {
    render(<StatusBadge status="running" />);
    expect(screen.getByText("RUNNING")).toBeInTheDocument();
  });

  it("renders completed status", () => {
    render(<StatusBadge status="completed" />);
    expect(screen.getByText("DONE")).toBeInTheDocument();
  });

  it("renders failed status", () => {
    render(<StatusBadge status="failed" />);
    expect(screen.getByText("FAILED")).toBeInTheDocument();
  });

  it("applies correct CSS class for status", () => {
    const { container } = render(<StatusBadge status="running" />);
    expect(container.firstChild).toHaveClass("status-badge--running");
  });
});

describe("ThinkingAccordion", () => {
  it("renders summary text", () => {
    render(<ThinkingAccordion content="Some thought" />);
    expect(screen.getByText("Thinking...")).toBeInTheDocument();
  });

  it("renders content in pre element", () => {
    render(<ThinkingAccordion content="Deep thought here" />);
    expect(screen.getByText("Deep thought here")).toBeInTheDocument();
  });
});

describe("extractThinkingBlocks", () => {
  it("extracts thinking tags", () => {
    const text = "Before <thinking>inner thought</thinking> After";
    const { cleanText, thinkingBlocks } = extractThinkingBlocks(text);
    expect(thinkingBlocks).toHaveLength(1);
    expect(thinkingBlocks[0]).toBe("inner thought");
    expect(cleanText).toBe("Before  After");
  });

  it("extracts reasoning tags", () => {
    const text = "<reasoning>step 1\nstep 2</reasoning> Result";
    const { cleanText, thinkingBlocks } = extractThinkingBlocks(text);
    expect(thinkingBlocks).toHaveLength(1);
    expect(thinkingBlocks[0]).toContain("step 1");
    expect(cleanText).toContain("Result");
  });

  it("handles text with no thinking blocks", () => {
    const text = "Just regular text";
    const { cleanText, thinkingBlocks } = extractThinkingBlocks(text);
    expect(thinkingBlocks).toHaveLength(0);
    expect(cleanText).toBe("Just regular text");
  });

  it("handles multiple thinking blocks", () => {
    const text = "<thinking>first</thinking> middle <thinking>second</thinking>";
    const { thinkingBlocks } = extractThinkingBlocks(text);
    expect(thinkingBlocks).toHaveLength(2);
    expect(thinkingBlocks[0]).toBe("first");
    expect(thinkingBlocks[1]).toBe("second");
  });
});

describe("HITLOptions", () => {
  it("renders option buttons", () => {
    render(
      <HITLOptions
        runId="run-123"
        options={["Option A", "Option B", "Option C"]}
      />
    );

    expect(screen.getByText("Option A")).toBeInTheDocument();
    expect(screen.getByText("Option B")).toBeInTheDocument();
    expect(screen.getByText("Option C")).toBeInTheDocument();
  });

  it("disables other buttons after selection", async () => {
    const user = userEvent.setup();
    render(
      <HITLOptions
        runId="run-123"
        options={["Yes", "No"]}
      />
    );

    await user.click(screen.getByText("Yes"));

    const buttons = screen.getAllByRole("button");
    buttons.forEach((btn) => {
      expect(btn).toBeDisabled();
    });
  });
});

describe("HITLInterrupt", () => {
  it("renders Approve and Reject buttons", () => {
    render(<HITLInterrupt runId="run-456" />);
    expect(screen.getByText("Approve")).toBeInTheDocument();
    expect(screen.getByText("Reject")).toBeInTheDocument();
  });

  it("disables both buttons after clicking Approve", async () => {
    const user = userEvent.setup();
    render(<HITLInterrupt runId="run-456" />);

    await user.click(screen.getByText("Approve"));

    expect(screen.getByText("Approve")).toBeDisabled();
    expect(screen.getByText("Reject")).toBeDisabled();
  });

  it("disables both buttons after clicking Reject", async () => {
    const user = userEvent.setup();
    render(<HITLInterrupt runId="run-456" />);

    await user.click(screen.getByText("Reject"));

    expect(screen.getByText("Approve")).toBeDisabled();
    expect(screen.getByText("Reject")).toBeDisabled();
  });
});
