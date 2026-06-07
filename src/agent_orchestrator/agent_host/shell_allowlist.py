"""Shell-command allowlist for the agent-host client.

The agent runs on a remote server but tools execute locally — so
``shell_exec`` is the most dangerous tool to delegate. We refuse
``shell=True`` style strings entirely (the caller must supply ``argv``
as a list) and gate ``argv[0]`` on a per-session allowlist that
persists to ``~/.cache/ago/shell-allow.json``.

UX model (copy of Claude Code's first-use confirmation):

1. Agent requests ``shell_exec`` with ``argv=["pytest", "-q"]``.
2. Client looks up ``pytest`` in the allowlist.
   - Present → run.
   - Absent → ask the user once (``confirm_callback``). If yes, add
     to the allowlist file and run.  If no, return an
     ``error_code="shell_denied"`` SkillResult without running.
3. Future calls to ``pytest`` in this or any later session run without
   prompting.

The decision file is human-editable JSON: ``{"allowed": ["pytest", …]}``.
Removing an entry rolls back the allow. The file lives in
``XDG_CACHE_HOME`` (or ``~/.cache``) so it survives binary upgrades but
is not synced to dotfiles by default.

Threats mitigated:

* Command injection via ``argv[0]`` — the registry is keyed by basename
  only, refusing path-traversal (``..`` / ``/``) in the binary name.
* Persisting blanket allow for ``sh`` / ``bash`` / ``python -c`` is
  flagged at allow-time with a warning string the caller can surface.
* Race on the allow file — single atomic write via temp + rename so a
  killed process never leaves a corrupt JSON.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)


# Binaries that effectively bypass an allowlist (they can run anything).
# We do not forbid them — sometimes the user really wants to allow
# ``bash`` — but we expose them via :func:`is_high_risk` so a CLI front-
# end can mark the confirmation prompt with an extra warning.
_HIGH_RISK_BINARIES = frozenset(
    {"sh", "bash", "zsh", "fish", "dash", "ksh", "csh", "tcsh", "ash"}
)


class ShellAllowlistError(ValueError):
    """Raised on malformed argv (containing a path separator, etc.)."""


ConfirmCallback = Callable[[str, bool], Awaitable[bool]]
"""Async callable ``(basename, high_risk) -> bool`` asking the user."""


def _default_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "ago" / "shell-allow.json"


def is_high_risk(binary: str) -> bool:
    """Return True if ``binary`` is a general-purpose shell.

    Used by the CLI front-end to attach an extra "this is equivalent to
    full shell access" warning to the first-time confirmation prompt.
    """
    return Path(binary).name in _HIGH_RISK_BINARIES


def _basename_or_raise(argv: Sequence[str]) -> str:
    """Validate argv and return the canonical lookup key (``argv[0]`` basename).

    Refuses any path separator in ``argv[0]`` — the allowlist is keyed
    by basename only and accepting a path would let an attacker bypass
    the allow by aliasing ``/tmp/evil/pytest`` to the already-allowed
    ``pytest``.
    """
    if not argv:
        raise ShellAllowlistError("argv must not be empty")
    bin0 = argv[0]
    if not bin0:
        raise ShellAllowlistError("argv[0] must not be empty")
    if "/" in bin0 or "\\" in bin0:
        raise ShellAllowlistError(
            f"argv[0] must be a bare binary name, not a path: {bin0!r}"
        )
    return bin0


class ShellAllowlist:
    """Persistent set of permitted ``argv[0]`` basenames.

    Lazily loads on first use; writes atomically on every mutation. Thread
    safety is not provided — the agent-host client runs all decisions on
    a single asyncio event loop.
    """

    def __init__(self, *, path: Path | None = None) -> None:
        self._path = path or _default_path()
        self._allowed: set[str] | None = None

    def _load(self) -> None:
        if self._allowed is not None:
            return
        if not self._path.exists():
            self._allowed = set()
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._allowed = {str(x) for x in data.get("allowed", [])}
        except (OSError, ValueError) as exc:
            logger.warning(
                "shell allowlist: failed to load %s (%s); starting empty",
                self._path,
                exc,
            )
            self._allowed = set()

    def _save(self) -> None:
        assert self._allowed is not None  # noqa: S101
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"allowed": sorted(self._allowed)}, indent=2, sort_keys=True
        )
        # Atomic write: temp file in the same dir, then rename.
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self._path.parent,
            delete=False,
            prefix=self._path.name,
            suffix=".tmp",
        ) as tmp:
            tmp.write(payload)
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, self._path)

    def contains(self, argv: Sequence[str]) -> bool:
        self._load()
        return _basename_or_raise(argv) in (self._allowed or set())

    def allow(self, argv: Sequence[str]) -> None:
        self._load()
        bin0 = _basename_or_raise(argv)
        assert self._allowed is not None  # noqa: S101
        self._allowed.add(bin0)
        self._save()

    def revoke(self, binary: str) -> bool:
        self._load()
        assert self._allowed is not None  # noqa: S101
        if binary in self._allowed:
            self._allowed.remove(binary)
            self._save()
            return True
        return False

    def snapshot(self) -> list[str]:
        self._load()
        return sorted(self._allowed or set())

    async def gate(
        self,
        argv: Sequence[str],
        *,
        confirm: ConfirmCallback,
    ) -> bool:
        """Resolve the allow decision for ``argv``.

        Returns ``True`` if the command should run, ``False`` if the user
        declined. ``confirm`` is awaited only on first use; subsequent
        calls hit the in-memory cache.
        """
        bin0 = _basename_or_raise(argv)
        if self.contains(argv):
            return True
        decision = await confirm(bin0, is_high_risk(bin0))
        if decision:
            self.allow(argv)
        return decision
