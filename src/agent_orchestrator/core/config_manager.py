"""Configuration manager — load, save, validate orchestrator configuration."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentConfigEntry:
    """Configuration for a single agent."""
    name: str
    role: str
    provider_key: str
    tools: list[str] = field(default_factory=list)
    max_steps: int = 10
    escalation_provider_key: str | None = None


@dataclass
class ProviderConfigEntry:
    """Configuration for a single provider."""
    key: str
    type: str  # "ollama", "openrouter", "openai", "anthropic", "google"
    model: str
    api_key: str | None = None
    base_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrchestratorConfiguration:
    """Complete orchestrator configuration."""
    version: str = "1.0.0"
    agents: list[AgentConfigEntry] = field(default_factory=list)
    providers: list[ProviderConfigEntry] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)  # enabled skill names
    routing_strategy: str = "local_first"
    budget_limit_usd: float | None = None
    offline_mode: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: float = 0.0


class ConfigManager:
    """Load, save, and validate orchestrator configuration.

    In-memory configuration store with JSON import/export.
    Tracks configuration history for rollback.
    """

    def __init__(self) -> None:
        self._config = OrchestratorConfiguration()
        self._history: list[OrchestratorConfiguration] = []

    @property
    def config(self) -> OrchestratorConfiguration:
        return self._config

    def update(self, config: OrchestratorConfiguration) -> OrchestratorConfiguration:
        """Update the configuration, saving the previous version to history."""
        self._history.append(self._config)
        config.updated_at = time.time()
        self._config = config
        return self._config

    def rollback(self) -> OrchestratorConfiguration | None:
        """Rollback to the previous configuration. Returns None if no history."""
        if not self._history:
            return None
        self._config = self._history.pop()
        return self._config

    def get_history(self) -> list[OrchestratorConfiguration]:
        """Return configuration history (oldest first)."""
        return list(self._history)

    def validate(self, config: OrchestratorConfiguration | None = None) -> list[str]:
        """Validate configuration. Returns list of error messages (empty = valid)."""
        cfg = config or self._config
        errors: list[str] = []

        # Check for duplicate agent names
        agent_names = [a.name for a in cfg.agents]
        if len(agent_names) != len(set(agent_names)):
            errors.append("Duplicate agent names found")

        # Check for duplicate provider keys
        provider_keys = [p.key for p in cfg.providers]
        if len(provider_keys) != len(set(provider_keys)):
            errors.append("Duplicate provider keys found")

        # Check agent provider references exist
        for agent in cfg.agents:
            if agent.provider_key and agent.provider_key not in provider_keys:
                errors.append(f"Agent '{agent.name}' references unknown provider '{agent.provider_key}'")
            if agent.escalation_provider_key and agent.escalation_provider_key not in provider_keys:
                errors.append(f"Agent '{agent.name}' escalation references unknown provider '{agent.escalation_provider_key}'")

        # Validate routing strategy
        valid_strategies = {"fixed", "local_first", "cost_optimized", "capability_based", "fallback_chain", "complexity_based", "split_execution"}
        if cfg.routing_strategy not in valid_strategies:
            errors.append(f"Unknown routing strategy: '{cfg.routing_strategy}'")

        # Validate provider types
        valid_types = {"ollama", "openrouter", "openai", "anthropic", "google", "local", "vllm"}
        for p in cfg.providers:
            if p.type not in valid_types:
                errors.append(f"Provider '{p.key}' has unknown type '{p.type}'")

        return errors

    def export_json(self, indent: int = 2) -> str:
        """Export configuration as JSON string."""
        return json.dumps(_config_to_dict(self._config), indent=indent)

    def import_json(self, json_str: str) -> OrchestratorConfiguration:
        """Import configuration from JSON string. Does NOT auto-apply; call update() to apply."""
        return _config_from_dict(json.loads(json_str))

    def add_agent(self, agent: AgentConfigEntry) -> None:
        """Add an agent to the current configuration."""
        self._history.append(_clone_config(self._config))
        self._config.agents.append(agent)
        self._config.updated_at = time.time()

    def remove_agent(self, name: str) -> bool:
        """Remove an agent by name. Returns True if found."""
        before = len(self._config.agents)
        self._config.agents = [a for a in self._config.agents if a.name != name]
        if len(self._config.agents) < before:
            self._config.updated_at = time.time()
            return True
        return False

    def add_provider(self, provider: ProviderConfigEntry) -> None:
        """Add a provider to the current configuration."""
        self._history.append(_clone_config(self._config))
        self._config.providers.append(provider)
        self._config.updated_at = time.time()

    def remove_provider(self, key: str) -> bool:
        """Remove a provider by key. Returns True if found."""
        before = len(self._config.providers)
        self._config.providers = [p for p in self._config.providers if p.key != key]
        if len(self._config.providers) < before:
            self._config.updated_at = time.time()
            return True
        return False

    def get_agent(self, name: str) -> AgentConfigEntry | None:
        """Get an agent config by name."""
        for a in self._config.agents:
            if a.name == name:
                return a
        return None

    def get_provider(self, key: str) -> ProviderConfigEntry | None:
        """Get a provider config by key."""
        for p in self._config.providers:
            if p.key == key:
                return p
        return None


def _config_to_dict(cfg: OrchestratorConfiguration) -> dict[str, Any]:
    return {
        "version": cfg.version,
        "routing_strategy": cfg.routing_strategy,
        "budget_limit_usd": cfg.budget_limit_usd,
        "offline_mode": cfg.offline_mode,
        "skills": cfg.skills,
        "metadata": cfg.metadata,
        "updated_at": cfg.updated_at,
        "agents": [
            {
                "name": a.name,
                "role": a.role,
                "provider_key": a.provider_key,
                "tools": a.tools,
                "max_steps": a.max_steps,
                "escalation_provider_key": a.escalation_provider_key,
            }
            for a in cfg.agents
        ],
        "providers": [
            {
                "key": p.key,
                "type": p.type,
                "model": p.model,
                "api_key": p.api_key,
                "base_url": p.base_url,
                "extra": p.extra,
            }
            for p in cfg.providers
        ],
    }


def _config_from_dict(data: dict[str, Any]) -> OrchestratorConfiguration:
    agents = [
        AgentConfigEntry(
            name=a["name"],
            role=a.get("role", ""),
            provider_key=a.get("provider_key", ""),
            tools=a.get("tools", []),
            max_steps=a.get("max_steps", 10),
            escalation_provider_key=a.get("escalation_provider_key"),
        )
        for a in data.get("agents", [])
    ]
    providers = [
        ProviderConfigEntry(
            key=p["key"],
            type=p.get("type", "ollama"),
            model=p.get("model", ""),
            api_key=p.get("api_key"),
            base_url=p.get("base_url"),
            extra=p.get("extra", {}),
        )
        for p in data.get("providers", [])
    ]
    return OrchestratorConfiguration(
        version=data.get("version", "1.0.0"),
        agents=agents,
        providers=providers,
        skills=data.get("skills", []),
        routing_strategy=data.get("routing_strategy", "local_first"),
        budget_limit_usd=data.get("budget_limit_usd"),
        offline_mode=data.get("offline_mode", False),
        metadata=data.get("metadata", {}),
        updated_at=data.get("updated_at", 0.0),
    )


def _clone_config(cfg: OrchestratorConfiguration) -> OrchestratorConfiguration:
    """Deep-clone a configuration for history."""
    return _config_from_dict(_config_to_dict(cfg))
