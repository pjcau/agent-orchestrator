"""Versioned REST API — route definitions and response models for /api/v1/.

This module defines the API contract (endpoints, request/response schemas)
as plain data structures. The actual FastAPI wiring happens in dashboard/app.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


API_VERSION = "v1"
API_PREFIX = f"/api/{API_VERSION}"


class HTTPMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"


@dataclass
class APIEndpoint:
    """Describes a single API endpoint."""
    path: str
    method: HTTPMethod
    summary: str
    description: str = ""
    request_schema: dict[str, Any] | None = None
    response_schema: dict[str, Any] | None = None
    auth_required: bool = True
    permissions: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass
class APIResponse:
    """Standard API response envelope."""
    success: bool
    data: Any = None
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class APIRegistry:
    """Registry of all API endpoints.

    Provides endpoint discovery and OpenAPI-compatible schema export.
    """

    def __init__(self) -> None:
        self._endpoints: list[APIEndpoint] = []
        self._register_defaults()

    def register(self, endpoint: APIEndpoint) -> None:
        """Register an API endpoint."""
        self._endpoints.append(endpoint)

    def list_endpoints(self, tag: str | None = None) -> list[APIEndpoint]:
        """List all endpoints, optionally filtered by tag."""
        if tag is None:
            return list(self._endpoints)
        return [e for e in self._endpoints if tag in e.tags]

    def get_endpoint(self, path: str, method: HTTPMethod) -> APIEndpoint | None:
        """Find an endpoint by path and method."""
        for e in self._endpoints:
            if e.path == path and e.method == method:
                return e
        return None

    def export_openapi_paths(self) -> dict[str, Any]:
        """Export endpoints as OpenAPI-compatible paths dict."""
        paths: dict[str, dict[str, Any]] = {}
        for ep in self._endpoints:
            full_path = f"{API_PREFIX}{ep.path}"
            if full_path not in paths:
                paths[full_path] = {}
            method_lower = ep.method.value.lower()
            entry: dict[str, Any] = {
                "summary": ep.summary,
                "description": ep.description,
                "tags": ep.tags,
                "security": [{"apiKey": []}] if ep.auth_required else [],
            }
            if ep.request_schema:
                entry["requestBody"] = {
                    "content": {"application/json": {"schema": ep.request_schema}}
                }
            if ep.response_schema:
                entry["responses"] = {
                    "200": {"content": {"application/json": {"schema": ep.response_schema}}}
                }
            else:
                entry["responses"] = {"200": {"description": "Success"}}
            paths[full_path][method_lower] = entry
        return paths

    def export_openapi_spec(self) -> dict[str, Any]:
        """Export a full OpenAPI 3.0 spec."""
        return {
            "openapi": "3.0.3",
            "info": {
                "title": "Agent Orchestrator API",
                "version": "1.0.0",
                "description": "Provider-agnostic AI agent orchestration framework.",
            },
            "paths": self.export_openapi_paths(),
            "components": {
                "securitySchemes": {
                    "apiKey": {
                        "type": "apiKey",
                        "in": "header",
                        "name": "X-API-Key",
                    }
                }
            },
        }

    def _register_defaults(self) -> None:
        """Register all default v1 API endpoints."""
        # --- Agents ---
        self.register(APIEndpoint(
            path="/agents",
            method=HTTPMethod.GET,
            summary="List agents",
            tags=["agents"],
            permissions=["agents.read"],
        ))
        self.register(APIEndpoint(
            path="/agents",
            method=HTTPMethod.POST,
            summary="Create agent",
            tags=["agents"],
            permissions=["agents.write"],
            request_schema={"type": "object", "properties": {"name": {"type": "string"}, "role": {"type": "string"}, "provider_key": {"type": "string"}}},
        ))
        self.register(APIEndpoint(
            path="/agents/{agent_id}",
            method=HTTPMethod.GET,
            summary="Get agent details",
            tags=["agents"],
            permissions=["agents.read"],
        ))
        self.register(APIEndpoint(
            path="/agents/{agent_id}",
            method=HTTPMethod.DELETE,
            summary="Delete agent",
            tags=["agents"],
            permissions=["agents.write"],
        ))
        self.register(APIEndpoint(
            path="/agents/{agent_id}/execute",
            method=HTTPMethod.POST,
            summary="Execute a task with an agent",
            tags=["agents"],
            permissions=["agents.execute"],
            request_schema={"type": "object", "properties": {"task": {"type": "string"}}},
        ))

        # --- Providers ---
        self.register(APIEndpoint(
            path="/providers",
            method=HTTPMethod.GET,
            summary="List providers",
            tags=["providers"],
            permissions=["config.read"],
        ))
        self.register(APIEndpoint(
            path="/providers/presets",
            method=HTTPMethod.GET,
            summary="List provider presets",
            tags=["providers"],
            permissions=["config.read"],
        ))
        self.register(APIEndpoint(
            path="/providers/presets/{preset_name}/activate",
            method=HTTPMethod.POST,
            summary="Activate a provider preset",
            tags=["providers"],
            permissions=["config.write"],
        ))

        # --- Projects ---
        self.register(APIEndpoint(
            path="/projects",
            method=HTTPMethod.GET,
            summary="List projects",
            tags=["projects"],
            permissions=["projects.read"],
        ))
        self.register(APIEndpoint(
            path="/projects",
            method=HTTPMethod.POST,
            summary="Create project",
            tags=["projects"],
            permissions=["projects.write"],
            request_schema={"type": "object", "properties": {"name": {"type": "string"}, "root_path": {"type": "string"}}},
        ))
        self.register(APIEndpoint(
            path="/projects/{project_id}",
            method=HTTPMethod.GET,
            summary="Get project details",
            tags=["projects"],
            permissions=["projects.read"],
        ))
        self.register(APIEndpoint(
            path="/projects/{project_id}",
            method=HTTPMethod.DELETE,
            summary="Delete project",
            tags=["projects"],
            permissions=["projects.write"],
        ))

        # --- Graphs ---
        self.register(APIEndpoint(
            path="/graphs/templates",
            method=HTTPMethod.GET,
            summary="List graph templates",
            tags=["graphs"],
            permissions=["config.read"],
        ))
        self.register(APIEndpoint(
            path="/graphs/templates/{name}",
            method=HTTPMethod.GET,
            summary="Get graph template",
            tags=["graphs"],
            permissions=["config.read"],
        ))
        self.register(APIEndpoint(
            path="/graphs/run",
            method=HTTPMethod.POST,
            summary="Run a graph",
            tags=["graphs"],
            permissions=["agents.execute"],
            request_schema={"type": "object", "properties": {"template": {"type": "string"}, "input": {"type": "object"}}},
        ))

        # --- Config ---
        self.register(APIEndpoint(
            path="/config",
            method=HTTPMethod.GET,
            summary="Get current configuration",
            tags=["config"],
            permissions=["config.read"],
        ))
        self.register(APIEndpoint(
            path="/config",
            method=HTTPMethod.PUT,
            summary="Update configuration",
            tags=["config"],
            permissions=["config.write"],
        ))
        self.register(APIEndpoint(
            path="/config/validate",
            method=HTTPMethod.POST,
            summary="Validate configuration",
            tags=["config"],
            permissions=["config.read"],
        ))

        # --- Users ---
        self.register(APIEndpoint(
            path="/users",
            method=HTTPMethod.GET,
            summary="List users",
            tags=["users"],
            permissions=["users.read"],
        ))
        self.register(APIEndpoint(
            path="/users",
            method=HTTPMethod.POST,
            summary="Create user",
            tags=["users"],
            permissions=["users.write"],
        ))

        # --- Health ---
        self.register(APIEndpoint(
            path="/health",
            method=HTTPMethod.GET,
            summary="Health check",
            tags=["system"],
            auth_required=False,
        ))
        self.register(APIEndpoint(
            path="/metrics",
            method=HTTPMethod.GET,
            summary="Prometheus metrics",
            tags=["system"],
            auth_required=False,
        ))

        # --- Audit ---
        self.register(APIEndpoint(
            path="/audit",
            method=HTTPMethod.GET,
            summary="Get audit log entries",
            tags=["audit"],
            permissions=["audit.read"],
        ))

        # --- Webhooks ---
        self.register(APIEndpoint(
            path="/webhooks",
            method=HTTPMethod.GET,
            summary="List webhooks",
            tags=["webhooks"],
            permissions=["config.read"],
        ))
        self.register(APIEndpoint(
            path="/webhooks",
            method=HTTPMethod.POST,
            summary="Register webhook",
            tags=["webhooks"],
            permissions=["config.write"],
        ))
        self.register(APIEndpoint(
            path="/webhooks/{webhook_id}/receive",
            method=HTTPMethod.POST,
            summary="Receive webhook event",
            tags=["webhooks"],
            auth_required=False,  # uses signature validation instead
        ))
