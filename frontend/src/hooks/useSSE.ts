import { useEffect, useRef, useCallback } from "react";
import { useAppStore } from "@/stores/useAppStore";
import type { OrchestratorEvent } from "@/api/types";

interface SSEOptions {
  runId?: string;
  enabled: boolean;
}

/**
 * SSE (Server-Sent Events) transport — alternative to WebSocket streaming.
 * Used when sseMode is active.
 *
 * Connects to /api/runs/{runId}/stream or /api/stream for general streaming.
 */
export function useSSE({ runId, enabled }: SSEOptions) {
  const sourceRef = useRef<EventSource | null>(null);
  const { applyEvent, appendStreamChunk, finalizeStream, clearStreamBuffer, addMessage } =
    useAppStore.getState();

  const disconnect = useCallback(() => {
    if (sourceRef.current) {
      sourceRef.current.close();
      sourceRef.current = null;
    }
  }, []);

  const connect = useCallback(
    (id?: string) => {
      disconnect();

      const url = id
        ? `/api/runs/${encodeURIComponent(id)}/stream`
        : "/api/stream";

      const source = new EventSource(url);
      sourceRef.current = source;

      source.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data as string) as {
            type?: string;
            content?: string;
            usage?: { output_tokens?: number; model?: string };
            elapsed_s?: number;
            speed?: number;
            error?: string;
            data?: OrchestratorEvent;
          };

          if (data.type === "event" && data.data) {
            applyEvent(data.data);
          } else if (data.type === "token") {
            appendStreamChunk(data.content ?? "");
          } else if (data.type === "done") {
            finalizeStream(data);
            useAppStore.setState({ isStreaming: false });
            disconnect();
          } else if (data.type === "error") {
            clearStreamBuffer();
            addMessage({
              role: "assistant",
              content: `Error: ${data.error ?? "Unknown"}`,
              timestamp: Date.now(),
            });
            useAppStore.setState({ isStreaming: false });
            disconnect();
          }
        } catch (err) {
          console.error("SSE message parse error:", err);
        }
      };

      source.onerror = () => {
        // EventSource auto-reconnects; mark as error state
        useAppStore.setState({ isStreaming: false });
      };
    },
    [disconnect, applyEvent, appendStreamChunk, finalizeStream, clearStreamBuffer, addMessage]
  );

  useEffect(() => {
    if (enabled) {
      connect(runId);
    } else {
      disconnect();
    }

    return () => disconnect();
  }, [enabled, runId, connect, disconnect]);

  return { connect, disconnect };
}
