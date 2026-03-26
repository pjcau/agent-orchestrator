import { useAppStore } from "@/stores/useAppStore";

/** Displays team run status and active agent information. */
export function TeamRunPanel() {
  const { pendingTeamJobId, orchestratorStatus, agents } = useAppStore();

  if (!pendingTeamJobId && orchestratorStatus === "idle") {
    return null;
  }

  const activeAgents = Object.entries(agents)
    .filter(([, a]) => a.status === "running")
    .map(([name]) => name);

  return (
    <div className="team-run-panel">
      {pendingTeamJobId && (
        <div className="team-run-panel__job">
          <span className="team-run-panel__label">Job</span>
          <span className="team-run-panel__value">{pendingTeamJobId}</span>
        </div>
      )}
      {activeAgents.length > 0 && (
        <div className="team-run-panel__agents">
          <span className="team-run-panel__label">Active</span>
          {activeAgents.map((name) => (
            <span key={name} className="team-run-panel__agent">
              {name}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
