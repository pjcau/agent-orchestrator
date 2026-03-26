import React from "react";
import { useAgents } from "@/api/hooks";
import type { AgentInfo } from "@/api/types";
import { useAppStore } from "@/stores/useAppStore";

const CATEGORY_LABELS: Record<string, string> = {
  general: "General",
  "software-engineering": "Software Eng",
  "data-science": "Data Science",
  finance: "Finance",
  marketing: "Marketing",
  tooling: "Tooling",
};

const CATEGORY_COLORS: Record<string, string> = {
  general: "#00d4ff",
  "software-engineering": "#39d2c0",
  "data-science": "#bc8cff",
  finance: "#3fb950",
  marketing: "#f778ba",
  tooling: "#ffa657",
};

const CATEGORY_ORDER = [
  "general",
  "software-engineering",
  "data-science",
  "finance",
  "marketing",
  "tooling",
];

interface AgentBadgeProps {
  agent: AgentInfo;
  color: string;
}

function AgentBadge({ agent, color }: AgentBadgeProps) {
  const agentStates = useAppStore((s) => s.agents);
  const agentState = agentStates[agent.name];
  const status = agentState?.status ?? "idle";

  const desc = agent.description
    ? agent.description.split(" — ")[1] ?? agent.description
    : "";

  return (
    <span
      className={`agent-badge-mini agent-badge-mini--${status}`}
      title={`${agent.name}: ${desc}`}
      style={{ "--cat-color": color } as React.CSSProperties}
    >
      <span className="agent-dot-mini" />
      {agent.name}
    </span>
  );
}

/** Agent badges grouped by category. Mirrors the original app header badges. */
export function AgentSelector() {
  const { data: agentRegistry } = useAgents();

  if (!agentRegistry) {
    return <div className="agent-badges" />;
  }

  const { categories } = agentRegistry;

  // If no categories, render flat list
  if (!categories || Object.keys(categories).length === 0) {
    return (
      <div className="agent-badges">
        {agentRegistry.agents.map((a) => (
          <AgentBadge key={a.name} agent={a} color="#00d4ff" />
        ))}
      </div>
    );
  }

  const sortedCats = Object.keys(categories).sort((a, b) => {
    const ia = CATEGORY_ORDER.indexOf(a);
    const ib = CATEGORY_ORDER.indexOf(b);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
  });

  return (
    <div className="agent-badges">
      {sortedCats.map((cat) => {
        const agents: AgentInfo[] = categories[cat] ?? [];
        if (!agents.length) return null;
        const color = CATEGORY_COLORS[cat] ?? "#666";
        const label = CATEGORY_LABELS[cat] ?? cat;

        return (
          <div
            key={cat}
            className="agent-category"
            style={{ "--cat-color": color } as React.CSSProperties}
          >
            <span className="agent-cat-label">{label}</span>
            {agents.map((a) => (
              <AgentBadge key={a.name} agent={a} color={color} />
            ))}
          </div>
        );
      })}
    </div>
  );
}
