import { useState } from "react";
import { useSandboxStatus, useSandboxInfo, useSandboxCleanup, useSessionInfo } from "@/api/hooks";
import { SandboxTerminal } from "./SandboxTerminal";
import type { SandboxInfo } from "@/api/types";
import "./sandbox.css";

/**
 * Build the preview URL for a sandbox port.
 * In development (localhost), connects directly to the host port.
 * In production, uses the nginx /sandbox-preview/{port}/ proxy.
 */
function sandboxPreviewUrl(hostPort: string): string {
  const isDev = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
  if (isDev) {
    return `http://localhost:${hostPort}`;
  }
  // Production: proxy through nginx to avoid exposing extra ports
  return `${window.location.origin}/sandbox-preview/${hostPort}/`;
}

/**
 * Sandbox workspace panel — shows container status, preview iframe,
 * and interactive terminal for the current session's sandbox.
 */
export function SandboxPanel() {
  const { data: status } = useSandboxStatus();
  const { data: session } = useSessionInfo();
  const sessionId = session?.session_id ?? "";
  const { data: info, isLoading } = useSandboxInfo(sessionId, {
    enabled: Boolean(sessionId) && Boolean(status?.enabled),
  });
  const cleanup = useSandboxCleanup();
  const [activeTab, setActiveTab] = useState<"status" | "preview" | "terminal" | "logs">(
    "status"
  );
  const [previewPort, setPreviewPort] = useState<string>("");

  if (!status?.enabled) {
    return (
      <div className="sandbox-panel sandbox-panel--disabled">
        <p className="sandbox-panel__msg">
          Sandbox disabled. Set <code>SANDBOX_ENABLED=true</code> to enable.
        </p>
      </div>
    );
  }

  const isRunning = info?.status === "running";
  const ports = info?.mapped_ports ?? {};
  const portEntries = Object.entries(ports);

  return (
    <div className="sandbox-panel">
      {/* Tab bar */}
      <div className="sandbox-panel__tabs">
        {(["status", "preview", "terminal", "logs"] as const).map((tab) => (
          <button
            key={tab}
            className={`sandbox-panel__tab ${activeTab === tab ? "sandbox-panel__tab--active" : ""}`}
            onClick={() => setActiveTab(tab)}
          >
            {tab.charAt(0).toUpperCase() + tab.slice(1)}
          </button>
        ))}
      </div>

      {/* Status tab */}
      {activeTab === "status" && (
        <SandboxStatusView
          info={info ?? null}
          isLoading={isLoading}
          activeSessions={status.active_sessions}
          maxConcurrent={status.max_concurrent}
          onCleanup={() => {
            if (sessionId) cleanup.mutate(sessionId);
          }}
        />
      )}

      {/* Preview tab */}
      {activeTab === "preview" && (
        <div className="sandbox-panel__preview">
          {portEntries.length === 0 ? (
            <p className="sandbox-panel__msg">
              No ports exposed. Configure <code>exposed_ports</code> in sandbox config.
            </p>
          ) : (
            <>
              <div className="sandbox-panel__port-selector">
                <label>Port:</label>
                <select
                  value={previewPort || portEntries[0]?.[1]?.toString() || ""}
                  onChange={(e) => setPreviewPort(e.target.value)}
                >
                  {portEntries.map(([containerPort, hostPort]) => (
                    <option key={containerPort} value={String(hostPort)}>
                      :{containerPort} &rarr; :{hostPort}
                    </option>
                  ))}
                </select>
              </div>
              <iframe
                className="sandbox-panel__iframe"
                src={sandboxPreviewUrl(previewPort || String(portEntries[0]?.[1] ?? ""))}
                title="Sandbox Preview"
                sandbox="allow-scripts allow-same-origin allow-forms"
              />
            </>
          )}
        </div>
      )}

      {/* Terminal tab */}
      {activeTab === "terminal" && (
        <div className="sandbox-panel__terminal">
          {isRunning && info?.container_id ? (
            <SandboxTerminal sessionId={sessionId ?? ""} />
          ) : (
            <p className="sandbox-panel__msg">
              No running sandbox. Start an agent run with sandbox enabled.
            </p>
          )}
        </div>
      )}

      {/* Logs tab */}
      {activeTab === "logs" && (
        <SandboxLogsView sessionId={sessionId ?? ""} isDocker={Boolean(info?.container_id)} />
      )}
    </div>
  );
}

// --- Sub-components ---

function SandboxStatusView({
  info,
  isLoading,
  activeSessions,
  maxConcurrent,
  onCleanup,
}: {
  info: SandboxInfo | null;
  isLoading: boolean;
  activeSessions: number;
  maxConcurrent: number;
  onCleanup: () => void;
}) {
  if (isLoading) {
    return <p className="sandbox-panel__msg">Loading sandbox info...</p>;
  }

  return (
    <div className="sandbox-panel__status">
      <div className="sandbox-panel__status-grid">
        <div className="sandbox-panel__stat">
          <span className="sandbox-panel__stat-label">Status</span>
          <span
            className={`sandbox-panel__stat-value sandbox-panel__stat-value--${info?.status ?? "not_started"}`}
          >
            {info?.status ?? "not_started"}
          </span>
        </div>
        <div className="sandbox-panel__stat">
          <span className="sandbox-panel__stat-label">Image</span>
          <span className="sandbox-panel__stat-value">{info?.image ?? "-"}</span>
        </div>
        <div className="sandbox-panel__stat">
          <span className="sandbox-panel__stat-label">Uptime</span>
          <span className="sandbox-panel__stat-value">
            {info?.uptime_seconds ? `${Math.round(info.uptime_seconds)}s` : "-"}
          </span>
        </div>
        <div className="sandbox-panel__stat">
          <span className="sandbox-panel__stat-label">Memory</span>
          <span className="sandbox-panel__stat-value">{info?.memory_limit ?? "-"}</span>
        </div>
        <div className="sandbox-panel__stat">
          <span className="sandbox-panel__stat-label">CPU</span>
          <span className="sandbox-panel__stat-value">{info?.cpu_limit ?? "-"}</span>
        </div>
        <div className="sandbox-panel__stat">
          <span className="sandbox-panel__stat-label">Sessions</span>
          <span className="sandbox-panel__stat-value">
            {activeSessions}/{maxConcurrent}
          </span>
        </div>
      </div>

      {/* Port mappings */}
      {info?.mapped_ports && Object.keys(info.mapped_ports).length > 0 && (
        <div className="sandbox-panel__ports">
          <h4>Exposed Ports</h4>
          <ul>
            {Object.entries(info.mapped_ports).map(([cp, hp]) => (
              <li key={cp}>
                Container :{cp} &rarr;{" "}
                <a href={sandboxPreviewUrl(String(hp))} target="_blank" rel="noopener noreferrer">
                  :{hp}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}

      {info?.status === "running" && (
        <button className="sandbox-panel__cleanup-btn" onClick={onCleanup}>
          Stop Sandbox
        </button>
      )}
    </div>
  );
}

function SandboxLogsView({
  sessionId,
  isDocker,
}: {
  sessionId: string;
  isDocker: boolean;
}) {
  const [logs, setLogs] = useState<string[]>([]);
  const [connected, setConnected] = useState(false);

  const startLogs = () => {
    if (!sessionId || !isDocker) return;
    const url = `/api/sandbox/${encodeURIComponent(sessionId)}/logs?follow=true&tail=200`;
    const es = new EventSource(url);
    setConnected(true);
    setLogs([]);

    es.onmessage = (e) => {
      setLogs((prev) => [...prev.slice(-500), e.data]);
    };
    es.onerror = () => {
      es.close();
      setConnected(false);
    };
  };

  if (!isDocker) {
    return <p className="sandbox-panel__msg">Logs only available for Docker sandboxes.</p>;
  }

  return (
    <div className="sandbox-panel__logs">
      {!connected && (
        <button className="sandbox-panel__logs-btn" onClick={startLogs}>
          Stream Logs
        </button>
      )}
      <pre className="sandbox-panel__logs-output">
        {logs.length > 0 ? logs.join("\n") : "No logs yet."}
      </pre>
    </div>
  );
}
