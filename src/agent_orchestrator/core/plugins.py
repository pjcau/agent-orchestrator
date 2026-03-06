"""Plugin system — drop-in skills/providers without modifying core code."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PluginManifest:
    name: str
    version: str
    plugin_type: str  # "skill", "provider", "graph_template"
    description: str = ""
    author: str = ""
    entry_point: str = ""  # module path, e.g. "my_plugin.skill"
    config: dict[str, Any] = field(default_factory=dict)


class PluginLoader:
    """Discover and load plugins from a directory or manifest.

    Actual dynamic loading (importlib) is deferred — this class registers
    manifests and stores references to already-instantiated objects.
    The ``entry_point`` field documents where the code lives but we do not
    auto-import it here.
    """

    def __init__(self) -> None:
        self._manifests: dict[str, PluginManifest] = {}
        self._loaded_skills: dict[str, Any] = {}  # name -> skill instance
        self._loaded_providers: dict[str, Any] = {}  # name -> provider instance

    # ------------------------------------------------------------------
    # Manifest management
    # ------------------------------------------------------------------

    def register(self, manifest: PluginManifest) -> None:
        """Register a plugin manifest (without loading it)."""
        self._manifests[manifest.name] = manifest

    def load_from_dict(self, data: dict) -> PluginManifest:
        """Create and register a manifest from a plain dict."""
        manifest = PluginManifest(
            name=data["name"],
            version=data["version"],
            plugin_type=data["plugin_type"],
            description=data.get("description", ""),
            author=data.get("author", ""),
            entry_point=data.get("entry_point", ""),
            config=data.get("config", {}),
        )
        self.register(manifest)
        return manifest

    def get_manifest(self, name: str) -> PluginManifest | None:
        """Get a registered manifest by name."""
        return self._manifests.get(name)

    def list_plugins(self, plugin_type: str | None = None) -> list[PluginManifest]:
        """List all registered plugins, optionally filtered by type."""
        manifests = list(self._manifests.values())
        if plugin_type is not None:
            manifests = [m for m in manifests if m.plugin_type == plugin_type]
        return manifests

    def unregister(self, name: str) -> bool:
        """Remove a plugin registration. Returns True if it existed."""
        if name in self._manifests:
            del self._manifests[name]
            # Also remove any loaded instance so callers see a clean state.
            self._loaded_skills.pop(name, None)
            self._loaded_providers.pop(name, None)
            return True
        return False

    # ------------------------------------------------------------------
    # Loaded instance management
    # ------------------------------------------------------------------

    def register_skill_instance(self, name: str, instance: Any) -> None:
        """Store an already-instantiated skill under the given name."""
        self._loaded_skills[name] = instance

    def register_provider_instance(self, name: str, instance: Any) -> None:
        """Store an already-instantiated provider under the given name."""
        self._loaded_providers[name] = instance

    def get_loaded_skills(self) -> dict[str, Any]:
        """Return all loaded skill instances."""
        return dict(self._loaded_skills)

    def get_loaded_providers(self) -> dict[str, Any]:
        """Return all loaded provider instances."""
        return dict(self._loaded_providers)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> list[dict]:
        """Export all manifests as plain dicts."""
        return [
            {
                "name": m.name,
                "version": m.version,
                "plugin_type": m.plugin_type,
                "description": m.description,
                "author": m.author,
                "entry_point": m.entry_point,
                "config": m.config,
            }
            for m in self._manifests.values()
        ]
