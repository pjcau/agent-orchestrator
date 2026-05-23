/**
 * Upload flow test. The component now uses native `fetch` for the upload
 * (NOT the shared axios apiClient), so we mock `globalThis.fetch` here.
 *
 * Why fetch and not axios: the apiClient instance sets a default
 * `Content-Type: application/json`, and getting axios v1 to reliably
 * *delete* that header for a single FormData request across browsers
 * (especially iOS Safari) is brittle. fetch + FormData always emits
 * `multipart/form-data; boundary=<random>` — exactly what FastAPI needs.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

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
import { useAppStore } from "@/stores/useAppStore";

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

const baseModels = {
  ollama: [],
  openrouter: [{ name: "openai/gpt-4o", size: "" }],
};

/** Build a fake Response with the given JSON body & status. */
function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "Error",
    json: async () => body,
    text: async () => JSON.stringify(body),
    headers: new Headers({ "Content-Type": "application/json" }),
  } as unknown as Response;
}

describe("ChatInput — C2 file upload via /api/upload", () => {
  let fetchSpy: ReturnType<typeof vi.fn>;
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    useAppStore.getState().reset();
    useAppStore.getState().clearAttachedFiles();
    fetchSpy = vi.fn();
    globalThis.fetch = fetchSpy as unknown as typeof fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
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
          (captured as HTMLInputElement).click = () => {
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
    await user.click(screen.getByTitle(/Upload local file/i));
    spy.mockRestore();
  }

  it("POSTs /api/upload with FormData (no manual Content-Type) and adds markdown to attachedFiles", async () => {
    fetchSpy.mockResolvedValueOnce(
      jsonResponse({
        success: true,
        filename: "report.pdf",
        file_type: "pdf",
        markdown_content: "# Report\n\nHello.",
        markdown_path: "/tmp/report.md",
        page_count: 1,
      })
    );

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

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/upload");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    expect(init.credentials).toBe("include");
    // We must NOT set Content-Type — the browser auto-generates
    // `multipart/form-data; boundary=...`. Manual override broke iOS Safari.
    expect(init.headers?.["Content-Type"]).toBeUndefined();

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

  it("forwards X-API-Key from localStorage when present (API-key auth)", async () => {
    localStorage.setItem("api_key", "test-key-123");
    fetchSpy.mockResolvedValueOnce(
      jsonResponse({
        success: true,
        filename: "x.pdf",
        file_type: "pdf",
        markdown_content: "x",
        markdown_path: "/tmp/x.md",
      })
    );

    render(
      <ChatInput models={baseModels} isDisabled={false} onSend={vi.fn()} onNewChat={vi.fn()} />,
      { wrapper }
    );

    await simulateFileSelect(new File(["x"], "x.pdf", { type: "application/pdf" }));
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    const [, init] = fetchSpy.mock.calls[0];
    expect(init.headers?.["X-API-Key"]).toBe("test-key-123");
    localStorage.removeItem("api_key");
  });

  it("shows error chip when the server returns a non-OK status with a JSON body", async () => {
    fetchSpy.mockResolvedValueOnce(jsonResponse({ error: "Unsupported file format" }, 400));

    render(
      <ChatInput models={baseModels} isDisabled={false} onSend={vi.fn()} onNewChat={vi.fn()} />,
      { wrapper }
    );

    await simulateFileSelect(
      new File([new Uint8Array([0x50, 0x4b, 0x03, 0x04])], "data.zip", {
        type: "application/zip",
      })
    );

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/Unsupported file format/i);
    });
    expect(useAppStore.getState().attachedFiles).toHaveLength(0);
  });

  it("falls back to status text when the error response is not JSON (nginx 413)", async () => {
    fetchSpy.mockResolvedValueOnce({
      ok: false,
      status: 413,
      statusText: "Request Entity Too Large",
      json: async () => {
        throw new Error("not json");
      },
      headers: new Headers(),
    } as unknown as Response);

    render(
      <ChatInput models={baseModels} isDisabled={false} onSend={vi.fn()} onNewChat={vi.fn()} />,
      { wrapper }
    );

    await simulateFileSelect(new File(["big"], "big.pdf", { type: "application/pdf" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/413.*Request Entity Too Large/i);
    });
  });

  it("attaches an image file with file_type=image once OCR extracted text", async () => {
    fetchSpy.mockResolvedValueOnce(
      jsonResponse({
        success: true,
        filename: "screenshot.png",
        file_type: "image",
        markdown_content: "# OCR text from `screenshot.png`\n\nHello from OCR",
        markdown_path: "/tmp/screenshot.md",
      })
    );

    render(
      <ChatInput models={baseModels} isDisabled={false} onSend={vi.fn()} onNewChat={vi.fn()} />,
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

  it("shows server error when /api/upload returns 200 with success:false", async () => {
    fetchSpy.mockResolvedValueOnce(jsonResponse({ success: false, error: "File too large" }));

    render(
      <ChatInput models={baseModels} isDisabled={false} onSend={vi.fn()} onNewChat={vi.fn()} />,
      { wrapper }
    );

    await simulateFileSelect(new File(["x"], "huge.pdf", { type: "application/pdf" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/File too large/i);
    });
    expect(useAppStore.getState().attachedFiles).toHaveLength(0);
  });

  it("surfaces TypeError(\"Failed to fetch\") as a network-error message", async () => {
    fetchSpy.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    render(
      <ChatInput models={baseModels} isDisabled={false} onSend={vi.fn()} onNewChat={vi.fn()} />,
      { wrapper }
    );

    await simulateFileSelect(new File(["x"], "x.pdf", { type: "application/pdf" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/Network error.*Failed to fetch/i);
    });
  });
});
