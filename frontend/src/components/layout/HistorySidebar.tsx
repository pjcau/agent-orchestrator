import { useState } from "react";
import { useJobsList, useJobDetail, useDeleteJob } from "@/api/hooks";
import type { JobSession, JobRecord } from "@/api/types";
import apiClient from "@/api/client";
import { useAppStore } from "@/stores/useAppStore";
import { SessionExplorer } from "./SessionExplorer";

/** Left sidebar showing session history list with ability to load sessions. */
export function HistorySidebar() {
  const { data: jobsList, isLoading, refetch } = useJobsList();
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [explorerSessionId, setExplorerSessionId] = useState<string | null>(null);
  const [loadingSessionId, setLoadingSessionId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<{ sessionId: string; message: string } | null>(null);
  const { data: jobDetail } = useJobDetail(selectedSessionId ?? "");
  const deleteJob = useDeleteJob();
  const { addMessage, setConversationId, clearMessages } = useAppStore();

  // Click on a session item: select + auto-load records into chat.
  // Fetches records fresh from the API so no stale closure from useJobDetail.
  const handleSelectSession = async (sessionId: string) => {
    setSelectedSessionId(sessionId);
    setLoadError(null);
    await handleLoadSession(sessionId);
  };

  const handleDeleteSession = async (e: React.MouseEvent, sessionId: string) => {
    e.stopPropagation();
    if (!confirm(`Delete session ${sessionId}?`)) return;
    try {
      await deleteJob.mutateAsync(sessionId);
      if (selectedSessionId === sessionId) {
        setSelectedSessionId(null);
      }
    } catch (err) {
      console.error("Delete failed:", err);
    }
  };

  const handleLoadSession = async (sessionId: string) => {
    setLoadingSessionId(sessionId);
    setLoadError(null);
    try {
      const [restoreResp, detailResp] = await Promise.all([
        apiClient.post<{ conversation_id?: string; messages_restored?: number }>(
          `/api/jobs/${encodeURIComponent(sessionId)}/restore`
        ),
        apiClient.get<{ records: JobRecord[] }>(
          `/api/jobs/${encodeURIComponent(sessionId)}`
        ),
      ]);

      clearMessages();
      if (restoreResp.data.conversation_id) {
        setConversationId(restoreResp.data.conversation_id);
      }
      const restoredCount = restoreResp.data.messages_restored ?? 0;
      addMessage({
        role: "system",
        content: `Loaded session ${sessionId} (${restoredCount} messages restored)`,
        timestamp: Date.now(),
      });

      const records = detailResp.data?.records ?? [];
      if (records.length === 0) {
        addMessage({
          role: "system",
          content: "(session has no records yet)",
          timestamp: Date.now(),
        });
      } else {
        renderRecordsToChat(records);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.error("Load session failed:", err);
      setLoadError({ sessionId, message });
    } finally {
      setLoadingSessionId(null);
    }
  };

  const renderRecordsToChat = (records: JobRecord[]) => {
    for (const rec of records) {
      const type = rec.job_type;
      if (type === "prompt" || type === "stream") {
        if (rec.prompt) {
          addMessage({ role: "user", content: rec.prompt, timestamp: Date.now() });
        }
        const result = rec.result;
        if (result?.output) {
          addMessage({ role: "assistant", content: result.output, timestamp: Date.now() });
        } else if (result?.error) {
          addMessage({ role: "assistant", content: `Error: ${result.error}`, timestamp: Date.now() });
        }
      } else if (type === "agent_run") {
        if (rec.task) {
          addMessage({ role: "user", content: rec.task, timestamp: Date.now() });
        }
        const result = rec.result;
        if (result?.success && result.output) {
          addMessage({
            role: "assistant",
            content: {
              steps: [{ node: rec.agent ?? "agent", output: result.output }],
              usage: { output_tokens: result.total_tokens, model: rec.model },
              elapsed_s: result.elapsed_s,
            } as import("@/api/types").AssistantContent,
            timestamp: Date.now(),
          } as import("@/api/types").ChatMessage);
        } else if (result?.error) {
          addMessage({ role: "assistant", content: `Error: ${result.error}`, timestamp: Date.now() });
        }
      } else if (type === "team_run") {
        if (rec.task) {
          addMessage({ role: "user", content: rec.task, timestamp: Date.now() });
        }
        const result = rec.result;
        if (result?.success && result.output) {
          addMessage({
            role: "assistant",
            content: {
              steps: [{ node: "team (summary)", output: result.output }],
              agent_costs: result.agent_costs,
              usage: { output_tokens: result.total_tokens, model: rec.model },
              elapsed_s: result.elapsed_s,
            } as import("@/api/types").AssistantContent,
            timestamp: Date.now(),
          } as import("@/api/types").ChatMessage);
        }
      }
    }
  };

  const sessions = jobsList?.sessions ?? [];

  return (
    <aside className="history-sidebar" aria-label="Session history">
      <div className="history-sidebar__header">
        <h2 className="history-sidebar__title">History</h2>
        <button className="btn-text" onClick={() => refetch()}>
          Refresh
        </button>
      </div>

      {isLoading && <div className="empty-state">Loading...</div>}

      <div className="history-sessions">
        {sessions.length === 0 && !isLoading && (
          <div className="empty-state">No sessions yet</div>
        )}
        {sessions.map((session: JobSession) => {
          const ts = session.session_id.replace(/_/g, " ").slice(0, 15);
          const isSelected = selectedSessionId === session.session_id;
          const isLoading = loadingSessionId === session.session_id;
          const hasError = loadError?.sessionId === session.session_id;
          return (
            <div
              key={session.session_id}
              className={`history-session-item ${session.is_current ? "history-session-item--current" : ""} ${isSelected ? "history-session-item--selected" : ""} ${isLoading ? "history-session-item--loading" : ""} ${hasError ? "history-session-item--error" : ""}`}
              onClick={() => !isLoading && handleSelectSession(session.session_id)}
              title={isLoading ? "Loading session..." : "Click to load into chat"}
            >
              <div className="history-session-meta">
                <span className="history-session-ts">{ts}</span>
                {isLoading && <span className="badge badge--running">loading…</span>}
                {session.is_current && (
                  <span className="badge badge--running">current</span>
                )}
                {!session.is_current && (
                  <button
                    className="btn-session-delete"
                    onClick={(e) => handleDeleteSession(e, session.session_id)}
                    title="Delete session"
                  >
                    &times;
                  </button>
                )}
              </div>
              <div className="history-session-prompt">
                {session.first_prompt ?? "(no prompt)"}
              </div>
              <div className="history-session-stats">
                {session.records} records &middot; {session.files} files
              </div>
              {hasError && (
                <div className="history-session-error" role="alert">
                  Failed to load: {loadError!.message}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Detail panel for selected session */}
      {selectedSessionId && jobDetail && (
        <div className="history-detail">
          <div className="history-detail__actions">
            <button
              className="btn-primary"
              onClick={() => handleLoadSession(selectedSessionId)}
              disabled={loadingSessionId === selectedSessionId}
            >
              {loadingSessionId === selectedSessionId ? "Loading…" : "Reload into chat"}
            </button>
            <button
              className="btn-explorer-action"
              onClick={() =>
                setExplorerSessionId(
                  explorerSessionId === selectedSessionId
                    ? null
                    : selectedSessionId
                )
              }
            >
              {explorerSessionId === selectedSessionId
                ? "Hide Files"
                : "View Files"}
            </button>
          </div>
          {explorerSessionId === selectedSessionId && (
            <SessionExplorer
              sessionId={selectedSessionId}
              onClose={() => setExplorerSessionId(null)}
            />
          )}
          {jobDetail.records.map((r: JobRecord) => {
            const prompt = r.prompt ?? r.task ?? "";
            const result = r.result ?? {};
            const output = result.output ?? result.error ?? "";
            const tokens = result.total_tokens ?? 0;
            const cost = result.total_cost_usd
              ? `$${result.total_cost_usd.toFixed(4)}`
              : "";
            return (
              <div key={r.job_number} className="history-record">
                <div className="history-record__header">
                  <span className="history-record__type">{r.job_type[0].toUpperCase()}</span>
                  <span className="history-record__num">#{r.job_number}</span>
                  <span className="history-record__type-label">{r.job_type}</span>
                  {tokens > 0 && (
                    <span className="history-record__tokens">{tokens} tok</span>
                  )}
                  {cost && <span className="history-record__cost">{cost}</span>}
                </div>
                {prompt && (
                  <div className="history-record__prompt">
                    {prompt.slice(0, 200)}
                  </div>
                )}
                {output && (
                  <div className="history-record__output">
                    {output.slice(0, 300)}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </aside>
  );
}
