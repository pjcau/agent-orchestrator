"""MCP server interface — expose the orchestrator as a Model Context Protocol server.

This module provides the registry/data-structure layer only.
Actual transport (stdio / SSE) is not implemented here; a transport wrapper
would instantiate ``MCPServerRegistry`` and handle the wire protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema
    handler: str  # reference to handler, e.g. "agent.backend.execute"


@dataclass
class MCPResource:
    uri: str
    name: str
    description: str
    mime_type: str = "text/plain"


class MCPServerRegistry:
    """Registry of tools and resources exposed via MCP.

    This prepares the data structures for an MCP server.
    Actual transport (stdio/SSE) would wrap this registry.
    """

    def __init__(
        self,
        server_name: str = "agent-orchestrator",
        version: str = "0.8.0",
    ) -> None:
        self.server_name = server_name
        self.version = version
        self._tools: dict[str, MCPTool] = {}
        self._resources: dict[str, MCPResource] = {}

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def register_tool(self, tool: MCPTool) -> None:
        """Register an MCP tool."""
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> MCPTool | None:
        """Retrieve a tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[MCPTool]:
        """Return all registered tools."""
        return list(self._tools.values())

    def unregister_tool(self, name: str) -> bool:
        """Remove a tool registration. Returns True if it existed."""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    # ------------------------------------------------------------------
    # Resources
    # ------------------------------------------------------------------

    def register_resource(self, resource: MCPResource) -> None:
        """Register an MCP resource."""
        self._resources[resource.uri] = resource

    def get_resource(self, uri: str) -> MCPResource | None:
        """Retrieve a resource by URI."""
        return self._resources.get(uri)

    def list_resources(self) -> list[MCPResource]:
        """Return all registered resources."""
        return list(self._resources.values())

    def unregister_resource(self, uri: str) -> bool:
        """Remove a resource registration. Returns True if it existed."""
        if uri in self._resources:
            del self._resources[uri]
            return True
        return False

    # ------------------------------------------------------------------
    # Auto-registration helpers
    # ------------------------------------------------------------------

    def register_agent_tools(self, agent_configs: dict[str, Any]) -> None:
        """Auto-register tools from agent configs.

        Creates one MCPTool per agent named ``agent_run_{name}`` whose
        description is the agent's ``role`` value (or an empty string when
        not present).  The input schema accepts a single ``task`` string.
        """
        for agent_name, config in agent_configs.items():
            role = config.get("role", "") if isinstance(config, dict) else ""
            tool = MCPTool(
                name=f"agent_run_{agent_name}",
                description=role,
                input_schema={
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "The task to execute",
                        }
                    },
                    "required": ["task"],
                },
                handler=f"agent.{agent_name}.execute",
            )
            self.register_tool(tool)

    def register_skill_tools(
        self, skill_names: list[str], skill_registry: Any
    ) -> None:
        """Auto-register tools from the skill registry.

        Creates one MCPTool per skill named ``skill_{name}`` using the
        skill's own ``parameters`` dict as the input schema.
        """
        for skill_name in skill_names:
            skill = skill_registry.get(skill_name)
            if skill is None:
                continue
            tool = MCPTool(
                name=f"skill_{skill_name}",
                description=skill.description,
                input_schema=skill.parameters,
                handler=f"skill.{skill_name}.execute",
            )
            self.register_tool(tool)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def export_manifest(self) -> dict:
        """Export as an MCP server manifest dict for client discovery.

        Returns a dict with keys: ``name``, ``version``, ``tools``,
        ``resources``.
        """
        return {
            "name": self.server_name,
            "version": self.version,
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                    "handler": t.handler,
                }
                for t in self._tools.values()
            ],
            "resources": [
                {
                    "uri": r.uri,
                    "name": r.name,
                    "description": r.description,
                    "mime_type": r.mime_type,
                }
                for r in self._resources.values()
            ],
        }
