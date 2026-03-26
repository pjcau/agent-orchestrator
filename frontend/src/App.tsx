import React, { useEffect } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient } from "./queryClient";
import { DashboardPage } from "@/pages/DashboardPage";
import { WebSocketProvider } from "@/hooks/useWebSocketContext";
import { useUsage } from "@/api/hooks";
import { useAppStore } from "@/stores/useAppStore";

/** Bootstraps data loading (WS connections are in WebSocketProvider). */
function AppBootstrap({ children }: { children: React.ReactNode }) {

  // Load cumulative usage stats
  const { data: usage } = useUsage();
  const setCumulativeUsage = useAppStore((s) => s.setCumulativeUsage);

  useEffect(() => {
    if (usage) {
      setCumulativeUsage(usage);
      // Update session speed from server if more accurate
      if (usage.session_speed && usage.session_speed > 0) {
        useAppStore.getState().setLastTokenSpeed(usage.session_speed);
      }
    }
  }, [usage, setCumulativeUsage]);

  // Report frontend errors to server
  useEffect(() => {
    const handleError = (e: ErrorEvent) => {
      fetch("/api/errors/client", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          component: "ui",
          message: e.message || String(e),
          source: e.filename || "",
          line: e.lineno || 0,
          session_id: "",
        }),
      }).catch(() => {});
    };

    const handleUnhandledRejection = (e: PromiseRejectionEvent) => {
      fetch("/api/errors/client", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          component: "ui-promise",
          message: e.reason
            ? (e.reason as Error).message || String(e.reason)
            : "Unhandled rejection",
          source: "",
          line: 0,
          session_id: "",
        }),
      }).catch(() => {});
    };

    window.addEventListener("error", handleError);
    window.addEventListener("unhandledrejection", handleUnhandledRejection);
    return () => {
      window.removeEventListener("error", handleError);
      window.removeEventListener("unhandledrejection", handleUnhandledRejection);
    };
  }, []);

  return <>{children}</>;
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <WebSocketProvider>
        <AppBootstrap>
          <DashboardPage />
        </AppBootstrap>
      </WebSocketProvider>
    </QueryClientProvider>
  );
}
