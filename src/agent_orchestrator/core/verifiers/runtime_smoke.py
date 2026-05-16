"""Runtime smoke verifier — actually try to install + import the workspace.

Why this exists
---------------

The static verifiers (`SyntaxVerifier`, `EncodingVerifier`,
`DependencyVerifier`, `ImportVerifier`, `WorkspaceCoherenceVerifier`)
are all approximations of ground truth. Each runs in <2 s and catches
its own class of failure, but every approximation drifts: the 2026-05-16
benchmarks surfaced three different cases where the static chain said
"passed" but the produced repo could not actually run.

This verifier closes the loop by doing the actual thing:

1. Hash ``backend/requirements.txt`` (or the first ``requirements*.txt``
   under the workdir) → derive a cache key.
2. If a venv for that cache key already exists, reuse it. Otherwise
   create a fresh venv and ``pip install -r requirements.txt``.
3. If pip fails, emit a single ``pip_install`` failure with the
   stderr tail (the LLM repair prompt then sees the actual error).
4. For each top-level local module (``backend/main.py``, etc.), run
   ``python -c "import <module>"`` inside the venv. Capture every
   ``ModuleNotFoundError`` as a ``missing_dep`` failure — same format
   as :class:`ImportVerifier`, so the existing ``requirements_append``
   auto-fix matches without any new pattern.

Cost & ordering
---------------

``cost_estimate_s = 30`` is intentionally large so the gate sorts this
verifier LAST. With ``fail_fast=True`` (the default), if any cheap
verifier fires first, the smoke verifier is skipped entirely. It only
runs when the static chain is already clean — at which point the
~15-30 s amortised cost is worth it to confirm the workspace truly works.

The venv cache lives under ``tempfile.gettempdir() / "ao-smoke-venvs"``
and is keyed by SHA-256 of the requirements file. Subsequent runs with
identical deps reuse the venv, dropping the cost to <2 s for the import
loop alone.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from agent_orchestrator.core.verification_gate import VerifierFailure

_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", "dist", "build", ".tox"}
_PIP_INSTALL_TIMEOUT_S = 240
_IMPORT_TIMEOUT_S = 15


class RuntimeSmokeVerifier:
    name = "runtime_smoke"
    # Intentionally high so the gate runs cheaper verifiers first; with
    # ``fail_fast=True`` this verifier only fires when the static chain
    # is already clean.
    cost_estimate_s = 30.0

    def __init__(
        self,
        *,
        cache_root: Path | None = None,
        max_import_targets: int = 12,
    ) -> None:
        self._cache_root = cache_root or Path(tempfile.gettempdir()) / "ao-smoke-venvs"
        self._max_imports = max_import_targets

    async def verify(self, workdir: Path) -> list[VerifierFailure]:
        req = _find_requirements(workdir)
        if req is None:
            return []  # nothing Python-shaped to install
        try:
            venv_dir, install_failure = self._ensure_venv(workdir, req)
        except Exception as exc:  # noqa: BLE001 — never let infra break the gate
            return [
                VerifierFailure(
                    verifier=self.name,
                    severity="warning",
                    category="smoke_infrastructure",
                    message=f"smoke verifier could not provision a venv: {exc}",
                    detail=str(exc)[:1024],
                    file=None,
                    exit_code=None,
                )
            ]
        failures: list[VerifierFailure] = []
        if install_failure is not None:
            failures.append(install_failure)
            return failures  # no point trying imports if install failed
        failures.extend(self._probe_imports(workdir, venv_dir))
        return failures

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ensure_venv(
        self, workdir: Path, req: Path
    ) -> tuple[Path, VerifierFailure | None]:
        """Create or reuse a venv keyed by SHA-256(requirements file). Returns
        the venv dir + either None (install succeeded / venv reused) or a
        single ``pip_install`` failure."""
        digest = hashlib.sha256(req.read_bytes()).hexdigest()[:12]
        venv_dir = self._cache_root / digest
        self._cache_root.mkdir(parents=True, exist_ok=True)

        # Reuse on cache hit — pip install was already verified for this hash.
        marker = venv_dir / ".smoke-ok"
        if marker.exists():
            return venv_dir, None

        # Fresh venv.
        if venv_dir.exists():
            # Stale half-built venv → wipe (best effort).
            import shutil
            shutil.rmtree(venv_dir, ignore_errors=True)
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            capture_output=True, timeout=60, check=True,
        )
        pip = venv_dir / "bin" / "pip"
        if not pip.exists():
            # Windows path
            pip = venv_dir / "Scripts" / "pip.exe"

        # Try install. Capture stderr; surface as a single failure on non-zero.
        cp = subprocess.run(
            [str(pip), "install", "-q", "--no-input", "-r", str(req)],
            capture_output=True, timeout=_PIP_INSTALL_TIMEOUT_S,
        )
        if cp.returncode != 0:
            stderr = cp.stderr.decode(errors="replace")
            tail = stderr.strip().splitlines()[-12:] if stderr else []
            return venv_dir, VerifierFailure(
                verifier=self.name,
                severity="error",
                category="pip_install",
                message=f"pip install -r {req.relative_to(workdir)} failed (exit {cp.returncode})",
                detail="\n".join(tail)[:2048],
                file=str(req.relative_to(workdir)),
                exit_code=cp.returncode,
            )
        marker.write_text(digest)
        return venv_dir, None

    def _probe_imports(self, workdir: Path, venv_dir: Path) -> list[VerifierFailure]:
        """Find top-level local modules and try `python -c 'import X'` for each.

        A "top-level local module" is the stem of any ``*.py`` directly under
        ``workdir`` OR under a single-level subdir that contains a sibling
        ``requirements*.txt`` (e.g. ``backend/``). We cap at ``max_import_targets``
        to keep the runtime bounded.
        """
        py_exe = venv_dir / "bin" / "python"
        if not py_exe.exists():
            py_exe = venv_dir / "Scripts" / "python.exe"
        # Build PYTHONPATH = the requirements-bearing dir(s).
        path_entries: set[Path] = set()
        for req in workdir.rglob("requirements*.txt"):
            if any(p in _SKIP_DIRS for p in req.parts):
                continue
            path_entries.add(req.parent)
        targets: list[tuple[str, Path]] = []
        seen_names: set[str] = set()
        for root in sorted(path_entries):
            for py in sorted(root.glob("*.py")):
                if py.name.startswith("_") or py.name == "setup.py":
                    continue
                if py.stem in seen_names:
                    continue
                seen_names.add(py.stem)
                targets.append((py.stem, root))
                if len(targets) >= self._max_imports:
                    break
            if len(targets) >= self._max_imports:
                break
        failures: list[VerifierFailure] = []
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        pythonpath_value = os.pathsep.join(str(p) for p in path_entries)
        seen_missing: set[str] = set()
        for module, root in targets:
            env["PYTHONPATH"] = str(root) + os.pathsep + pythonpath_value
            cp = subprocess.run(
                [str(py_exe), "-c", f"import {module}"],
                capture_output=True, timeout=_IMPORT_TIMEOUT_S, env=env,
            )
            if cp.returncode == 0:
                continue
            stderr = cp.stderr.decode(errors="replace")
            # Extract `No module named 'X'` for de-dup + matching the existing
            # ImportVerifier auto-fix pattern.
            import re as _re
            m = _re.search(r"No module named '([^']+)'", stderr)
            if m:
                missing = m.group(1).split(".", 1)[0]
                if missing in seen_missing:
                    continue
                seen_missing.add(missing)
                rel_root = str(root.relative_to(workdir)) if root != workdir else "."
                failures.append(
                    VerifierFailure(
                        verifier=self.name,
                        severity="error",
                        category="missing_dep",  # match ImportVerifier so the same auto-fix runs
                        message=f"No module named '{missing}' (smoke import of '{module}' in {rel_root})",
                        detail=(
                            f"{rel_root}/{module}.py: `python -c \"import {module}\"` failed "
                            f"with ModuleNotFoundError for '{missing}'. "
                            f"Expected package on PyPI: '{missing}'.\n\n"
                            f"stderr tail:\n{stderr.strip()[-800:]}"
                        ),
                        file=f"{rel_root}/{module}.py" if rel_root != "." else f"{module}.py",
                        exit_code=cp.returncode,
                    )
                )
            else:
                # Generic import-time failure (syntax error at import, NameError, etc.).
                failures.append(
                    VerifierFailure(
                        verifier=self.name,
                        severity="error",
                        category="smoke_import_error",
                        message=f"`import {module}` failed at runtime",
                        detail=stderr.strip()[-1500:],
                        file=f"{module}.py",
                        exit_code=cp.returncode,
                    )
                )
        return failures


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------


def _find_requirements(workdir: Path) -> Path | None:
    """Return the first non-cache requirements*.txt under the workdir."""
    candidates: list[Path] = []
    for p in workdir.rglob("requirements*.txt"):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        candidates.append(p)
    if not candidates:
        return None
    # Prefer the shallowest one (most likely the canonical "production" deps).
    candidates.sort(key=lambda p: (len(p.parts), str(p)))
    return candidates[0]
