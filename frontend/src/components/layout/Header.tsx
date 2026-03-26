import React from "react";
import { useAppStore } from "@/stores/useAppStore";
import { StatusBadge } from "@/components/common/StatusBadge";
import { MetricsBar } from "@/components/metrics/MetricsBar";

interface HeaderProps {
  onToggleSidebar: () => void;
  onToggleSSE: () => void;
  sidebarOpen: boolean;
  sseMode: boolean;
}

export function Header({ onToggleSidebar, onToggleSSE, sidebarOpen, sseMode }: HeaderProps) {
  const { orchestratorStatus, wsConnected } = useAppStore();

  return (
    <header className="app-header">
      <div className="app-header__left">
        <h1 className="app-header__title">Agent Orchestrator</h1>
        <StatusBadge status={orchestratorStatus} />
      </div>

      <div className="app-header__right">
        <MetricsBar />

        <div className="app-header__actions">
          <button
            className={`btn-sidebar-toggle ${sidebarOpen ? "active" : ""}`}
            onClick={onToggleSidebar}
            title="Toggle logs panel"
          >
            Logs
          </button>

          <button
            className={`btn-sidebar-toggle sse-toggle-btn ${sseMode ? "active" : ""}`}
            onClick={onToggleSSE}
            title="Toggle SSE mode"
          >
            SSE
          </button>

          <span
            className={`ws-dot ${wsConnected ? "ws-dot--connected" : "ws-dot--disconnected"}`}
            title={wsConnected ? "WebSocket connected" : "WebSocket disconnected"}
          />
        </div>
      </div>
    </header>
  );
}
