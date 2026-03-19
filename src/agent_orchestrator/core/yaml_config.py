"""YAML configuration loader with reflection-based class loading.

Supports:
- YAML-based orchestrator configuration (providers, agents, routing, budgets)
- Reflection-based class loading via ``module.path:ClassName`` syntax
- Environment variable substitution with ``${VAR_NAME}`` placeholders
- Config versioning with automatic upgrade from older formats
- Round-trip save/load
"""

from __future__ import annotations

import importlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

from .config_manager import (
    AgentConfigEntry,
    OrchestratorConfiguration,
    ProviderConfigEntry,
)

# Current config version; bump when the schema changes.
CURRENT_CONFIG_VERSION = 1

# Regex for ${VAR_NAME} placeholders.
_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class YAMLConfigError(Exception):
    """Raised when YAML configuration is invalid or cannot be loaded."""


# ---------------------------------------------------------------------------
# Reflection helper
# ---------------------------------------------------------------------------


def load_class(class_path: str) -> type:
    """Load a class by dotted path with colon separator.

    Example: ``"agent_orchestrator.providers.local:LocalProvider"``

    Raises ``YAMLConfigError`` if the module or class cannot be found.
    """
    if ":" not in class_path:
        raise YAMLConfigError(
            f"Invalid class path '{class_path}': expected 'module.path:ClassName'"
        )
    module_path, class_name = class_path.rsplit(":", 1)
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        raise YAMLConfigError(f"Module not found: '{module_path}'") from exc
    cls = getattr(module, class_name, None)
    if cls is None:
        raise YAMLConfigError(f"Class '{class_name}' not found in module '{module_path}'")
    if not isinstance(cls, type):
        raise YAMLConfigError(f"'{class_path}' is not a class")
    return cls


# ---------------------------------------------------------------------------
# Environment variable substitution
# ---------------------------------------------------------------------------


def substitute_env_vars(value: Any) -> Any:
    """Recursively replace ``${VAR}`` placeholders with environment values.

    Raises ``YAMLConfigError`` if a referenced variable is not set.
    """
    if isinstance(value, str):

        def _replace(match: re.Match) -> str:
            var_name = match.group(1)
            env_val = os.environ.get(var_name)
            if env_val is None:
                raise YAMLConfigError(f"Environment variable '{var_name}' is not set")
            return env_val

        return _ENV_VAR_RE.sub(_replace, value)
    if isinstance(value, dict):
        return {k: substitute_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute_env_vars(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# Config version upgraders
# ---------------------------------------------------------------------------

# Map of (from_version -> to_version) upgrade functions.
_UPGRADERS: dict[int, tuple[int, Any]] = {}


def _register_upgrader(from_version: int, to_version: int):
    """Decorator to register a config upgrade function."""

    def decorator(fn):
        _UPGRADERS[from_version] = (to_version, fn)
        return fn

    return decorator


# Example: upgrade from hypothetical v0 to v1 — adds config_version key.
@_register_upgrader(0, 1)
def _upgrade_v0_to_v1(data: dict) -> dict:
    """Upgrade from legacy (no config_version) to version 1."""
    data["config_version"] = 1
    # Ensure budgets section exists.
    if "budgets" not in data:
        data["budgets"] = {}
    return data


def upgrade_config(data: dict) -> dict:
    """Upgrade config data to the current version.

    Returns a new dict at ``CURRENT_CONFIG_VERSION``.
    """
    version = data.get("config_version", 0)
    while version < CURRENT_CONFIG_VERSION:
        if version not in _UPGRADERS:
            raise YAMLConfigError(
                f"No upgrade path from config version {version} to {CURRENT_CONFIG_VERSION}"
            )
        target_version, upgrader = _UPGRADERS[version]
        data = upgrader(data)
        version = target_version
    return data


# ---------------------------------------------------------------------------
# Budget config
# ---------------------------------------------------------------------------


@dataclass
class BudgetConfig:
    """Budget limits from YAML."""

    daily_limit_usd: float | None = None
    per_task_limit_usd: float | None = None
    alert_threshold_pct: float | None = None


# ---------------------------------------------------------------------------
# Parsed YAML config (richer than OrchestratorConfiguration)
# ---------------------------------------------------------------------------


@dataclass
class OrchestratorConfig:
    """Fully-parsed YAML configuration.

    This wraps the existing ``OrchestratorConfiguration`` and adds YAML-specific
    fields (class paths, budgets, fallback chain).
    """

    config_version: int = CURRENT_CONFIG_VERSION
    base_config: OrchestratorConfiguration = field(default_factory=OrchestratorConfiguration)
    provider_classes: dict[str, type] = field(default_factory=dict)
    budgets: BudgetConfig = field(default_factory=BudgetConfig)
    fallback_chain: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_VALID_STRATEGIES = {
    "fixed",
    "local_first",
    "cost_optimized",
    "capability_based",
    "fallback_chain",
    "complexity_based",
    "split_execution",
}


def validate_raw_config(data: dict) -> list[str]:
    """Validate raw YAML config dict. Returns list of error strings (empty = valid)."""
    errors: list[str] = []

    # config_version
    if "config_version" not in data:
        errors.append("Missing required field: 'config_version'")

    # providers
    providers = data.get("providers")
    if providers is not None:
        if not isinstance(providers, dict):
            errors.append("'providers' must be a mapping")
        else:
            for key, prov in providers.items():
                if not isinstance(prov, dict):
                    errors.append(f"Provider '{key}' must be a mapping")
                    continue
                if "use" not in prov:
                    errors.append(f"Provider '{key}' missing required field 'use'")

    # agents
    agents = data.get("agents")
    if agents is not None:
        if not isinstance(agents, dict):
            errors.append("'agents' must be a mapping")
        else:
            provider_keys = set((providers or {}).keys())
            for name, agent in agents.items():
                if not isinstance(agent, dict):
                    errors.append(f"Agent '{name}' must be a mapping")
                    continue
                prov_ref = agent.get("provider")
                if prov_ref and provider_keys and prov_ref not in provider_keys:
                    errors.append(f"Agent '{name}' references unknown provider '{prov_ref}'")

    # routing
    routing = data.get("routing")
    if routing is not None:
        if not isinstance(routing, dict):
            errors.append("'routing' must be a mapping")
        else:
            strategy = routing.get("strategy")
            if strategy and strategy not in _VALID_STRATEGIES:
                errors.append(f"Unknown routing strategy: '{strategy}'")

    # budgets
    budgets = data.get("budgets")
    if budgets is not None:
        if not isinstance(budgets, dict):
            errors.append("'budgets' must be a mapping")
        else:
            for field_name in ("daily_limit_usd", "per_task_limit_usd", "alert_threshold_pct"):
                val = budgets.get(field_name)
                if val is not None and not isinstance(val, (int, float)):
                    errors.append(f"budgets.{field_name} must be a number")

    return errors


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


class YAMLConfigLoader:
    """Load, validate, and save YAML-based orchestrator configuration."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else Path("orchestrator.yaml")

    @property
    def path(self) -> Path:
        return self._path

    # -- public API --

    def load(self, *, resolve_classes: bool = True) -> OrchestratorConfig:
        """Load and parse YAML config from ``self.path``.

        Args:
            resolve_classes: If True, use reflection to load provider classes.
                Set to False for validation-only workflows.

        Raises:
            YAMLConfigError: on missing file, invalid YAML, or validation failure.
        """
        self._ensure_yaml()
        if not self._path.exists():
            raise YAMLConfigError(f"Config file not found: {self._path}")
        raw_text = self._path.read_text(encoding="utf-8")
        return self.loads(raw_text, resolve_classes=resolve_classes)

    def loads(self, yaml_text: str, *, resolve_classes: bool = True) -> OrchestratorConfig:
        """Parse YAML text into an ``OrchestratorConfig``.

        Performs env var substitution, version upgrade, validation, and
        optional reflection-based class loading.
        """
        self._ensure_yaml()
        data = yaml.safe_load(yaml_text)
        if not isinstance(data, dict):
            raise YAMLConfigError("YAML root must be a mapping")

        # Env var substitution.
        data = substitute_env_vars(data)

        # Version upgrade.
        data = upgrade_config(data)

        # Validate.
        errors = self.validate(data)
        if errors:
            raise YAMLConfigError(
                "Config validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
            )

        return self._parse(data, resolve_classes=resolve_classes)

    def validate(self, config: dict) -> list[str]:
        """Validate a raw config dict. Returns list of error messages."""
        return validate_raw_config(config)

    def save(self, config: OrchestratorConfig, path: str | Path | None = None) -> None:
        """Serialize ``config`` back to YAML and write to disk."""
        self._ensure_yaml()
        target = Path(path) if path else self._path
        data = self._serialize(config)
        target.write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

    # -- internals --

    def _ensure_yaml(self) -> None:
        if yaml is None:
            raise YAMLConfigError(
                "PyYAML is required for YAML config. Install with: pip install pyyaml"
            )

    def _parse(self, data: dict, *, resolve_classes: bool = True) -> OrchestratorConfig:
        """Convert validated dict into ``OrchestratorConfig``."""
        providers_raw: dict = data.get("providers") or {}
        agents_raw: dict = data.get("agents") or {}
        routing_raw: dict = data.get("routing") or {}
        budgets_raw: dict = data.get("budgets") or {}

        # Build ProviderConfigEntry list + resolve classes.
        provider_entries: list[ProviderConfigEntry] = []
        provider_classes: dict[str, type] = {}

        for key, prov in providers_raw.items():
            class_path = prov.get("use", "")
            params = prov.get("params") or {}
            entry = ProviderConfigEntry(
                key=key,
                type=_infer_provider_type(class_path),
                model=params.get("model", prov.get("model", "")),
                api_key=params.get("api_key"),
                base_url=params.get("base_url"),
                extra=params,
            )
            provider_entries.append(entry)
            if resolve_classes and class_path:
                provider_classes[key] = load_class(class_path)

        # Build AgentConfigEntry list.
        agent_entries: list[AgentConfigEntry] = []
        for name, agent in agents_raw.items():
            entry = AgentConfigEntry(
                name=name,
                role=agent.get("role", name),
                provider_key=agent.get("provider", ""),
                tools=agent.get("skills") or agent.get("tools") or [],
                max_steps=agent.get("max_steps", 10),
            )
            agent_entries.append(entry)

        # Routing.
        strategy = routing_raw.get("strategy", "local_first")
        fallback_chain = routing_raw.get("fallback_chain") or []

        # Budgets.
        budgets = BudgetConfig(
            daily_limit_usd=budgets_raw.get("daily_limit_usd"),
            per_task_limit_usd=budgets_raw.get("per_task_limit_usd"),
            alert_threshold_pct=budgets_raw.get("alert_threshold_pct"),
        )

        # Build base OrchestratorConfiguration.
        base = OrchestratorConfiguration(
            agents=agent_entries,
            providers=provider_entries,
            routing_strategy=strategy,
            budget_limit_usd=budgets.daily_limit_usd,
        )

        return OrchestratorConfig(
            config_version=data.get("config_version", CURRENT_CONFIG_VERSION),
            base_config=base,
            provider_classes=provider_classes,
            budgets=budgets,
            fallback_chain=fallback_chain,
            raw=data,
        )

    def _serialize(self, config: OrchestratorConfig) -> dict[str, Any]:
        """Convert ``OrchestratorConfig`` back to a YAML-compatible dict."""
        base = config.base_config

        providers: dict[str, Any] = {}
        for p in base.providers:
            prov: dict[str, Any] = {"use": _class_path_from_entry(p)}
            params: dict[str, Any] = {}
            if p.base_url:
                params["base_url"] = p.base_url
            if p.api_key:
                params["api_key"] = p.api_key
            if p.model:
                params["model"] = p.model
            if p.extra:
                for k, v in p.extra.items():
                    if k not in params:
                        params[k] = v
            if params:
                prov["params"] = params
            providers[p.key] = prov

        agents: dict[str, Any] = {}
        for a in base.agents:
            agent: dict[str, Any] = {}
            if a.provider_key:
                agent["provider"] = a.provider_key
            if a.tools:
                agent["skills"] = a.tools
            if a.max_steps != 10:
                agent["max_steps"] = a.max_steps
            agents[a.name] = agent

        routing: dict[str, Any] = {}
        if base.routing_strategy != "local_first":
            routing["strategy"] = base.routing_strategy
        if config.fallback_chain:
            routing["fallback_chain"] = config.fallback_chain

        budgets_dict: dict[str, Any] = {}
        if config.budgets.daily_limit_usd is not None:
            budgets_dict["daily_limit_usd"] = config.budgets.daily_limit_usd
        if config.budgets.per_task_limit_usd is not None:
            budgets_dict["per_task_limit_usd"] = config.budgets.per_task_limit_usd
        if config.budgets.alert_threshold_pct is not None:
            budgets_dict["alert_threshold_pct"] = config.budgets.alert_threshold_pct

        result: dict[str, Any] = {"config_version": config.config_version}
        if providers:
            result["providers"] = providers
        if agents:
            result["agents"] = agents
        if routing:
            result["routing"] = routing
        if budgets_dict:
            result["budgets"] = budgets_dict

        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TYPE_MAP = {
    "local": "local",
    "ollama": "ollama",
    "openrouter": "openrouter",
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "google",
    "vllm": "vllm",
}


def _infer_provider_type(class_path: str) -> str:
    """Guess provider type from class path for ProviderConfigEntry.type."""
    lower = class_path.lower()
    for keyword, ptype in _TYPE_MAP.items():
        if keyword in lower:
            return ptype
    return "local"


def _class_path_from_entry(entry: ProviderConfigEntry) -> str:
    """Build a ``module:Class`` path from provider type (best-effort reverse)."""
    type_to_path = {
        "local": "agent_orchestrator.providers.local:LocalProvider",
        "ollama": "agent_orchestrator.providers.local:LocalProvider",
        "openrouter": "agent_orchestrator.providers.openrouter:OpenRouterProvider",
        "openai": "agent_orchestrator.providers.openai:OpenAIProvider",
        "anthropic": "agent_orchestrator.providers.anthropic:AnthropicProvider",
        "google": "agent_orchestrator.providers.google:GoogleProvider",
    }
    return type_to_path.get(entry.type, f"agent_orchestrator.providers.{entry.type}:Provider")
