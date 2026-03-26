import React from "react";
import { useAppStore } from "@/stores/useAppStore";
import { useUsage, useMCPTools } from "@/api/hooks";

function formatNumber(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(n);
}

/** Session + cumulative metrics bar displayed in the header. */
export function MetricsBar() {
  const { totalTokens, totalCostUsd, lastTokenSpeed, cache } = useAppStore();
  const { data: usage } = useUsage();
  const { data: mcpData } = useMCPTools();

  const cacheTotal = (cache.hits ?? 0) + (cache.misses ?? 0);
  const cacheRate =
    cacheTotal > 0
      ? `${((cache.hits / cacheTotal) * 100).toFixed(1)}%`
      : "-";

  return (
    <div className="metrics-bar">
      {/* Session metrics */}
      <div className="metric-group" title="Current session">
        <div className="metric">
          <span className="metric-label">Session Tokens</span>
          <span className="metric-value">{formatNumber(totalTokens)}</span>
        </div>
        <div className="metric">
          <span className="metric-label">Session Cost</span>
          <span className="metric-value">${totalCostUsd.toFixed(3)}</span>
        </div>
        <div className="metric">
          <span className="metric-label">Speed</span>
          <span className="metric-value">
            {lastTokenSpeed > 0 ? `${lastTokenSpeed.toFixed(1)} tok/s` : "- tok/s"}
          </span>
        </div>
      </div>

      <div className="metric-separator" />

      {/* Cumulative metrics from DB */}
      {usage && (
        <div className="metric-group metric-group--cumulative" title="All-time totals (from DB)">
          <div className="metric">
            <span className="metric-label">Total Tokens</span>
            <span className="metric-value">{formatNumber(usage.total_tokens)}</span>
          </div>
          <div className="metric">
            <span className="metric-label">Total Cost</span>
            <span className="metric-value">${(usage.total_cost_usd ?? 0).toFixed(3)}</span>
          </div>
          <div className="metric">
            <span className="metric-label">Avg Speed</span>
            <span className="metric-value">
              {usage.avg_speed > 0 ? `${usage.avg_speed} tok/s` : "- tok/s"}
            </span>
          </div>
          <div className="metric">
            <span className="metric-label">Requests</span>
            <span className="metric-value">{usage.total_requests}</span>
          </div>
          <span
            className={`db-dot ${usage.db_connected ? "db-dot--connected" : "db-dot--disconnected"}`}
            title={usage.db_connected ? "PostgreSQL connected" : "In-memory only"}
          />
        </div>
      )}

      <div className="metric-separator" />

      {/* Cache hit rate */}
      <div className="metric metric--cache">
        <span className="metric-label">Cache</span>
        <span className="metric-value">{cacheRate}</span>
      </div>

      <div className="metric-separator" />

      {/* MCP tool count */}
      <div className="metric metric--mcp">
        <span className="metric-label">MCP</span>
        <span className="metric-value">{mcpData?.count ?? "-"}</span>
      </div>
    </div>
  );
}
