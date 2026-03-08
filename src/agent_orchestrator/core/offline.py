"""Offline mode — restrict to local providers when internet is unavailable."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class OfflineConfig:
    enabled: bool = False  # when True, only local providers are allowed
    allow_cached: bool = True  # allow cached cloud results
    local_provider_keys: list[str] = field(default_factory=lambda: ["local", "ollama"])


class OfflineManager:
    """Manage offline mode — restrict to local providers when internet is unavailable."""

    def __init__(self, config: OfflineConfig | None = None) -> None:
        self._config = config or OfflineConfig()
        self._is_offline: bool = self._config.enabled

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------

    def enable(self) -> None:
        """Switch to offline mode."""
        self._is_offline = True

    def disable(self) -> None:
        """Switch back to online mode."""
        self._is_offline = False

    @property
    def is_offline(self) -> bool:
        """True when offline mode is active."""
        return self._is_offline

    # ------------------------------------------------------------------
    # Provider filtering
    # ------------------------------------------------------------------

    def filter_providers(self, providers: dict[str, Any]) -> dict[str, Any]:
        """Return only local providers if offline, all providers if online."""
        if not self._is_offline:
            return dict(providers)
        return {
            key: provider for key, provider in providers.items() if self.is_provider_allowed(key)
        }

    def is_provider_allowed(self, provider_key: str) -> bool:
        """Check if a specific provider is allowed in the current mode."""
        if not self._is_offline:
            return True
        return provider_key in self._config.local_provider_keys

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Return current offline status and configuration."""
        return {
            "is_offline": self._is_offline,
            "allow_cached": self._config.allow_cached,
            "local_provider_keys": list(self._config.local_provider_keys),
        }
