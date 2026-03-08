"""Graph template store — define, version, and rebuild StateGraphs from data.

Templates describe a graph as a plain data structure (dataclasses).  They can
be serialised to/from JSON or a plain dict.  YAML support can be added later
with ``pip install pyyaml`` and a thin adapter.

Versioning: every ``save()`` call auto-increments the version number so the
full history of a named template is preserved in memory.

Usage::

    store = GraphTemplateStore()

    tmpl = GraphTemplate(
        name="summarise",
        description="Summarise a document",
        version=1,
        nodes=[
            NodeTemplate("fetch", "custom", {"function_name": "fetch_doc"}),
            NodeTemplate("summarise", "llm", {
                "system": "Summarise the document.",
                "prompt_key": "document",
                "output_key": "summary",
                "provider": "claude",
            }),
        ],
        edges=[
            EdgeTemplate("__start__", "fetch"),
            EdgeTemplate("fetch", "summarise"),
            EdgeTemplate("summarise", "__end__"),
        ],
        created_at=time.time(),
    )

    store.save(tmpl)
    graph = store.build_graph("summarise", providers={"claude": my_provider})
    compiled = graph.compile()
    result = await compiled.invoke({"document": "..."})
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .graph import StateGraph, NodeFunc
from .provider import Provider
from .llm_nodes import llm_node


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class NodeTemplate:
    """Descriptor for a single graph node.

    ``type`` can be:

    - ``"llm"``: build with :func:`llm_node`. ``config`` keys: ``system``,
      ``prompt_key`` (default ``"input"``), ``output_key`` (default
      ``"output"``), ``provider`` (key into the providers dict passed to
      :meth:`GraphTemplateStore.build_graph`).
    - ``"custom"``: look up ``config["function_name"]`` in the
      ``node_registry`` dict passed to :meth:`GraphTemplateStore.build_graph`.
    - ``"subgraph"``: look up ``config["template_name"]`` in the store itself
      and recursively build it, then wrap it in a
      :class:`~agent_orchestrator.core.graph_patterns.SubGraphNode`.
    """

    name: str
    type: str  # "llm" | "custom" | "subgraph"
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class EdgeTemplate:
    """Descriptor for a directed edge between two nodes.

    ``condition`` is an optional string key used as a hint for conditional
    edges.  When ``condition`` is set, the edge is registered as a
    conditional edge using a router that reads ``state["_route"]`` and
    returns the value — callers need to ensure the node function writes that
    key.  ``None`` means a plain fixed edge.
    """

    source: str
    target: str
    condition: str | None = None


@dataclass
class GraphTemplate:
    """A complete, versioned graph specification."""

    name: str
    description: str
    version: int
    nodes: list[NodeTemplate]
    edges: list[EdgeTemplate]
    created_at: float
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class GraphTemplateStore:
    """In-memory store for graph templates with versioning.

    Each named template can have multiple versions.  Versions start at 1 and
    auto-increment on every :meth:`save` call.  The original ``version``
    field on the :class:`GraphTemplate` passed to :meth:`save` is ignored and
    replaced with the next auto-assigned version.
    """

    def __init__(self) -> None:
        # name -> list of GraphTemplate, ordered by version (index 0 = v1)
        self._templates: dict[str, list[GraphTemplate]] = {}

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def save(self, template: GraphTemplate) -> GraphTemplate:
        """Persist a template; auto-assigns the next version number.

        Returns the stored template (with its final version number set).
        """
        versions = self._templates.setdefault(template.name, [])
        next_version = len(versions) + 1
        stored = GraphTemplate(
            name=template.name,
            description=template.description,
            version=next_version,
            nodes=list(template.nodes),
            edges=list(template.edges),
            created_at=template.created_at,
            metadata=dict(template.metadata),
        )
        versions.append(stored)
        return stored

    def get(self, name: str, version: int | None = None) -> GraphTemplate | None:
        """Return a template by name.  Returns the latest version when
        *version* is ``None``."""
        versions = self._templates.get(name)
        if not versions:
            return None
        if version is None:
            return versions[-1]
        return self.get_version(name, version)

    def get_version(self, name: str, version: int) -> GraphTemplate | None:
        """Return a specific version (1-based).  Returns ``None`` if not found."""
        versions = self._templates.get(name)
        if not versions:
            return None
        index = version - 1
        if index < 0 or index >= len(versions):
            return None
        return versions[index]

    def list_templates(self) -> list[str]:
        """Return the names of all stored templates."""
        return list(self._templates.keys())

    def get_versions(self, name: str) -> list[int]:
        """Return all stored version numbers for a named template."""
        versions = self._templates.get(name, [])
        return [t.version for t in versions]

    def delete(self, name: str) -> bool:
        """Delete all versions of a template.  Returns True if anything was deleted."""
        if name in self._templates:
            del self._templates[name]
            return True
        return False

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def export_dict(self, name: str, version: int | None = None) -> dict[str, Any]:
        """Export a template as a plain dict (JSON-serialisable)."""
        tmpl = self.get(name, version)
        if tmpl is None:
            raise KeyError(f"Template not found: {name!r} (version={version})")
        return _template_to_dict(tmpl)

    def import_dict(self, data: dict[str, Any]) -> GraphTemplate:
        """Reconstruct a :class:`GraphTemplate` from a plain dict.

        Does NOT save it; call :meth:`save` if persistence is wanted.
        """
        return _template_from_dict(data)

    def to_json(self, name: str, version: int | None = None, *, indent: int = 2) -> str:
        """Serialise a template to a JSON string."""
        return json.dumps(self.export_dict(name, version), indent=indent)

    def from_json(self, json_str: str) -> GraphTemplate:
        """Deserialise a :class:`GraphTemplate` from a JSON string.

        Does NOT save it automatically.
        """
        return self.import_dict(json.loads(json_str))

    # Aliases with YAML-style names so callers can use the roadmap API names.
    # YAML support can be wired in later by replacing these with pyyaml calls.

    def export_yaml(self, name: str, version: int | None = None) -> str:
        """Export template as a JSON string (YAML-named alias for :meth:`to_json`).

        To get real YAML output install ``pyyaml`` and replace this method body
        with ``import yaml; return yaml.safe_dump(self.export_dict(...))``.
        """
        return self.to_json(name, version)

    def import_yaml(self, yaml_str: str) -> GraphTemplate:
        """Import template from a JSON string (YAML-named alias for :meth:`from_json`).

        To accept real YAML install ``pyyaml`` and replace with
        ``import yaml; return self.import_dict(yaml.safe_load(yaml_str))``.
        """
        return self.from_json(yaml_str)

    # ------------------------------------------------------------------
    # Graph building
    # ------------------------------------------------------------------

    def build_graph(
        self,
        name: str,
        providers: dict[str, Provider] | None = None,
        node_registry: dict[str, NodeFunc] | None = None,
        version: int | None = None,
    ) -> StateGraph:
        """Build a :class:`~agent_orchestrator.core.graph.StateGraph` from a template.

        Args:
            name: Template name.
            providers: Map of provider key -> :class:`~agent_orchestrator.core.provider.Provider`
                instance.  Required for ``type="llm"`` nodes.
            node_registry: Map of function name -> async node function.
                Required for ``type="custom"`` nodes.
            version: Template version to use; latest if ``None``.

        Returns:
            An uncompiled :class:`~agent_orchestrator.core.graph.StateGraph`.
        """
        tmpl = self.get(name, version)
        if tmpl is None:
            raise KeyError(f"Template not found: {name!r} (version={version})")

        providers = providers or {}
        node_registry = node_registry or {}

        graph = StateGraph()

        # Build nodes.
        for node_tmpl in tmpl.nodes:
            func = self._build_node_func(node_tmpl, providers, node_registry)
            graph.add_node(node_tmpl.name, func)

        # Accumulate conditional edges: source -> list[EdgeTemplate]
        conditional: dict[str, list[EdgeTemplate]] = {}
        fixed_edges: list[EdgeTemplate] = []

        for edge_tmpl in tmpl.edges:
            if edge_tmpl.condition is not None:
                conditional.setdefault(edge_tmpl.source, []).append(edge_tmpl)
            else:
                fixed_edges.append(edge_tmpl)

        # Add fixed edges.
        for edge_tmpl in fixed_edges:
            graph.add_edge(edge_tmpl.source, edge_tmpl.target)

        # Add conditional edges grouped by source node.
        for source, cond_edges in conditional.items():
            route_map = {e.condition: e.target for e in cond_edges}  # type: ignore[misc]

            def make_router(rm: dict[str, str]) -> Callable[[dict[str, Any]], str]:
                def router(state: dict[str, Any]) -> str:
                    key = state.get("_route", "")
                    return rm.get(key, next(iter(rm.values())))

                return router

            graph.add_conditional_edges(source, make_router(route_map), route_map)

        return graph

    def _build_node_func(
        self,
        node_tmpl: NodeTemplate,
        providers: dict[str, Provider],
        node_registry: dict[str, NodeFunc],
    ) -> NodeFunc:
        """Resolve a NodeTemplate to a callable async node function."""
        if node_tmpl.type == "llm":
            cfg = node_tmpl.config
            provider_key = cfg.get("provider", "")
            provider = providers.get(provider_key)
            if provider is None:
                if not providers:
                    raise ValueError(
                        f"Node {node_tmpl.name!r} requires a provider but "
                        f"providers dict is empty (wanted {provider_key!r})"
                    )
                # Fall back to first available provider.
                provider = next(iter(providers.values()))

            return llm_node(
                provider=provider,
                system=cfg.get("system", ""),
                prompt_key=cfg.get("prompt_key", "input"),
                output_key=cfg.get("output_key", "output"),
            )

        if node_tmpl.type == "custom":
            func_name = node_tmpl.config.get("function_name", "")
            func = node_registry.get(func_name)
            if func is None:
                raise ValueError(
                    f"Node {node_tmpl.name!r}: custom function {func_name!r} "
                    f"not found in node_registry (available: {list(node_registry.keys())})"
                )
            return func

        if node_tmpl.type == "subgraph":
            from .graph_patterns import SubGraphNode  # local import to avoid circularity

            template_name = node_tmpl.config.get("template_name", "")
            sub_graph = self.build_graph(template_name, providers, node_registry)
            compiled = sub_graph.compile()
            sub_node = SubGraphNode(
                compiled,
                input_mapping=node_tmpl.config.get("input_mapping"),
                output_mapping=node_tmpl.config.get("output_mapping"),
            )
            return sub_node  # SubGraphNode.__call__ matches NodeFunc signature

        raise ValueError(
            f"Unknown node type {node_tmpl.type!r} for node {node_tmpl.name!r}. "
            f"Supported types: 'llm', 'custom', 'subgraph'."
        )


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _template_to_dict(tmpl: GraphTemplate) -> dict[str, Any]:
    return {
        "name": tmpl.name,
        "description": tmpl.description,
        "version": tmpl.version,
        "created_at": tmpl.created_at,
        "metadata": tmpl.metadata,
        "nodes": [{"name": n.name, "type": n.type, "config": n.config} for n in tmpl.nodes],
        "edges": [
            {"source": e.source, "target": e.target, "condition": e.condition} for e in tmpl.edges
        ],
    }


def _template_from_dict(data: dict[str, Any]) -> GraphTemplate:
    nodes = [
        NodeTemplate(
            name=n["name"],
            type=n["type"],
            config=n.get("config", {}),
        )
        for n in data.get("nodes", [])
    ]
    edges = [
        EdgeTemplate(
            source=e["source"],
            target=e["target"],
            condition=e.get("condition"),
        )
        for e in data.get("edges", [])
    ]
    return GraphTemplate(
        name=data["name"],
        description=data.get("description", ""),
        version=data.get("version", 1),
        nodes=nodes,
        edges=edges,
        created_at=data.get("created_at", time.time()),
        metadata=data.get("metadata", {}),
    )
