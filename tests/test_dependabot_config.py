"""Structural tests for `.github/dependabot.yml`.

Per-package Dependabot PRs that edit the same manifest (pyproject.toml,
package.json, a shared workflow file) conflict with each other and can only
land one-per-rebase-cycle. Grouping every update of an ecosystem into a single
PR removes that cascade. These tests guard the grouping so a future edit can't
silently revert to per-package PRs, and keep the commit-message prefixes in
sync with the auto-merge title filter in
`.github/workflows/auto-merge-maintenance.yml`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

CONFIG = Path(__file__).resolve().parent.parent / ".github" / "dependabot.yml"

# Ecosystems whose per-package PRs share a manifest and therefore must be
# grouped into a single catch-all PR covering every update type.
CASCADING_ECOSYSTEMS = {"pip", "github-actions", "npm"}

# Prefixes the auto-merge workflow treats as eligible (`^(deps|ci|docs):`).
AUTO_MERGE_PREFIXES = ("deps", "ci", "docs")


@pytest.fixture(scope="module")
def updates() -> list[dict]:
    assert CONFIG.exists(), f"dependabot config not found: {CONFIG}"
    data = yaml.safe_load(CONFIG.read_text())
    assert data.get("version") == 2, "Dependabot config must be version 2"
    return data["updates"]


def _catch_all_group(entry: dict) -> dict | None:
    """Return the first group whose patterns are a catch-all (`*`), or None."""
    for group in (entry.get("groups") or {}).values():
        if "*" in (group.get("patterns") or []):
            return group
    return None


@pytest.mark.parametrize("ecosystem", sorted(CASCADING_ECOSYSTEMS))
def test_cascading_ecosystem_is_grouped(updates: list[dict], ecosystem: str) -> None:
    entries = [u for u in updates if u["package-ecosystem"] == ecosystem]
    assert entries, f"No dependabot entry for ecosystem {ecosystem!r}"
    for entry in entries:
        group = _catch_all_group(entry)
        assert group is not None, (
            f"{ecosystem} must have a catch-all (patterns: ['*']) group to "
            f"collapse per-package PRs into one and avoid manifest conflicts"
        )
        update_types = set(group.get("update-types") or [])
        # Major bumps are exactly the ones that previously escaped the group
        # and conflicted as standalone PRs — they must be included.
        assert {"minor", "patch", "major"}.issubset(update_types), (
            f"{ecosystem} group must cover minor/patch/major, got {update_types}"
        )


def test_prefixes_match_auto_merge_filter(updates: list[dict]) -> None:
    for entry in updates:
        prefix = entry.get("commit-message", {}).get("prefix")
        assert prefix is not None, (
            f"{entry['package-ecosystem']} must set commit-message.prefix"
        )
        eco = entry["package-ecosystem"]
        if eco in CASCADING_ECOSYSTEMS:
            assert prefix.startswith(AUTO_MERGE_PREFIXES), (
                f"{eco} prefix {prefix!r} must start with one of "
                f"{AUTO_MERGE_PREFIXES} so the weekly auto-merge job picks it up"
            )
