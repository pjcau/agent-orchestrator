import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ChatMessageActions } from "@/components/chat/ChatMessageActions";
import { ChatMessageItem } from "@/components/chat/ChatMessage";
import { useAppStore, STORAGE_KEY_MESSAGE_FEEDBACK } from "@/stores/useAppStore";
import type { ChatMessage } from "@/api/types";

// HITL components hit the API client on mount via React Query. Stub the
// module so importing ChatMessage doesn't blow up.
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

function resetStore() {
  // Reset only the bits we touch.
  useAppStore.setState({ messageFeedback: {} });
  try {
    window.localStorage.removeItem(STORAGE_KEY_MESSAGE_FEEDBACK);
  } catch {
    /* fail silently */
  }
}

describe("ChatMessageActions", () => {
  beforeEach(() => {
    resetStore();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders all six action buttons when onRegenerate is provided", () => {
    render(
      <ChatMessageActions messageId="1" content="hello" onRegenerate={() => {}} />,
    );
    expect(screen.getByLabelText("Copy response")).toBeInTheDocument();
    expect(screen.getByLabelText("Share response")).toBeInTheDocument();
    expect(screen.getByLabelText("Read aloud")).toBeInTheDocument();
    expect(screen.getByLabelText("Good response")).toBeInTheDocument();
    expect(screen.getByLabelText("Bad response")).toBeInTheDocument();
    expect(screen.getByLabelText("Regenerate response")).toBeInTheDocument();
  });

  it("hides the regenerate button when no onRegenerate handler is given", () => {
    render(<ChatMessageActions messageId="1" content="hello" />);
    expect(screen.queryByLabelText("Regenerate response")).toBeNull();
  });

  it("copies the content to clipboard when Copy is clicked", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    render(<ChatMessageActions messageId="1" content="copy me" />);
    fireEvent.click(screen.getByLabelText("Copy response"));

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith("copy me");
    });
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });

  it("toggles the thumb-up state in the store", () => {
    render(<ChatMessageActions messageId="msg-42" content="x" />);

    fireEvent.click(screen.getByLabelText("Good response"));
    expect(useAppStore.getState().messageFeedback["msg-42"]).toBe("up");

    // Clicking again clears the rating.
    fireEvent.click(screen.getByLabelText("Good response"));
    expect(useAppStore.getState().messageFeedback["msg-42"]).toBeUndefined();
  });

  it("thumbs-up and thumbs-down are mutually exclusive", () => {
    render(<ChatMessageActions messageId="m1" content="x" />);

    fireEvent.click(screen.getByLabelText("Good response"));
    expect(useAppStore.getState().messageFeedback["m1"]).toBe("up");

    fireEvent.click(screen.getByLabelText("Bad response"));
    expect(useAppStore.getState().messageFeedback["m1"]).toBe("down");
  });

  it("invokes onRegenerate when the regenerate button is clicked", () => {
    const onRegenerate = vi.fn();
    render(
      <ChatMessageActions messageId="1" content="hi" onRegenerate={onRegenerate} />,
    );
    fireEvent.click(screen.getByLabelText("Regenerate response"));
    expect(onRegenerate).toHaveBeenCalledTimes(1);
  });

  it("posts the content to rentry and opens the URL when navigator.share is missing (desktop)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ url: "https://rentry.co/abc" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    // Ensure navigator.share is NOT defined for this test.
    const originalShare = (navigator as { share?: unknown }).share;
    delete (navigator as { share?: unknown }).share;

    const openMock = vi.fn();
    vi.stubGlobal("open", openMock);

    render(<ChatMessageActions messageId="1" content="**bold**" />);
    fireEvent.click(screen.getByLabelText("Share response"));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://rentry.co/api/new");
    expect((init as RequestInit).method).toBe("POST");
    // Body is form-encoded with the markdown intact.
    expect(String((init as RequestInit).body)).toContain("text=");

    await waitFor(() => {
      expect(openMock).toHaveBeenCalledWith(
        "https://rentry.co/abc",
        "_blank",
        "noopener,noreferrer",
      );
    });

    // Restore share if it existed
    if (originalShare !== undefined) {
      (navigator as { share?: unknown }).share = originalShare;
    }
  });

  it("calls navigator.share with the rentry URL when available (mobile)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ url: "https://rentry.co/xyz" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const shareMock = vi.fn().mockResolvedValue(undefined);
    (navigator as { share?: unknown }).share = shareMock;

    render(<ChatMessageActions messageId="1" content="hi" />);
    fireEvent.click(screen.getByLabelText("Share response"));

    await waitFor(() => {
      expect(shareMock).toHaveBeenCalledWith({ url: "https://rentry.co/xyz" });
    });

    delete (navigator as { share?: unknown }).share;
  });

  it("surfaces an error toast when rentry returns a non-ok response", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);

    render(<ChatMessageActions messageId="1" content="x" />);
    fireEvent.click(screen.getByLabelText("Share response"));

    expect(await screen.findByText(/Share failed/i)).toBeInTheDocument();
  });
});

describe("ChatMessage assistant bubble — actions row", () => {
  it("renders the actions row below the meta footer for a string assistant message", () => {
    const msg: ChatMessage = {
      role: "assistant",
      content: "Hello world",
      model: "claude",
      elapsed_s: 1,
      cost_usd: 0.001,
      timestamp: 12345,
    };
    const { container } = render(<ChatMessageItem message={msg} />);
    expect(container.querySelector(".chat-bubble__meta")).not.toBeNull();
    expect(container.querySelector(".chat-bubble__actions")).not.toBeNull();
    // Meta must come before actions in DOM order.
    const meta = container.querySelector(".chat-bubble__meta");
    const actions = container.querySelector(".chat-bubble__actions");
    if (meta && actions) {
      // eslint-disable-next-line no-bitwise
      expect(meta.compareDocumentPosition(actions) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it("does not render the actions row for user messages", () => {
    const msg: ChatMessage = {
      role: "user",
      content: "Hi",
      timestamp: 1,
    };
    const { container } = render(<ChatMessageItem message={msg} />);
    expect(container.querySelector(".chat-bubble__actions")).toBeNull();
  });

  it("does not render the actions row while the assistant is streaming", () => {
    const msg = {
      role: "assistant" as const,
      content: "partial...",
      timestamp: 1,
      streaming: true,
    };
    const { container } = render(<ChatMessageItem message={msg} />);
    expect(container.querySelector(".chat-bubble__actions")).toBeNull();
  });
});
