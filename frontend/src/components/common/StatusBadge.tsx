import React from "react";
import type { OrchestratorStatus } from "@/stores/useAppStore";

interface StatusBadgeProps {
  status: OrchestratorStatus | string;
  className?: string;
}

const STATUS_LABELS: Record<string, string> = {
  idle: "IDLE",
  running: "RUNNING",
  completed: "DONE",
  failed: "FAILED",
  error: "ERROR",
};

export function StatusBadge({ status, className = "" }: StatusBadgeProps) {
  const label = STATUS_LABELS[status] ?? status.toUpperCase();
  return (
    <span className={`status-badge status-badge--${status} ${className}`}>
      {label}
    </span>
  );
}
