import { useState } from "react";
import { Header } from "@/components/layout/Header";
import { Sidebar } from "@/components/layout/Sidebar";
import { HistorySidebar } from "@/components/layout/HistorySidebar";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { GraphVisualizer } from "@/components/graph/GraphVisualizer";
import { InteractionTimeline } from "@/components/graph/InteractionTimeline";
import { AgentSelector } from "@/components/agents/AgentSelector";
import { SandboxPanel } from "@/components/sandbox/SandboxPanel";
import { PromptsPanel } from "@/components/prompts/PromptsPanel";
import { MetricsBar } from "@/components/metrics/MetricsBar";
import { useAppStore } from "@/stores/useAppStore";
import { useGraphReset, useSandboxStatus } from "@/api/hooks";
import apiClient from "@/api/client";

type LeftPanelMode = "history" | "prompts" | null;

/**
 * Main 3-column dashboard layout:
 * HistorySidebar | ChatPanel + GraphVisualizer | Sidebar (events/activity/cache)
 */
/** True iff we're being rendered into a mobile-width viewport. SSR-safe. */
const isMobileViewport = () =>
  typeof window !== "undefined" &&
  window.matchMedia("(max-width: 600px)").matches;

export function DashboardPage() {
  const [leftPanel, setLeftPanel] = useState<LeftPanelMode>(null);
  const [sandboxOpen, setSandboxOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  // On mobile the graph section is collapsed by default so the chat keeps
  // the full screen. The header bar remains tappable to expand it.
  const [graphCollapsed, setGraphCollapsed] = useState(isMobileViewport);
  const { sidebarOpen, setSidebarOpen, setSseMode, sseMode, reset } = useAppStore();
  const setBrowseOpen = useAppStore((s) => s.setBrowseOpen);
  const graphReset = useGraphReset();
  const { data: sandboxStatus } = useSandboxStatus();

  const handleToggleSidebar = () => setSidebarOpen(!sidebarOpen);
  const handleToggleSSE = () => setSseMode(!sseMode);
  const handleToggleMobileNav = () => setMobileNavOpen((o) => !o);

  const handleToggleHistory = () => {
    setLeftPanel((p) => (p === "history" ? null : "history"));
    setMobileNavOpen(false);
  };

  const handleTogglePrompts = () => {
    setLeftPanel((p) => (p === "prompts" ? null : "prompts"));
    setMobileNavOpen(false);
  };

  /**
   * Full Reset (B): wipes graph + chat + attached files + conversation memory,
   * both client-side and server-side.
   *
   * 1. DELETE /api/conversation/{id} — drops conversation memory on the server
   * 2. POST /api/graph/reset — clears server-side graph snapshot
   * 3. store.reset() — clears all client UI state (messages, attachedFiles,
   *    conversationId, graph nodes, events, activity, interactions, …) and
   *    removes the persisted localStorage entry.
   *
   * Server calls are best-effort: client state is always cleared so the user
   * never sees a stale dashboard even if the network fails.
   */
  const handleResetGraph = async () => {
    const currentConvId = useAppStore.getState().conversationId;
    try {
      if (currentConvId) {
        await apiClient
          .delete(`/api/conversation/${encodeURIComponent(currentConvId)}`)
          .catch((err) => {
            // Stale or unknown id — proceed with reset regardless.
            console.warn("DELETE /api/conversation failed:", err);
          });
      }
      await graphReset.mutateAsync();
    } catch (err) {
      console.warn("Graph reset call failed:", err);
    } finally {
      reset();
    }
  };

  return (
    <div className="dashboard">
      <Header
        onToggleSidebar={handleToggleSidebar}
        onToggleSSE={handleToggleSSE}
        onToggleMobileNav={handleToggleMobileNav}
        sidebarOpen={sidebarOpen}
        sseMode={sseMode}
      />

      {/* Mobile-only nav drawer (left side). Holds metrics + History/Prompts/
          Sandbox/SSE actions that on desktop live in the left-rail + header. */}
      {mobileNavOpen && (
        <>
          <div
            className="mobile-nav-backdrop"
            role="presentation"
            onClick={() => setMobileNavOpen(false)}
          />
          <aside className="mobile-nav" aria-label="Mobile navigation">
            <header className="mobile-nav__header">
              <h2 className="mobile-nav__title">Menu</h2>
              <button
                className="btn-icon"
                onClick={() => setMobileNavOpen(false)}
                aria-label="Close menu"
              >
                ×
              </button>
            </header>
            <section className="mobile-nav__section">
              <h3 className="mobile-nav__section-title">Session</h3>
              <MetricsBar />
            </section>
            <nav className="mobile-nav__section">
              <h3 className="mobile-nav__section-title">Panels</h3>
              <button
                className={`mobile-nav__item ${leftPanel === "history" ? "active" : ""}`}
                onClick={handleToggleHistory}
              >
                History
              </button>
              <button
                className={`mobile-nav__item ${leftPanel === "prompts" ? "active" : ""}`}
                onClick={handleTogglePrompts}
              >
                Prompts
              </button>
              {sandboxStatus?.enabled && (
                <button
                  className={`mobile-nav__item ${sandboxOpen ? "active" : ""}`}
                  onClick={() => {
                    setSandboxOpen((o) => !o);
                    setMobileNavOpen(false);
                  }}
                >
                  Sandbox
                  {sandboxStatus.active_sessions > 0 && (
                    <span className="sandbox-badge">{sandboxStatus.active_sessions}</span>
                  )}
                </button>
              )}
              <button
                className="mobile-nav__item"
                onClick={() => {
                  setBrowseOpen(true);
                  setMobileNavOpen(false);
                }}
                title="Browse workspace files"
              >
                Browse files
              </button>
              <button
                className={`mobile-nav__item ${sseMode ? "active" : ""}`}
                onClick={handleToggleSSE}
              >
                SSE mode {sseMode ? "✓" : ""}
              </button>
            </nav>
          </aside>
        </>
      )}

      <div className="dashboard__body">
        {/* Left rail — always visible, buttons in a column at the bottom */}
        <aside className="left-rail">
          <div className="left-rail__bottom">
            <button
              className={`btn-sidebar-toggle ${leftPanel === "history" ? "active" : ""}`}
              onClick={handleToggleHistory}
              title="Job history"
            >
              History
            </button>
            <button
              className={`btn-sidebar-toggle ${leftPanel === "prompts" ? "active" : ""}`}
              onClick={handleTogglePrompts}
              title="Prompt registry"
            >
              Prompts
            </button>
            {sandboxStatus?.enabled && (
              <button
                className={`btn-sidebar-toggle ${sandboxOpen ? "active" : ""}`}
                onClick={() => setSandboxOpen(!sandboxOpen)}
                title="Sandbox workspace"
              >
                Sandbox
                {sandboxStatus.active_sessions > 0 && (
                  <span className="sandbox-badge">{sandboxStatus.active_sessions}</span>
                )}
              </button>
            )}
          </div>
        </aside>

        {/* Left panel — History or Prompts.
            On mobile (≤600px) these become fixed left-side drawers with a
            backdrop that closes them on tap; on desktop they remain inline
            flex children of the dashboard body. */}
        {leftPanel !== null && (
          <>
            <div
              className="left-panel-backdrop"
              role="presentation"
              onClick={() => setLeftPanel(null)}
            />
            {leftPanel === "history" && <HistorySidebar />}
            {leftPanel === "prompts" && <PromptsPanel />}
          </>
        )}

        {/* Center — main content */}
        <main className="dashboard__main">
          {/* Top: Agent graph section.
              On mobile the body collapses behind a tappable title bar; when
              both visualizer and timeline are empty the whole section is
              hidden via CSS :has() to free the chat scroll area. */}
          <section
            className={`graph-section ${graphCollapsed ? "graph-section--collapsed" : ""}`}
          >
            <div className="graph-section__header">
              <button
                type="button"
                className="graph-section__title-btn"
                onClick={() => setGraphCollapsed((c) => !c)}
                aria-expanded={!graphCollapsed}
                aria-controls="graph-section-body"
                title={graphCollapsed ? "Expand graph" : "Collapse graph"}
              >
                <span className="graph-section__chevron" aria-hidden="true">
                  {graphCollapsed ? "▶" : "▼"}
                </span>
                <h2 className="section-title">Agent Interactions</h2>
              </button>
              <div className="graph-section__header-right">
                <AgentSelector />
                <button
                  className="btn-graph-ctrl"
                  onClick={handleResetGraph}
                  title="Reset all state"
                >
                  Reset
                </button>
              </div>
            </div>
            <div id="graph-section-body" className="graph-section__body">
              <GraphVisualizer />
              <InteractionTimeline />
            </div>
          </section>

          {/* Center: Chat */}
          <section className="chat-section">
            <ChatPanel />
          </section>

          {/* Bottom: Sandbox workspace */}
          {sandboxOpen && (
            <section className="sandbox-section">
              <SandboxPanel />
            </section>
          )}
        </main>

        {/* Right panel — Event logs, activity, cache.
            On mobile (≤600px) the sidebar becomes a fixed drawer that slides
            in from the right; the backdrop only appears on small screens (see
            .sidebar-backdrop in index.css) and closes the drawer on tap. */}
        {sidebarOpen && (
          <>
            <div
              className="sidebar-backdrop"
              role="presentation"
              onClick={() => setSidebarOpen(false)}
            />
            <Sidebar />
          </>
        )}
      </div>

    </div>
  );
}
