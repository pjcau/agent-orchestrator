"""Import boundary tests: harness layer must never import from app layer.

The library is split into two layers:

  HARNESS (publishable library):
    - core/         Core abstractions (Provider, Agent, Skill, Graph, etc.)
    - providers/    LLM provider implementations
    - skills/       Reusable skill modules
    - client.py     High-level client API (if present)

  APP (application-only, not part of library):
    - dashboard/    FastAPI dashboard, WebSocket, auth, UI
    - integrations/ External service integrations

Rule: Harness layer files MUST NEVER import from app layer modules.
This ensures the core library can be installed and used without dashboard
or integration dependencies.
"""

import ast
import os
from pathlib import Path

import pytest

# Root of the agent_orchestrator package
_PKG_ROOT = Path(__file__).resolve().parent.parent / "src" / "agent_orchestrator"

# Harness layer: directories and standalone files that form the publishable library
HARNESS_DIRS = ["core", "providers", "skills"]
HARNESS_FILES = ["client.py"]

# App layer: module prefixes that harness code must never import
APP_MODULES = ["dashboard", "integrations"]


def _collect_harness_python_files() -> list[Path]:
    """Return all .py files belonging to the harness layer."""
    files: list[Path] = []

    for dirname in HARNESS_DIRS:
        dirpath = _PKG_ROOT / dirname
        if not dirpath.is_dir():
            continue
        for root, _dirs, filenames in os.walk(dirpath):
            for fname in filenames:
                if fname.endswith(".py"):
                    files.append(Path(root) / fname)

    for fname in HARNESS_FILES:
        fpath = _PKG_ROOT / fname
        if fpath.is_file():
            files.append(fpath)

    return sorted(files)


def _extract_imports(filepath: Path) -> list[tuple[int, str]]:
    """Parse a Python file and return (line_number, module_string) for every import."""
    source = filepath.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []

    imports: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((node.lineno, alias.name))

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.append((node.lineno, module))

    return imports


def _is_app_import(module_str: str) -> str | None:
    """If *module_str* refers to an app-layer module, return the matched app module name."""
    for app_mod in APP_MODULES:
        # Absolute imports
        if module_str == f"agent_orchestrator.{app_mod}":
            return app_mod
        if module_str.startswith(f"agent_orchestrator.{app_mod}."):
            return app_mod

        # Relative-style (after ast resolution, module is just the dotted name)
        if module_str == app_mod:
            return app_mod
        if module_str.startswith(f"{app_mod}."):
            return app_mod

        # Relative imports that start with a dot are stored without the dot prefix
        # but with the resolved module name. We also handle bare matches.
        if module_str.startswith(f".{app_mod}"):
            return app_mod

    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestImportBoundary:
    """Ensure harness layer never imports from app layer."""

    def test_harness_files_exist(self):
        """Sanity check: we actually found harness files to scan."""
        files = _collect_harness_python_files()
        assert len(files) > 0, "No harness Python files found — check _PKG_ROOT"

    def test_harness_does_not_import_app(self):
        """Walk every harness file and assert no app-layer imports."""
        violations: list[str] = []

        for filepath in _collect_harness_python_files():
            rel = filepath.relative_to(_PKG_ROOT)
            for lineno, module_str in _extract_imports(filepath):
                matched = _is_app_import(module_str)
                if matched:
                    violations.append(
                        f"  {rel}:{lineno} imports '{module_str}' (app module: {matched})"
                    )

        if violations:
            msg = (
                f"Found {len(violations)} import boundary violation(s):\n"
                + "\n".join(violations)
                + "\n\nHarness layer (core/, providers/, skills/, client.py) "
                "must never import from app layer (dashboard/, integrations/)."
            )
            pytest.fail(msg)

    def test_harness_importable_without_dashboard(self):
        """Core harness modules can be imported without dashboard extras."""
        # These are pure-Python modules with no heavy external deps.
        from agent_orchestrator.core import provider  # noqa: F401
        from agent_orchestrator.core import agent  # noqa: F401
        from agent_orchestrator.core import skill  # noqa: F401
        from agent_orchestrator.core import orchestrator  # noqa: F401
        from agent_orchestrator.core import cooperation  # noqa: F401
        from agent_orchestrator.core import graph  # noqa: F401

    def test_violation_detection_catches_bad_import(self):
        """Verify our detection logic actually flags a deliberate bad import."""
        # Simulate an import statement that references the dashboard
        bad_imports = [
            (1, "agent_orchestrator.dashboard"),
            (2, "agent_orchestrator.dashboard.app"),
            (3, "dashboard"),
            (4, "dashboard.events"),
            (5, "agent_orchestrator.integrations"),
            (6, "agent_orchestrator.integrations.slack"),
            (7, "integrations"),
            (8, "integrations.webhook"),
        ]

        for lineno, module_str in bad_imports:
            result = _is_app_import(module_str)
            assert result is not None, (
                f"Expected '{module_str}' to be flagged as app-layer import, "
                f"but _is_app_import returned None"
            )

    def test_safe_imports_not_flagged(self):
        """Verify legitimate imports are NOT flagged as violations."""
        safe_imports = [
            "agent_orchestrator.core.provider",
            "agent_orchestrator.providers.openai",
            "agent_orchestrator.skills.filesystem",
            "os",
            "pathlib",
            "json",
            "ast",
            "agent_orchestrator.core.graph",
        ]

        for module_str in safe_imports:
            result = _is_app_import(module_str)
            assert result is None, (
                f"'{module_str}' should NOT be flagged as app-layer, but got: {result}"
            )
