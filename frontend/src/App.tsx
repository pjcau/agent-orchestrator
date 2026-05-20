import React, { useEffect } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient } from "./queryClient";
import { DashboardPage } from "@/pages/DashboardPage";
import { WebSocketProvider } from "@/hooks/useWebSocketContext";
import { useUsage } from "@/api/hooks";
import { useAppStore } from "@/stores/useAppStore";
import apiClient from "@/api/client";

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

  // Restore conversation history at boot if we have a persisted id.
  // The store hydrates conversationId from localStorage at create time;
  // here we fetch the saved messages for that id and replay them in the chat.
  useEffect(() => {
    const convId = useAppStore.getState().conversationId;
    if (!convId) return;

    type RestoredMessage = { role: string; content: string };
    apiClient
      .get<{ conversation_id: string; messages: RestoredMessage[] }>(
        `/api/conversation/${encodeURIComponent(convId)}`
      )
      .then(({ data }) => {
        const msgs = data.messages ?? [];
        if (msgs.length === 0) return; // empty conv — keep id, no replay
        const store = useAppStore.getState();
        for (const m of msgs) {
          if (m.role !== "user" && m.role !== "assistant" && m.role !== "system") continue;
          store.addMessage({
            role: m.role,
            content: m.content,
            timestamp: Date.now(),
          });
        }
        store.addMessage({
          role: "system",
          content: `Restored conversation (${msgs.length} messages).`,
          timestamp: Date.now(),
        });
      })
      .catch(() => {
        // Stored id no longer exists on the server — clear it so the next
        // send creates a fresh conversation.
        useAppStore.getState().setConversationId(null);
      });
  }, []);

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
