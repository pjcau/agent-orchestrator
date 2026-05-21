import { useEffect, useRef, useState } from "react";
import { useAppStore } from "@/stores/useAppStore";

interface ChatMessageActionsProps {
  /** Stable id for the message (we use timestamp). */
  messageId: string;
  /** Plain-string content of the assistant message (markdown). */
  content: string;
  /** Called when the user clicks Regenerate. */
  onRegenerate?: () => void;
}

/**
 * Strip the most common markdown syntax so the resulting plain text reads
 * naturally when passed to TTS or the native share sheet.
 */
function stripMarkdown(md: string): string {
  return md
    // fenced code blocks
    .replace(/```[\s\S]*?```/g, (m) => m.replace(/```\w*\n?|```/g, ""))
    // inline code
    .replace(/`([^`]+)`/g, "$1")
    // bold / italic / strike
    .replace(/(\*\*|__)(.*?)\1/g, "$2")
    .replace(/(\*|_)(.*?)\1/g, "$2")
    .replace(/~~(.*?)~~/g, "$1")
    // headings
    .replace(/^#{1,6}\s+/gm, "")
    // links: [text](url) → text (url)
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1 ($2)")
    // images
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, "$1")
    // list markers
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*\d+\.\s+/gm, "")
    // blockquotes
    .replace(/^>\s?/gm, "")
    .trim();
}

/**
 * POST the markdown to rentry.co and return the public URL.
 *
 * Note: rentry.co exposes a CORS-restricted endpoint. If the browser blocks
 * the request the caller will see a generic error and the UI surfaces a
 * "Share failed" notice. The behaviour here is deliberately fail-loud: we do
 * NOT silently fall back to copying so the user knows their action did not go
 * through the chosen platform.
 */
async function shareToRentry(text: string): Promise<string> {
  const form = new URLSearchParams();
  form.set("text", text);
  const resp = await fetch("https://rentry.co/api/new", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form.toString(),
  });
  if (!resp.ok) {
    throw new Error(`rentry HTTP ${resp.status}`);
  }
  const data = (await resp.json()) as { url?: string; status?: string; content?: string };
  if (!data.url) {
    throw new Error(data.content || data.status || "rentry: no url");
  }
  return data.url;
}

function IconCopy() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M5 15V6a2 2 0 0 1 2-2h9" />
    </svg>
  );
}

function IconShare() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 16V4" />
      <path d="M7 9l5-5 5 5" />
      <path d="M5 14v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5" />
    </svg>
  );
}

function IconPlay() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M7 5l12 7-12 7V5z" />
    </svg>
  );
}

function IconStop() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <rect x="6" y="6" width="12" height="12" rx="1.5" />
    </svg>
  );
}

function IconThumbUp({ filled }: { filled?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" fill={filled ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M7 11v9H4a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1h3z" />
      <path d="M7 11l4-7a2 2 0 0 1 4 1v4h4.5a2 2 0 0 1 2 2.3l-1.2 7A2 2 0 0 1 18.3 20H7" />
    </svg>
  );
}

function IconThumbDown({ filled }: { filled?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" fill={filled ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M17 13V4h3a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-3z" />
      <path d="M17 13l-4 7a2 2 0 0 1-4-1v-4H4.5a2 2 0 0 1-2-2.3l1.2-7A2 2 0 0 1 5.7 4H17" />
    </svg>
  );
}

function IconRegenerate() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 12a9 9 0 0 1 15.5-6.3" />
      <path d="M19 3v4h-4" />
      <path d="M21 12a9 9 0 0 1-15.5 6.3" />
      <path d="M5 21v-4h4" />
    </svg>
  );
}

export function ChatMessageActions({ messageId, content, onRegenerate }: ChatMessageActionsProps) {
  const feedback = useAppStore((s) => s.messageFeedback[messageId]);
  const setMessageFeedback = useAppStore((s) => s.setMessageFeedback);

  // Inline status pill (Copied / Shared / Error). Auto-clears after 1.5s.
  const [status, setStatus] = useState<string | null>(null);
  const statusTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const flash = (msg: string) => {
    setStatus(msg);
    if (statusTimer.current) clearTimeout(statusTimer.current);
    statusTimer.current = setTimeout(() => setStatus(null), 1500);
  };

  // Read-aloud state: track whether this message is currently being spoken
  // so we can toggle the button between play and stop.
  const [speaking, setSpeaking] = useState(false);
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null);

  useEffect(() => {
    return () => {
      if (statusTimer.current) clearTimeout(statusTimer.current);
      // Stop any in-flight speech on unmount so a regenerate/reset doesn't
      // leave the synth speaking the old content.
      if (utteranceRef.current && typeof window !== "undefined") {
        window.speechSynthesis?.cancel();
      }
    };
  }, []);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      flash("Copied");
    } catch {
      flash("Copy failed");
    }
  };

  const handleShare = async () => {
    flash("Sharing…");
    try {
      const url = await shareToRentry(content);
      // Mobile (or any browser that exposes Web Share API) → OS share sheet.
      // Desktop → open the rentry page directly in a new tab so the user can
      // grab the URL from the address bar.
      const nav = navigator as Navigator & { share?: (data: { url: string }) => Promise<void> };
      if (typeof nav.share === "function") {
        try {
          await nav.share({ url });
          flash("Shared");
        } catch (err) {
          // User canceled the share sheet — don't treat as error.
          const name = (err as { name?: string })?.name;
          if (name !== "AbortError") {
            flash("Share canceled");
          } else {
            setStatus(null);
          }
        }
      } else {
        window.open(url, "_blank", "noopener,noreferrer");
        flash("Opened");
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      flash(`Share failed: ${msg}`);
    }
  };

  const handleSpeak = () => {
    if (typeof window === "undefined" || !window.speechSynthesis) {
      flash("TTS not supported");
      return;
    }
    if (speaking) {
      window.speechSynthesis.cancel();
      setSpeaking(false);
      return;
    }
    const utter = new SpeechSynthesisUtterance(stripMarkdown(content));
    utter.lang = navigator.language || "en-US";
    utter.onend = () => setSpeaking(false);
    utter.onerror = () => setSpeaking(false);
    utteranceRef.current = utter;
    window.speechSynthesis.speak(utter);
    setSpeaking(true);
  };

  const handleThumb = (kind: "up" | "down") => {
    setMessageFeedback(messageId, kind);
  };

  return (
    <div className="chat-bubble__actions" role="toolbar" aria-label="Message actions">
      <button
        type="button"
        className="chat-action-btn"
        onClick={handleCopy}
        aria-label="Copy response"
        title="Copy"
      >
        <IconCopy />
      </button>
      <button
        type="button"
        className="chat-action-btn"
        onClick={handleShare}
        aria-label="Share response"
        title="Share"
      >
        <IconShare />
      </button>
      <button
        type="button"
        className={`chat-action-btn${speaking ? " chat-action-btn--active" : ""}`}
        onClick={handleSpeak}
        aria-label={speaking ? "Stop reading" : "Read aloud"}
        aria-pressed={speaking}
        title={speaking ? "Stop" : "Read aloud"}
      >
        {speaking ? <IconStop /> : <IconPlay />}
      </button>
      <button
        type="button"
        className={`chat-action-btn${feedback === "up" ? " chat-action-btn--active" : ""}`}
        onClick={() => handleThumb("up")}
        aria-label="Good response"
        aria-pressed={feedback === "up"}
        title="Good response"
      >
        <IconThumbUp filled={feedback === "up"} />
      </button>
      <button
        type="button"
        className={`chat-action-btn${feedback === "down" ? " chat-action-btn--active" : ""}`}
        onClick={() => handleThumb("down")}
        aria-label="Bad response"
        aria-pressed={feedback === "down"}
        title="Bad response"
      >
        <IconThumbDown filled={feedback === "down"} />
      </button>
      {onRegenerate && (
        <button
          type="button"
          className="chat-action-btn"
          onClick={onRegenerate}
          aria-label="Regenerate response"
          title="Regenerate"
        >
          <IconRegenerate />
        </button>
      )}
      {status && (
        <span className="chat-bubble__actions-status" role="status" aria-live="polite">
          {status}
        </span>
      )}
    </div>
  );
}
