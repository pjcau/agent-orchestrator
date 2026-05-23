import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Mock WS context (ChatPanel inside ChatInput's tree needs it via DashboardPage —
// but here we render ChatInput directly so we don't need WS at all).

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
import { ChatInput } from "@/components/chat/ChatInput";
import { useAppStore } from "@/stores/useAppStore";

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

const baseModels = {
  ollama: [],
  openrouter: [{ name: "openai/gpt-4o", size: "" }],
};

describe("ChatInput — C2 file upload via /api/upload", () => {
  beforeEach(() => {
    useAppStore.getState().reset();
    useAppStore.getState().clearAttachedFiles();
    vi.mocked(apiClient.post).mockReset();
  });

  /**
   * Simulate a file drop on the hidden input the component creates on click.
   * createElement('input').click() is intercepted in jsdom; instead we wrap
   * the global createElement to capture the input and dispatch a change.
   */
  async function simulateFileSelect(file: File) {
    const realCreate = document.createElement.bind(document);
    let captured: HTMLInputElement | null = null;
    const spy = vi
      .spyOn(document, "createElement")
      .mockImplementation((tag: string) => {
        const el = realCreate(tag);
        if (tag === "input") {
          captured = el as HTMLInputElement;
          // Stub click() so we can synthesise the change event
          (captured as HTMLInputElement).click = () => {
            // Drop the test file into files (jsdom does not allow direct
            // assignment, but defineProperty works)
            Object.defineProperty(captured, "files", {
              value: [file],
              configurable: true,
            });
            captured!.onchange?.(new Event("change") as unknown as Event);
          };
        }
        return el;
      });

    const user = userEvent.setup();
    await user.click(
      screen.getByTitle(/Upload local file/i)
    );
    spy.mockRestore();
  }

  it("POSTs /api/upload with FormData and adds resulting markdown to attachedFiles", async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        success: true,
        filename: "report.pdf",
        file_type: "pdf",
        markdown_content: "# Report\n\nHello.",
        markdown_path: "/tmp/report.md",
        page_count: 1,
      },
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

    const file = new File(["dummy"], "report.pdf", { type: "application/pdf" });
    await simulateFileSelect(file);

    await waitFor(() => {
      expect(vi.mocked(apiClient.post)).toHaveBeenCalledTimes(1);
    });
    const [url, body, config] = vi.mocked(apiClient.post).mock.calls[0];
    expect(url).toBe("/api/upload");
    expect(body).toBeInstanceOf(FormData);
    // We must NOT set Content-Type manually — the browser auto-generates
    // `multipart/form-data; boundary=...` for a FormData body, and
    // overriding it strips the boundary (which broke iOS Safari uploads).
    expect(config?.headers?.["Content-Type"]).toBeUndefined();

    await waitFor(() => {
      const files = useAppStore.getState().attachedFiles;
      expect(files).toHaveLength(1);
      expect(files[0]).toMatchObject({
        path: "report.pdf",
        content: "# Report\n\nHello.",
        source: "upload",
        kind: "pdf",
      });
    });
  });

  it("shows an error chip and does not attach the file when /api/upload fails (e.g. truly unsupported format)", async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce({
      response: { data: { error: "Unsupported file format" }, status: 400 },
      message: "Request failed with status code 400",
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

    // Use a format that has no extractor (zip, exe, …). Image formats are
    // now handled by the OCR pipeline, so they reach the success path.
    const file = new File([new Uint8Array([0x50, 0x4b, 0x03, 0x04])], "data.zip", {
      type: "application/zip",
    });
    await simulateFileSelect(file);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/Unsupported file format/i);
    });
    expect(useAppStore.getState().attachedFiles).toHaveLength(0);
  });

  it("attaches an image file with file_type=image once OCR extracted text", async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        success: true,
        filename: "screenshot.png",
        file_type: "image",
        markdown_content: "# OCR text from `screenshot.png`\n\nHello from OCR",
        markdown_path: "/tmp/screenshot.md",
      },
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

    const file = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], "screenshot.png", {
      type: "image/png",
    });
    await simulateFileSelect(file);

    await waitFor(() => {
      const files = useAppStore.getState().attachedFiles;
      expect(files).toHaveLength(1);
      expect(files[0]).toMatchObject({
        path: "screenshot.png",
        kind: "image",
        source: "upload",
      });
      expect(files[0].content).toContain("Hello from OCR");
    });
  });

  it("shows error message from server when upload returns success:false", async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { success: false, error: "File too large" },
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

    const file = new File(["x"], "huge.pdf", { type: "application/pdf" });
    await simulateFileSelect(file);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/File too large/i);
    });
    expect(useAppStore.getState().attachedFiles).toHaveLength(0);
  });
});
