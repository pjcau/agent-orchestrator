"""Workspace coherence verifier — catches cross-file contradictions.

Some failures only show up when two files are read together. The
SyntaxVerifier / EncodingVerifier / DependencyVerifier / ImportVerifier each
look at one file at a time. This verifier looks at pairs of files that are
known to **describe the same fact** and flags when they disagree.

v1 scope — focused on the 2026-05-16(b) failure mode
----------------------------------------------------

Iter 3 of the task-tracker test asked the agents to add a Postgres ``db``
service to ``docker-compose.yml`` while keeping ``backend/database.py``
defaulting to SQLite. The agent rewrote ``docker-compose.yml`` to point
``DATABASE_URL`` at Postgres but left ``backend/database.py`` defaulting
to SQLite. The two files now describe the same fact (which DB to use) in
incompatible ways — silently. No prior verifier catches it.

The v1 rule shipped here:

- Read ``docker-compose.yml`` (if any). For each service, extract
  ``environment.DATABASE_URL`` (both the dict form and the list form
  ``KEY=value``).
- Read every ``*.py`` under ``workdir``. Look for a literal assignment
  of ``DATABASE_URL`` or a ``getenv("DATABASE_URL", "<default>")`` call.
- If both forms set ``DATABASE_URL`` to a value whose *scheme* differs
  (``postgresql`` vs ``sqlite`` vs ``mysql``), flag a coherence failure.

The verifier is intentionally narrow. Adding new rules is a single new
private function + a single new failure category. Cost: a few file reads
+ regex scans — ~0.1 s for a typical workspace.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from agent_orchestrator.core.verification_gate import VerifierFailure

_DB_SCHEME = re.compile(r"^\s*([a-zA-Z][a-zA-Z0-9+\-.]*)://")
_DB_URL_DEFAULT_RE = re.compile(
    r"""
    (?:
      DATABASE_URL\s*=\s*['"](?P<v1>[^'"]+)['"]                 # DATABASE_URL = "..."
      |
      (?:os\.)?getenv\(\s*['"]DATABASE_URL['"]\s*,\s*           # getenv("DATABASE_URL",
        ['"](?P<v2>[^'"]+)['"]                                  # "<default>"
      |
      environ\.get\(\s*['"]DATABASE_URL['"]\s*,\s*['"](?P<v3>[^'"]+)['"]
    )
    """,
    re.VERBOSE,
)
_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", "dist", "build"}


class WorkspaceCoherenceVerifier:
    name = "coherence"
    cost_estimate_s = 0.3

    async def verify(self, workdir: Path) -> list[VerifierFailure]:
        failures: list[VerifierFailure] = []
        failures.extend(self._check_database_url(workdir))
        return failures

    # ------------------------------------------------------------------
    # Rule: docker-compose DATABASE_URL vs source DATABASE_URL default
    # ------------------------------------------------------------------

    def _check_database_url(self, workdir: Path) -> list[VerifierFailure]:
        compose_path = _find_compose(workdir)
        if compose_path is None:
            return []
        compose_url = _compose_database_url(compose_path)
        if not compose_url:
            return []
        compose_scheme = _scheme(compose_url)
        if compose_scheme is None:
            return []
        out: list[VerifierFailure] = []
        rel_compose = str(compose_path.relative_to(workdir))
        for py, lineno, default in _iter_py_database_url_defaults(workdir):
            src_scheme = _scheme(default)
            if src_scheme is None:
                continue
            if _equivalent(compose_scheme, src_scheme):
                continue
            rel_py = str(py.relative_to(workdir))
            out.append(
                VerifierFailure(
                    verifier=self.name,
                    severity="error",
                    category="db_url_mismatch",
                    message=(
                        f"DATABASE_URL scheme mismatch: {rel_compose} uses "
                        f"'{compose_scheme}', {rel_py}:{lineno} defaults to "
                        f"'{src_scheme}'"
                    ),
                    detail=(
                        f"{rel_compose} → DATABASE_URL='{compose_url}'\n"
                        f"{rel_py}:{lineno} → default='{default}'\n"
                        "These two paths describe the same fact (which database to "
                        "use). If the env var is unset, the app will silently fall "
                        "back to the source default, contradicting docker-compose."
                    ),
                    file=rel_py,
                    exit_code=1,
                )
            )
        return out


# ---------------------------------------------------------------------------
# Helpers (pure functions, easy to unit-test)
# ---------------------------------------------------------------------------


def _find_compose(workdir: Path) -> Path | None:
    for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        p = workdir / name
        if p.exists():
            return p
    return None


def _compose_database_url(compose_path: Path) -> str | None:
    try:
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — broken yaml → SyntaxVerifier's problem, not ours
        return None
    if not isinstance(data, dict):
        return None
    services = data.get("services")
    if not isinstance(services, dict):
        return None
    for svc in services.values():
        if not isinstance(svc, dict):
            continue
        env = svc.get("environment")
        if isinstance(env, dict):
            v = env.get("DATABASE_URL")
            if isinstance(v, str):
                return v
        elif isinstance(env, list):
            for item in env:
                if isinstance(item, str) and item.startswith("DATABASE_URL="):
                    return item.split("=", 1)[1]
    return None


def _iter_py_database_url_defaults(workdir: Path):
    for py in workdir.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in py.parts):
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for m in _DB_URL_DEFAULT_RE.finditer(text):
            value = m.group("v1") or m.group("v2") or m.group("v3")
            if value is None:
                continue
            lineno = text.count("\n", 0, m.start()) + 1
            yield py, lineno, value


def _scheme(url: str) -> str | None:
    m = _DB_SCHEME.match(url)
    return m.group(1).lower() if m else None


def _equivalent(a: str, b: str) -> bool:
    """Treat `postgresql` ≡ `postgresql+psycopg2` ≡ `postgresql+asyncpg`, etc."""
    return a.split("+", 1)[0] == b.split("+", 1)[0]
