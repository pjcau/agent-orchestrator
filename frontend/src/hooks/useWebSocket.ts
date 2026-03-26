import { useEffect, useRef, useCallback } from "react";
import { buildWsUrl } from "@/api/client";
import { useAppStore } from "@/stores/useAppStore";
import type { OrchestratorEvent, Snapshot, TeamRunResult } from "@/api/types";

// Re-connection backoff: 2s, 4s, 8s, max 16s
function getBackoffMs(attempt: number): number {
  return Math.min(2000 * Math.pow(2, attempt), 16000);
}

/**
 * Manages the two WebSocket connections:
 * 1. Event bus `/ws` — receives snapshots and incremental events
 * 2. Streaming `/ws/stream` — receives token-by-token LLM output
 *
 * Returns a sendPrompt function to trigger streaming runs.
 */
export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const streamWsRef = useRef<WebSocket | null>(null);
  const eventReconnectAttempt = useRef(0);
  const streamReconnectAttempt = useRef(0);
  const pageUnloading = useRef(false);
  const mountedRef = useRef(true);

  const {
    setWsConnected,
    applySnapshot,
    applyEvent,
    appendStreamChunk,
    finalizeStream,
    clearStreamBuffer,
    addMessage,
    addActivityItem,
    addInteraction,
    updateInteraction,
    setPendingTeamJob,
  } = useAppStore.getState();

  // Process orchestrator events and route to activity / interaction panels
  const processEvent = useCallback(
    (event: OrchestratorEvent) => {
      applyEvent(event);

      const t = event.event_type;
      const d = event.data as Record<string, unknown>;
      const agent = event.agent_name ?? "";

      // Route to activity panel
      if (t === "agent.spawn") {
        addActivityItem("spawn", agent, "Agent spawned", String(d.provider ?? ""));
        if (agent && agent !== "team-lead") {
          addInteraction("team-lead", agent, "spawned agent", "running");
        }
      } else if (t === "agent.step") {
        addActivityItem("step", agent, `Step ${String(d.step ?? "")}`, String(d.model ?? ""));
      } else if (t === "agent.tool_call") {
        const args = Object.entries((d.arguments as Record<string, unknown>) ?? {})
          .map(([k, v]) => `${k}=${String(v).slice(0, 40)}`)
          .join(", ");
        addActivityItem("tool", agent, `-> ${String(d.tool_name ?? "tool")}`, args);
      } else if (t === "agent.tool_result") {
        const status = d.success ? "success" : "fail";
        addActivityItem(
          "tool",
          agent,
          `<- ${String(d.tool_name ?? "tool")} [${status}]`,
          String(d.output ?? "").slice(0, 60)
        );
      } else if (t === "cooperation.task_assigned") {
        addActivityItem(
          "task",
          String(d.from_agent ?? "?"),
          `Delegated to ${String(d.to_agent ?? "?")}`,
          String(d.description ?? "").slice(0, 50)
        );
        addInteraction(
          String(d.from_agent ?? "?"),
          String(d.to_agent ?? "?"),
          String(d.description ?? "task delegation"),
          "running"
        );
      } else if (t === "cooperation.task_completed") {
        const ok = Boolean(d.success);
        addActivityItem(
          ok ? "complete" : "error",
          String(d.from_agent ?? "?"),
          `Task ${ok ? "completed" : "failed"}`,
          String(d.summary ?? "").slice(0, 50)
        );
        updateInteraction(
          String(d.from_agent ?? "?"),
          String(d.to_agent ?? "?"),
          ok ? "completed" : "failed"
        );
        addInteraction(
          String(d.to_agent ?? "?"),
          String(d.from_agent ?? "?"),
          String(d.summary ?? "task result"),
          ok ? "completed" : "failed"
        );
      } else if (t === "agent.complete") {
        addActivityItem(
          "complete",
          agent,
          "Completed",
          String(d.output ?? "").slice(0, 50)
        );
      } else if (t === "agent.error" || t === "agent.stalled") {
        addActivityItem("error", agent, String(d.error ?? "Error"), "");
      }

      // Team lifecycle
      if (t === "team.started") {
        addMessage({
          role: "system",
          content: `Agents planning: ${String(d.task ?? "").slice(0, 100)}...`,
          timestamp: Date.now(),
        });
      }

      if (t === "team.complete") {
        const state = useAppStore.getState();
        if (state.pendingTeamJobId) {
          const result = d as unknown as TeamRunResult;
          const model = state.pendingTeamModel ?? "";
          setPendingTeamJob(null, null);

          if (result.success) {
            const steps: Array<{ node: string; output: string }> = [];
            if (result.plan) {
              steps.push({ node: "team-lead (plan)", output: result.plan });
            }
            for (const [ag, output] of Object.entries(result.agent_outputs ?? {})) {
              steps.push({ node: ag, output });
              addInteraction("team-lead", ag, "delegated task", "completed");
              addInteraction(ag, "team-lead", "task result", "completed");
            }
            steps.push({ node: "team-lead (summary)", output: result.output ?? "" });

            addMessage({
              role: "assistant",
              content: {
                steps,
                agent_costs: result.agent_costs,
                usage: {
                  output_tokens: result.total_tokens,
                  model,
                },
                elapsed_s: result.elapsed_s,
              },
              timestamp: Date.now(),
            });
          } else {
            addMessage({
              role: "assistant",
              content: `Team error: ${result.error ?? "Unknown error"}`,
              timestamp: Date.now(),
            });
          }

          // Signal running = false
          useAppStore.setState({ isStreaming: false });
        }
      }

      // HITL events — add message with HITL metadata
      if (t === "clarification.request" || t === "interrupt") {
        const msg = String(
          (d.message as string | undefined) ??
            (t === "interrupt" ? "Approval required" : "Please choose an option:")
        );
        addMessage({
          role: "assistant",
          content: msg,
          timestamp: Date.now(),
          // Pass HITL metadata via a special type assertion
          ...(t === "clarification.request"
            ? { hitlType: "options", options: d.options, runId: d.run_id }
            : { hitlType: "interrupt", runId: d.run_id }),
        } as unknown as import("@/api/types").ChatMessage);
      }

      // Tool call display in chat
      if (t === "agent.tool_call" && d.tool_name) {
        addMessage({
          role: "system",
          content: `TOOL_CALL:${JSON.stringify({
            agent: event.agent_name,
            toolName: d.tool_name,
            arguments: d.arguments,
            toolCallId: d.tool_call_id,
          })}`,
          timestamp: Date.now(),
        });
      }
      if (t === "agent.tool_result" && d.tool_call_id) {
        addMessage({
          role: "system",
          content: `TOOL_RESULT:${JSON.stringify({
            toolCallId: d.tool_call_id,
            success: d.success,
            output: String(d.output ?? "").slice(0, 300),
          })}`,
          timestamp: Date.now(),
        });
      }
    },
    [
      applyEvent,
      addActivityItem,
      addInteraction,
      updateInteraction,
      addMessage,
      setPendingTeamJob,
    ]
  );

  // --- Event bus WebSocket ---
  const connectEventWs = useCallback(() => {
    if (pageUnloading.current || !mountedRef.current) return;
    if (wsRef.current && wsRef.current.readyState <= WebSocket.OPEN) {
      wsRef.current.onclose = null;
      wsRef.current.close();
    }

    const ws = new WebSocket(buildWsUrl("/ws"));
    wsRef.current = ws;

    ws.onopen = () => {
      if (!mountedRef.current) return;
      setWsConnected(true);
      eventReconnectAttempt.current = 0;
    };

    ws.onclose = (e) => {
      if (!mountedRef.current) return;
      setWsConnected(false);
      if (!pageUnloading.current && e.code !== 1001) {
        const delay = getBackoffMs(eventReconnectAttempt.current++);
        setTimeout(connectEventWs, delay);
      }
    };

    ws.onerror = () => ws.close();

    ws.onmessage = (msg) => {
      if (!mountedRef.current) return;
      try {
        const payload = JSON.parse(msg.data as string) as {
          type: "snapshot" | "event";
          data: Snapshot | OrchestratorEvent;
        };
        if (payload.type === "snapshot") {
          applySnapshot(payload.data as Snapshot);
        } else if (payload.type === "event") {
          processEvent(payload.data as OrchestratorEvent);
        }
      } catch (err) {
        console.error("WS message parse error:", err);
      }
    };
  }, [applySnapshot, processEvent, setWsConnected]);

  // --- Streaming WebSocket ---
  const connectStreamWs = useCallback(() => {
    if (pageUnloading.current || !mountedRef.current) return;
    if (streamWsRef.current && streamWsRef.current.readyState <= WebSocket.OPEN) {
      streamWsRef.current.onclose = null;
      streamWsRef.current.close();
    }

    const ws = new WebSocket(buildWsUrl("/ws/stream"));
    streamWsRef.current = ws;

    ws.onclose = (e) => {
      if (!mountedRef.current) return;
      if (!pageUnloading.current && e.code !== 1001) {
        const delay = getBackoffMs(streamReconnectAttempt.current++);
        setTimeout(connectStreamWs, delay);
      }
    };

    ws.onerror = () => ws.close();

    ws.onmessage = (msg) => {
      if (!mountedRef.current) return;
      try {
        const data = JSON.parse(msg.data as string) as {
          type: string;
          content?: string;
          usage?: { output_tokens?: number; model?: string };
          elapsed_s?: number;
          speed?: number;
          error?: string;
        };

        if (data.type === "token") {
          appendStreamChunk(data.content ?? "");
        } else if (data.type === "done") {
          finalizeStream(data);
          useAppStore.setState({ isStreaming: false });
          streamReconnectAttempt.current = 0;
        } else if (data.type === "error") {
          clearStreamBuffer();
          addMessage({
            role: "assistant",
            content: `Error: ${data.error ?? "Unknown error"}`,
            timestamp: Date.now(),
          });
          useAppStore.setState({ isStreaming: false });
        }
      } catch (err) {
        console.error("Stream WS message parse error:", err);
      }
    };
  }, [appendStreamChunk, finalizeStream, clearStreamBuffer, addMessage]);

  // Send a prompt through the streaming WebSocket
  const sendStreamPrompt = useCallback(
    (payload: {
      prompt: string;
      model: string;
      provider: string;
      conversation_id?: string | null;
      file_context?: string;
    }) => {
      if (streamWsRef.current?.readyState === WebSocket.OPEN) {
        streamWsRef.current.send(JSON.stringify(payload));
        return true;
      }
      return false;
    },
    []
  );

  // Mount effect
  useEffect(() => {
    mountedRef.current = true;
    connectEventWs();
    connectStreamWs();

    const handleBeforeUnload = () => {
      pageUnloading.current = true;
      wsRef.current?.close();
      streamWsRef.current?.close();
    };

    window.addEventListener("beforeunload", handleBeforeUnload);

    return () => {
      mountedRef.current = false;
      window.removeEventListener("beforeunload", handleBeforeUnload);
      wsRef.current?.close();
      streamWsRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    sendStreamPrompt,
    isStreamWsReady: () =>
      streamWsRef.current?.readyState === WebSocket.OPEN,
  };
}
