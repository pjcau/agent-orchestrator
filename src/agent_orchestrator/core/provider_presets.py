"""Provider presets — one-click setup for common provider configurations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderPresetEntry:
    """A single provider within a preset."""
    key: str
    type: str  # "ollama", "openrouter", "openai", "anthropic", "google"
    model: str
    api_key_env: str | None = None  # env var name for API key
    base_url: str | None = None
    is_default: bool = False


@dataclass
class ProviderPreset:
    """A named preset with a set of pre-configured providers."""
    name: str
    description: str
    providers: list[ProviderPresetEntry] = field(default_factory=list)
    routing_strategy: str = "local_first"
    offline_mode: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


# Built-in presets
_BUILTIN_PRESETS: dict[str, ProviderPreset] = {
    "local_only": ProviderPreset(
        name="local_only",
        description="Local models only via Ollama. No internet required.",
        providers=[
            ProviderPresetEntry(
                key="ollama",
                type="ollama",
                model="qwen2.5-coder:7b",
                base_url="http://localhost:11434",
                is_default=True,
            ),
        ],
        routing_strategy="fixed",
        offline_mode=True,
    ),
    "cloud_only": ProviderPreset(
        name="cloud_only",
        description="Cloud models only via OpenRouter.",
        providers=[
            ProviderPresetEntry(
                key="openrouter",
                type="openrouter",
                model="qwen/qwen3-235b-a22b:free",
                api_key_env="OPENROUTER_API_KEY",
                is_default=True,
            ),
        ],
        routing_strategy="cost_optimized",
        offline_mode=False,
    ),
    "hybrid": ProviderPreset(
        name="hybrid",
        description="Local-first with cloud fallback. Best of both worlds.",
        providers=[
            ProviderPresetEntry(
                key="ollama",
                type="ollama",
                model="qwen2.5-coder:7b",
                base_url="http://localhost:11434",
                is_default=True,
            ),
            ProviderPresetEntry(
                key="openrouter",
                type="openrouter",
                model="qwen/qwen3-235b-a22b:free",
                api_key_env="OPENROUTER_API_KEY",
            ),
        ],
        routing_strategy="local_first",
        offline_mode=False,
    ),
    "high_quality": ProviderPreset(
        name="high_quality",
        description="Premium cloud models for maximum quality.",
        providers=[
            ProviderPresetEntry(
                key="anthropic",
                type="anthropic",
                model="claude-sonnet-4-20250514",
                api_key_env="ANTHROPIC_API_KEY",
                is_default=True,
            ),
            ProviderPresetEntry(
                key="openai",
                type="openai",
                model="gpt-4.1",
                api_key_env="OPENAI_API_KEY",
            ),
        ],
        routing_strategy="capability_based",
        offline_mode=False,
    ),
}


class ProviderPresetManager:
    """Manage provider presets — built-in and custom.

    Built-in presets: local_only, cloud_only, hybrid, high_quality.
    Custom presets can be added at runtime.
    """

    def __init__(self) -> None:
        self._presets: dict[str, ProviderPreset] = dict(_BUILTIN_PRESETS)
        self._active_preset: str | None = None

    def list_presets(self) -> list[ProviderPreset]:
        """Return all available presets."""
        return list(self._presets.values())

    def get(self, name: str) -> ProviderPreset | None:
        """Get a preset by name."""
        return self._presets.get(name)

    def get_builtin_names(self) -> list[str]:
        """Return names of built-in presets."""
        return list(_BUILTIN_PRESETS.keys())

    def add_custom(self, preset: ProviderPreset) -> None:
        """Add a custom preset."""
        self._presets[preset.name] = preset

    def remove(self, name: str) -> bool:
        """Remove a custom preset. Cannot remove built-in presets. Returns True if removed."""
        if name in _BUILTIN_PRESETS:
            return False  # cannot remove built-in
        if name in self._presets:
            del self._presets[name]
            if self._active_preset == name:
                self._active_preset = None
            return True
        return False

    def activate(self, name: str) -> ProviderPreset:
        """Set the active preset. Returns the preset. Raises KeyError if not found."""
        preset = self._presets.get(name)
        if preset is None:
            raise KeyError(f"Preset '{name}' not found")
        self._active_preset = name
        return preset

    @property
    def active(self) -> ProviderPreset | None:
        """The currently active preset."""
        if self._active_preset is None:
            return None
        return self._presets.get(self._active_preset)

    @property
    def active_name(self) -> str | None:
        """Name of the currently active preset."""
        return self._active_preset

    def get_provider_configs(self, name: str | None = None) -> list[dict[str, Any]]:
        """Export provider configurations from a preset as dicts.

        If name is None, uses the active preset.
        """
        preset_name = name or self._active_preset
        if preset_name is None:
            return []
        preset = self._presets.get(preset_name)
        if preset is None:
            return []
        return [
            {
                "key": p.key,
                "type": p.type,
                "model": p.model,
                "api_key_env": p.api_key_env,
                "base_url": p.base_url,
                "is_default": p.is_default,
            }
            for p in preset.providers
        ]

    def get_default_provider_key(self, name: str | None = None) -> str | None:
        """Return the default provider key from a preset."""
        preset_name = name or self._active_preset
        if preset_name is None:
            return None
        preset = self._presets.get(preset_name)
        if preset is None:
            return None
        for p in preset.providers:
            if p.is_default:
                return p.key
        if preset.providers:
            return preset.providers[0].key
        return None
