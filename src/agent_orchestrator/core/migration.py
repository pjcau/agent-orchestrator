"""Migration wizard — import configurations from LangGraph, CrewAI, AutoGen."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MigrationResult:
    """Result of a migration import."""

    success: bool
    source_format: str
    agents_imported: int = 0
    nodes_imported: int = 0
    edges_imported: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


class MigrationManager:
    """Import and convert configurations from other orchestration frameworks.

    Supported formats:
    - LangGraph: graph definitions with nodes and edges
    - CrewAI: agent/task/crew definitions
    - AutoGen: agent configurations

    All imports produce a normalized dict that can be fed into
    ConfigManager or GraphTemplateStore.
    """

    def __init__(self) -> None:
        self._supported_formats = {"langgraph", "crewai", "autogen"}

    @property
    def supported_formats(self) -> list[str]:
        """Return list of supported import formats."""
        return sorted(self._supported_formats)

    def detect_format(self, data: dict[str, Any]) -> str | None:
        """Auto-detect the source format from a config dict.

        Returns format name or None if unrecognized.
        """
        # LangGraph: has "nodes" and "edges" at top level
        if "nodes" in data and "edges" in data and "agents" not in data:
            return "langgraph"
        # CrewAI: has "agents" and "tasks" and optionally "crew"
        if "agents" in data and "tasks" in data:
            return "crewai"
        # AutoGen: has "agents" with "llm_config" inside
        if "agents" in data and any(
            isinstance(a, dict) and "llm_config" in a for a in data.get("agents", [])
        ):
            return "autogen"
        return None

    def import_config(
        self, data: dict[str, Any], source_format: str | None = None
    ) -> MigrationResult:
        """Import a configuration dict from another framework.

        If source_format is None, auto-detection is attempted.
        The result.data contains normalized agent/graph configs.
        """
        fmt = source_format or self.detect_format(data)
        if fmt is None:
            return MigrationResult(
                success=False,
                source_format="unknown",
                errors=["Could not detect source format"],
            )
        if fmt not in self._supported_formats:
            return MigrationResult(
                success=False,
                source_format=fmt,
                errors=[f"Unsupported format: '{fmt}'"],
            )

        if fmt == "langgraph":
            return self._import_langgraph(data)
        elif fmt == "crewai":
            return self._import_crewai(data)
        elif fmt == "autogen":
            return self._import_autogen(data)

        return MigrationResult(success=False, source_format=fmt, errors=["Internal error"])

    def _import_langgraph(self, data: dict[str, Any]) -> MigrationResult:
        """Convert LangGraph graph definition to our format."""
        warnings: list[str] = []
        nodes_raw = data.get("nodes", [])
        edges_raw = data.get("edges", [])

        nodes = []
        for n in nodes_raw:
            if isinstance(n, dict):
                name = n.get("name", n.get("id", ""))
                node_type = n.get("type", "custom")
                # Map LangGraph node types
                if node_type in ("llm", "chat_model", "chatmodel"):
                    node_type = "llm"
                elif node_type not in ("llm", "custom", "subgraph"):
                    warnings.append(f"Node '{name}': mapped unknown type '{node_type}' to 'custom'")
                    node_type = "custom"
                nodes.append(
                    {
                        "name": name,
                        "type": node_type,
                        "config": n.get("config", n.get("kwargs", {})),
                    }
                )
            elif isinstance(n, str):
                nodes.append({"name": n, "type": "custom", "config": {}})

        edges = []
        for e in edges_raw:
            if isinstance(e, dict):
                edges.append(
                    {
                        "source": e.get("source", e.get("from", "")),
                        "target": e.get("target", e.get("to", "")),
                        "condition": e.get("condition"),
                    }
                )
            elif isinstance(e, (list, tuple)) and len(e) >= 2:
                edges.append({"source": e[0], "target": e[1], "condition": None})

        return MigrationResult(
            success=True,
            source_format="langgraph",
            nodes_imported=len(nodes),
            edges_imported=len(edges),
            warnings=warnings,
            data={"nodes": nodes, "edges": edges, "name": data.get("name", "imported_graph")},
        )

    def _import_crewai(self, data: dict[str, Any]) -> MigrationResult:
        """Convert CrewAI agent/task definitions to our format."""
        warnings: list[str] = []
        agents_raw = data.get("agents", [])
        tasks_raw = data.get("tasks", [])

        agents = []
        for a in agents_raw:
            if isinstance(a, dict):
                name = a.get("name", a.get("role", "unnamed"))
                # Normalize name to valid identifier
                safe_name = name.lower().replace(" ", "-").replace("_", "-")
                agents.append(
                    {
                        "name": safe_name,
                        "role": a.get("goal", a.get("backstory", a.get("role", ""))),
                        "provider_key": a.get("llm", "default"),
                        "tools": a.get("tools", []),
                    }
                )

        # Convert tasks to a simple list
        tasks = []
        for t in tasks_raw:
            if isinstance(t, dict):
                tasks.append(
                    {
                        "description": t.get("description", ""),
                        "agent": t.get("agent", ""),
                        "expected_output": t.get("expected_output", ""),
                    }
                )

        return MigrationResult(
            success=True,
            source_format="crewai",
            agents_imported=len(agents),
            warnings=warnings,
            data={"agents": agents, "tasks": tasks},
        )

    def _import_autogen(self, data: dict[str, Any]) -> MigrationResult:
        """Convert AutoGen agent configurations to our format."""
        warnings: list[str] = []
        agents_raw = data.get("agents", [])

        agents = []
        for a in agents_raw:
            if isinstance(a, dict):
                name = a.get("name", "unnamed")
                llm_config = a.get("llm_config", {})
                model = llm_config.get("model", "")

                # Map AutoGen agent types
                agent_type = a.get("type", "assistant")
                if agent_type == "user_proxy":
                    warnings.append(f"Agent '{name}': user_proxy mapped to viewer role")

                agents.append(
                    {
                        "name": name.lower().replace(" ", "-"),
                        "role": a.get("system_message", ""),
                        "provider_key": _guess_provider_key(model),
                        "tools": a.get("tools", []),
                        "model": model,
                    }
                )

        return MigrationResult(
            success=True,
            source_format="autogen",
            agents_imported=len(agents),
            warnings=warnings,
            data={"agents": agents},
        )

    def export_langgraph(self, graph_data: dict[str, Any]) -> dict[str, Any]:
        """Export our graph template format to LangGraph-compatible dict."""
        nodes = []
        for n in graph_data.get("nodes", []):
            nodes.append(
                {
                    "id": n.get("name", ""),
                    "type": n.get("type", "custom"),
                    "kwargs": n.get("config", {}),
                }
            )
        edges = []
        for e in graph_data.get("edges", []):
            edges.append(
                {
                    "from": e.get("source", ""),
                    "to": e.get("target", ""),
                    "condition": e.get("condition"),
                }
            )
        return {"nodes": nodes, "edges": edges}


def _guess_provider_key(model: str) -> str:
    """Guess provider key from model name."""
    model_lower = model.lower()
    if "gpt" in model_lower or "o1" in model_lower or "o3" in model_lower:
        return "openai"
    if "claude" in model_lower:
        return "anthropic"
    if "gemini" in model_lower or "gemma" in model_lower:
        return "google"
    if "llama" in model_lower or "qwen" in model_lower or "mistral" in model_lower:
        return "ollama"
    return "default"
