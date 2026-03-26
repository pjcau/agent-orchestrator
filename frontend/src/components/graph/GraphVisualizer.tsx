import React, { useMemo, useCallback } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  type Node,
  type Edge,
  type NodeTypes,
  BackgroundVariant,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useAppStore } from "@/stores/useAppStore";

interface AgentNodeData {
  label: string;
  status: "active" | "done" | "error" | "idle";
  color: string;
}

const AGENT_COLORS: Record<string, string> = {
  "team-lead": "#58a6ff",
  "backend": "#3fb950",
  "frontend": "#f778ba",
  "devops": "#d29922",
  "platform-engineer": "#bc8cff",
  "ai-engineer": "#39d2c0",
  "scout": "#f85149",
  "__start__": "#bc8cff",
  "__end__": "#3fb950",
};

const PASTEL_COLORS = [
  "#58a6ff", "#3fb950", "#f778ba", "#d29922",
  "#bc8cff", "#39d2c0", "#f85149", "#ffa657",
];

function getNodeColor(name: string): string {
  if (AGENT_COLORS[name]) return AGENT_COLORS[name];
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }
  return PASTEL_COLORS[Math.abs(hash) % PASTEL_COLORS.length];
}

/** Custom node renderer for agent nodes. */
function AgentNode({ data }: { data: AgentNodeData }) {
  const statusStyles: Record<string, React.CSSProperties> = {
    active: {
      background: `${data.color}30`,
      border: `2px solid ${data.color}`,
      boxShadow: `0 0 8px ${data.color}60`,
    },
    done: {
      background: "rgba(63,185,80,0.12)",
      border: "1.5px solid rgba(63,185,80,0.5)",
    },
    error: {
      background: "rgba(248,81,73,0.12)",
      border: "1.5px solid rgba(248,81,73,0.5)",
    },
    idle: {
      background: `${data.color}12`,
      border: `1.5px solid ${data.color}60`,
    },
  };

  const dotColors: Record<string, string> = {
    active: "#58a6ff",
    done: "#3fb950",
    error: "#f85149",
    idle: "#30363d",
  };

  return (
    <div
      className={`agent-node ${data.status === "active" ? "agent-node--active" : ""}`}
      style={{
        padding: "6px 12px 6px 28px",
        borderRadius: "6px",
        fontSize: "11px",
        color: data.status === "done" ? "#3fb950" : data.status === "error" ? "#f85149" : data.color,
        fontFamily: "var(--font-mono, monospace)",
        minWidth: "100px",
        position: "relative",
        ...statusStyles[data.status],
      }}
    >
      {/* Status dot */}
      <span
        style={{
          position: "absolute",
          left: "10px",
          top: "50%",
          transform: "translateY(-50%)",
          width: "8px",
          height: "8px",
          borderRadius: "50%",
          background: dotColors[data.status],
          display: "block",
        }}
      />
      {data.label === "__start__" ? "START" : data.label === "__end__" ? "END" : data.label}
    </div>
  );
}

const nodeTypes: NodeTypes = {
  agentNode: AgentNode as unknown as NodeTypes["agentNode"],
};

/** Uses @xyflow/react to render the agent interaction graph. */
export function GraphVisualizer() {
  const { graph, graphNodeStates } = useAppStore();

  const hasGraph = graph.nodes.length > 0 || graph.edges.length > 0;

  const { nodes, edges } = useMemo<{ nodes: Node[]; edges: Edge[] }>(() => {
    if (!hasGraph) return { nodes: [], edges: [] };

    const nodeCount = graph.nodes.length;
    const cols = Math.max(1, Math.ceil(Math.sqrt(nodeCount)));

    const flowNodes: Node[] = graph.nodes.map((name, i) => {
      const col = i % cols;
      const row = Math.floor(i / cols);
      const status = graphNodeStates[name] ?? "idle";

      return {
        id: name,
        type: "agentNode",
        position: { x: col * 160, y: row * 80 },
        data: {
          label: name,
          status,
          color: getNodeColor(name),
        } satisfies AgentNodeData,
      };
    });

    const flowEdges: Edge[] = graph.edges.flatMap((e) => {
      const targets = e.target ? [e.target] : (e.routes ?? []);
      return targets.map((t) => ({
        id: `${e.source}-${t}`,
        source: e.source,
        target: t,
        animated: graphNodeStates[e.source] === "active",
        style: {
          stroke: getNodeColor(e.source),
          opacity: 0.6,
        },
        markerEnd: {
          type: "arrowclosed" as const,
          color: getNodeColor(e.source),
        },
      }));
    });

    return { nodes: flowNodes, edges: flowEdges };
  }, [graph, graphNodeStates, hasGraph]);

  if (!hasGraph) {
    return (
      <div className="graph-empty">
        <span>Send a message to see agent interactions</span>
      </div>
    );
  }

  return (
    <div className="graph-visualizer" style={{ width: "100%", height: "200px" }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        nodesDraggable
        elementsSelectable
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#2a2a3e" variant={BackgroundVariant.Dots} gap={20} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
