import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
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

import { ChatInput, formatBytes } from "@/components/chat/ChatInput";
import { useAppStore } from "@/stores/useAppStore";

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

const baseModels = {
  ollama: [],
  openrouter: [{ name: "openai/gpt-4o", size: "" }],
};

describe("ChatInput — D file context transparency", () => {
  beforeEach(() => {
    useAppStore.getState().reset();
    useAppStore.getState().clearAttachedFiles();
  });

  describe("formatBytes", () => {
    it("renders bytes / KB / MB at appropriate boundaries", () => {
      expect(formatBytes(0)).toBe("");
      expect(formatBytes(undefined)).toBe("");
      expect(formatBytes(800)).toBe("800 B");
      expect(formatBytes(2048)).toBe("2.0 KB");
      expect(formatBytes(3 * 1024 * 1024)).toBe("3.0 MB");
    });
  });

  it("renders kind badge, size, source data attribute, and truncation indicator", () => {
    useAppStore.getState().addAttachedFile({
      path: "report.pdf",
      content: "# Report",
      source: "upload",
      kind: "pdf",
      bytes: 4096,
    });
    useAppStore.getState().addAttachedFile({
      path: "data.csv",
      content: "a,b\n1,2",
      source: "workspace",
      kind: "csv",
      bytes: 100 * 1024,
      truncated: true,
    });

    render(
      <ChatInput
        models={baseModels}
        isDisabled={false}
        onSend={vi.fn()}
        onNewChat={vi.fn()}
      />,
      { wrapper }
    );

    // Two badges (PDF, CSV)
    expect(screen.getByText("PDF")).toBeInTheDocument();
    expect(screen.getByText("CSV")).toBeInTheDocument();

    // Sizes formatted
    expect(screen.getByText("4.0 KB")).toBeInTheDocument();
    expect(screen.getByText("100.0 KB")).toBeInTheDocument();

    // Source distinguishes workspace from upload (data attribute)
    const csvChip = screen.getByText("data.csv").closest("[data-source]");
    expect(csvChip).toHaveAttribute("data-source", "workspace");
    const pdfChip = screen.getByText("report.pdf").closest("[data-source]");
    expect(pdfChip).toHaveAttribute("data-source", "upload");

    // Truncation indicator only on the truncated one
    const warnings = screen.getAllByLabelText("truncated");
    expect(warnings).toHaveLength(1);
    expect(warnings[0].closest("[data-source]")).toBe(csvChip);
  });

  it("falls back to extension-based badge when kind is missing", () => {
    useAppStore.getState().addAttachedFile({
      path: "diagram.svg",
      content: "<svg/>",
      source: "upload",
    });

    render(
      <ChatInput
        models={baseModels}
        isDisabled={false}
        onSend={vi.fn()}
        onNewChat={vi.fn()}
      />,
      { wrapper }
    );

    expect(screen.getByText("SVG")).toBeInTheDocument();
  });

  it("renders IMG badge for image kind (OCR pipeline output)", () => {
    useAppStore.getState().addAttachedFile({
      path: "screenshot.png",
      content: "OCR text",
      source: "upload",
      kind: "image",
      bytes: 12000,
    });

    render(
      <ChatInput
        models={baseModels}
        isDisabled={false}
        onSend={vi.fn()}
        onNewChat={vi.fn()}
      />,
      { wrapper }
    );

    expect(screen.getByText("IMG")).toBeInTheDocument();
  });
});
