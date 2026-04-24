import { useState } from "react";
import { Header } from "@/components/layout/Header";
import { Sidebar } from "@/components/layout/Sidebar";
import { HistorySidebar } from "@/components/layout/HistorySidebar";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { GraphVisualizer } from "@/components/graph/GraphVisualizer";
import { AgentSelector } from "@/components/agents/AgentSelector";
import { SandboxPanel } from "@/components/sandbox/SandboxPanel";
import { PromptsPanel } from "@/components/prompts/PromptsPanel";
import { useAppStore } from "@/stores/useAppStore";
import { useGraphReset, useSandboxStatus } from "@/api/hooks";

type LeftPanelMode = "history" | "prompts" | null;

/**
 * Main 3-column dashboard layout:
 * HistorySidebar | ChatPanel + GraphVisualizer | Sidebar (events/activity/cache)
 */
export function DashboardPage() {
  const [leftPanel, setLeftPanel] = useState<LeftPanelMode>(null);
  const [sandboxOpen, setSandboxOpen] = useState(false);
  const { sidebarOpen, setSidebarOpen, setSseMode, sseMode, reset } = useAppStore();
  const graphReset = useGraphReset();
  const { data: sandboxStatus } = useSandboxStatus();

  const handleToggleSidebar = () => setSidebarOpen(!sidebarOpen);
  const handleToggleSSE = () => setSseMode(!sseMode);

  const handleToggleHistory = () => {
    setLeftPanel((p) => (p === "history" ? null : "history"));
  };

  const handleTogglePrompts = () => {
    setLeftPanel((p) => (p === "prompts" ? null : "prompts"));
  };

  const handleResetGraph = async () => {
    try {
      await graphReset.mutateAsync();
      reset();
    } catch (err) {
      console.error("Graph reset failed:", err);
    }
  };

  return (
    <div className="dashboard">
      <Header
        onToggleSidebar={handleToggleSidebar}
        onToggleSSE={handleToggleSSE}
        sidebarOpen={sidebarOpen}
        sseMode={sseMode}
      />

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

        {/* Left panel — History or Prompts */}
        {leftPanel === "history" && <HistorySidebar />}
        {leftPanel === "prompts" && <PromptsPanel />}

        {/* Center — main content */}
        <main className="dashboard__main">
          {/* Top: Agent graph section */}
          <section className="graph-section">
            <div className="graph-section__header">
              <div className="graph-section__header-left">
                <h2 className="section-title">Agent Interactions</h2>
              </div>
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
            <GraphVisualizer />
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

        {/* Right panel — Event logs, activity, cache */}
        {sidebarOpen && <Sidebar />}
      </div>

    </div>
  );
}
