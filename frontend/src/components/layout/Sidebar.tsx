import { useState } from "react";
import { useAppStore } from "@/stores/useAppStore";
import type { OrchestratorEvent } from "@/api/types";
import { useClearCache } from "@/api/hooks";

type EventFilter = "all" | "agent" | "graph" | "cooperation" | "cache";

function eventCategory(type: string): string {
  if (type.startsWith("agent")) return "agent";
  if (type.startsWith("graph")) return "graph";
  if (type.startsWith("cooperation")) return "cooperation";
  if (type.startsWith("cache")) return "cache";
  if (type.startsWith("metrics")) return "metrics";
  return "orchestrator";
}

function eventDesc(evt: OrchestratorEvent): string {
  const d = evt.data as Record<string, unknown>;
  const a = evt.agent_name ? `[${evt.agent_name}] ` : "";
  switch (evt.event_type) {
    case "agent.spawn": return `${a}spawned`;
    case "agent.complete": return `${a}completed`;
    case "agent.error": return `${a}error`;
    case "graph.start": return `graph started (${((d.nodes as string[]) || []).length} nodes)`;
    case "graph.end": return `graph ended ${(d.elapsed_s as number) || 0}s`;
    case "graph.node.enter": return `entering ${evt.node_name ?? ""}`;
    case "graph.node.exit": return `exited ${evt.node_name ?? ""}`;
    case "cache.hit": return `cache hit${d.node_name ? ` [${String(d.node_name)}]` : ""}`;
    case "cache.miss": return `cache miss${d.node_name ? ` [${String(d.node_name)}]` : ""}`;
    default: return JSON.stringify(d).slice(0, 80);
  }
}

function formatTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

const CATEGORY_ICONS: Record<string, string> = {
  agent: "A",
  graph: "G",
  cooperation: "C",
  cache: "$",
  metrics: "M",
  orchestrator: "O",
};

/** Right sidebar with event log, agent activity, cache stats, and task plan. */
export function Sidebar() {
  const { events, clearEvents, cache, taskPlanItems, activityItems } = useAppStore();
  const [filter, setFilter] = useState<EventFilter>("all");
  const [selectedEvent, setSelectedEvent] = useState<OrchestratorEvent | null>(null);
  const clearCacheMutation = useClearCache();

  const filteredEvents =
    filter === "all"
      ? events
      : events.filter((e) => e.event_type.startsWith(filter));

  const cacheRate = (() => {
    const total = (cache.hits ?? 0) + (cache.misses ?? 0);
    return total > 0 ? `${(((cache.hits ?? 0) / total) * 100).toFixed(1)}%` : "-";
  })();

  const TASK_STATUS_ICONS: Record<string, string> = {
    pending: "·",
    in_progress: "~",
    completed: "✓",
    failed: "✗",
  };

  return (
    <aside className="sidebar" aria-label="Activity sidebar">
      {/* Agent Activity */}
      <section className="sidebar-section">
        <h2 className="sidebar-section__title">Agent Activity</h2>
        <div className="agent-activity">
          {activityItems.length === 0 ? (
            <div className="empty-state">Waiting for agents...</div>
          ) : (
            activityItems.slice(-50).map((item) => (
              <div key={item.id} className={`activity-item activity-item--${item.category}`}>
                <span className="activity-time">
                  {new Date(item.time).toLocaleTimeString("en-US", {
                    hour12: false,
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit",
                  })}
                </span>
                <span className={`activity-icon activity-icon--${item.category}`}>
                  {{ spawn: "S", step: "#", tool: "T", task: "D", complete: "✓", error: "!" }[
                    item.category
                  ] ?? "·"}
                </span>
                <div className="activity-body">
                  <span className="activity-agent">{item.agent}</span>
                  <div className="activity-desc">{item.desc}</div>
                  {item.detail && <div className="activity-detail">{item.detail}</div>}
                </div>
              </div>
            ))
          )}
        </div>
      </section>

      {/* Event Log */}
      <section className="sidebar-section">
        <div className="sidebar-section__header">
          <h2 className="sidebar-section__title">Event Log</h2>
          <div className="logs-controls">
            <select
              className="logs-filter"
              value={filter}
              onChange={(e) => setFilter(e.target.value as EventFilter)}
            >
              <option value="all">All</option>
              <option value="agent">Agent</option>
              <option value="graph">Graph</option>
              <option value="cooperation">Coop</option>
              <option value="cache">Cache</option>
            </select>
            <button className="btn-text" onClick={clearEvents}>
              Clear
            </button>
          </div>
        </div>
        <div className="timeline" role="list">
          {filteredEvents.slice(-100).map((evt, i) => {
            const cat = eventCategory(evt.event_type);
            return (
              <div
                key={i}
                className="event-item"
                role="listitem"
                onClick={() => setSelectedEvent(evt)}
                style={{ cursor: "pointer" }}
              >
                <span className="event-time">{formatTime(evt.timestamp)}</span>
                <span className={`event-icon event-icon--${cat}`}>
                  {CATEGORY_ICONS[cat] ?? "?"}
                </span>
                <div className="event-body">
                  <div className="event-type">{evt.event_type}</div>
                  <div className="event-desc">{eventDesc(evt)}</div>
                </div>
              </div>
            );
          })}
          {filteredEvents.length === 0 && (
            <div className="empty-state">No events</div>
          )}
        </div>
      </section>

      {/* Detail view */}
      {selectedEvent && (
        <section className="sidebar-section">
          <div className="sidebar-section__header">
            <h2 className="sidebar-section__title">Details</h2>
            <button className="btn-text" onClick={() => setSelectedEvent(null)}>
              Close
            </button>
          </div>
          <div className="detail-view">
            <pre className="detail-json">
              {JSON.stringify(selectedEvent, null, 2)}
            </pre>
          </div>
        </section>
      )}

      {/* Cache Stats */}
      <section className="sidebar-section">
        <div className="sidebar-section__header">
          <h2 className="sidebar-section__title">Cache</h2>
          <button
            className="btn-text"
            onClick={() => clearCacheMutation.mutate()}
            disabled={clearCacheMutation.isPending}
          >
            Clear
          </button>
        </div>
        <div className="cache-stats">
          <div className="cache-stats-grid">
            <div className="cache-stat">
              <span className="cache-stat-value">{cache.hits ?? 0}</span>
              <span className="cache-stat-label">Hits</span>
            </div>
            <div className="cache-stat">
              <span className="cache-stat-value">{cache.misses ?? 0}</span>
              <span className="cache-stat-label">Misses</span>
            </div>
            <div className="cache-stat">
              <span className="cache-stat-value">{cache.evictions ?? 0}</span>
              <span className="cache-stat-label">Evictions</span>
            </div>
            <div className="cache-stat">
              <span className="cache-stat-value">{cacheRate}</span>
              <span className="cache-stat-label">Hit Rate</span>
            </div>
          </div>
          <div className="cache-bar-container">
            <div
              className="cache-bar-fill"
              style={{
                width: `${(cache.hit_rate ?? 0) * 100}%`,
              }}
            />
          </div>
          <div className="cache-entries-info">
            <span>{cache.entries ?? 0} entries</span>
            {(cache.total_saved_tokens ?? 0) > 0 && (
              <span className="cache-saved">
                {cache.total_saved_tokens} tokens saved
              </span>
            )}
          </div>
        </div>
      </section>

      {/* Task Plan */}
      <section className="sidebar-section">
        <h2 className="sidebar-section__title">Task Plan</h2>
        <div className="task-plan-list">
          {taskPlanItems.length === 0 ? (
            <div className="empty-state">No active plan</div>
          ) : (
            taskPlanItems.map((item) => (
              <div
                key={item.nodeId}
                className={`task-plan-item task-plan-item--${item.status}`}
              >
                <span className="task-plan-icon">
                  {TASK_STATUS_ICONS[item.status] ?? "·"}
                </span>
                <span className="task-plan-name">{item.nodeId}</span>
                {item.elapsed !== null && (
                  <span className="task-plan-elapsed">{item.elapsed}s</span>
                )}
              </div>
            ))
          )}
        </div>
      </section>
    </aside>
  );
}
