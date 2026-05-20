import { useEffect, useRef } from "react";
import { useAppStore } from "@/stores/useAppStore";

const STATUS_COLORS: Record<string, string> = {
  pending: "var(--text-dim)",
  running: "var(--accent)",
  completed: "var(--green)",
  failed: "var(--red)",
};

/**
 * Scrollable timeline of agent-to-agent interactions.
 * Ported from vanilla app.js renderInteractionTimeline (lines 387-454).
 * Data source: useAppStore().interactions (populated by useWebSocket.ts).
 */
export function InteractionTimeline() {
  const interactions = useAppStore((s) => s.interactions);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom whenever new interactions arrive.
  // scrollIntoView may be absent in test environments (jsdom).
  useEffect(() => {
    const el = bottomRef.current;
    if (el && typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ behavior: "smooth" });
    }
  }, [interactions]);

  if (interactions.length === 0) {
    return (
      <div className="interaction-timeline interaction-timeline--empty">
        <div className="empty-state">No interactions yet</div>
      </div>
    );
  }

  return (
    <div className="interaction-timeline" aria-label="Agent interaction timeline" role="log">
      {interactions.map((item, i) => {
        const ts = new Date(item.time).toLocaleTimeString("en-US", {
          hour12: false,
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        });
        const color = STATUS_COLORS[item.status] ?? "var(--text-dim)";
        return (
          <div
            key={i}
            className={`interaction-item interaction-item--${item.status}`}
          >
            <span className="interaction-time">{ts}</span>
            <span className="interaction-agents">
              <span className="interaction-from">{item.from}</span>
              <span className="interaction-arrow" aria-hidden="true">
                {/* Simple ASCII arrow matching vanilla arrowSvg intent */}
                &rarr;
              </span>
              <span className="interaction-to">{item.to}</span>
            </span>
            <span className="interaction-desc">
              {item.desc.length > 50
                ? `${item.desc.slice(0, 50)}...`
                : item.desc}
            </span>
            <span
              className="interaction-status-dot"
              aria-label={item.status}
              style={{ background: color }}
            />
          </div>
        );
      })}
      <div ref={bottomRef} />
    </div>
  );
}
