"""Entrypoint verifier — actually run what `docker compose up` would run.

Why this exists
---------------

`RuntimeSmokeVerifier` proves that every top-level module *imports* in a
fresh venv. That's a necessary but not sufficient condition: the
production app is launched by a specific entrypoint (uvicorn, gunicorn,
``python manage.py runserver``, ``python -m app``, …) which can fail for
reasons that bare ``import`` never sees:

- relative-import bugs (``from .database`` works under ``python -m
  package`` but breaks under ``uvicorn main:app`` from inside the dir);
- startup-time DB connections that fail because the URL is wrong;
- ``app.on_event("startup")`` handlers that raise;
- missing module-level state that only the entrypoint touches.

The 2026-05-16(g) three.js-space-app benchmark hit exactly this class:
``main.py`` declared ``from .database import ...``, ``ImportVerifier``
and the bare-import probe both passed, but ``uvicorn main:app`` from
``/app`` crashed with ``ImportError: attempted relative import with no
known parent package``. Runtime scored 0 even though every static check
was green.

This verifier closes that gap by running the actual production
entrypoint with a hard timeout and a health probe.

How it works
------------

1. **Detect the entrypoint**. Look for:
   - ``docker-compose.yml::services.*.command`` (preferred — that's the
     shape the operator chose);
   - ``backend/Dockerfile`` ``CMD`` line.
   Pick the first matching uvicorn-shaped command (``uvicorn <mod>:<app> ...``).
   Other entrypoints are out of v1 scope (would need per-framework logic).

2. **Re-use the venv** the `RuntimeSmokeVerifier` already provisioned
   (same hash-keyed cache). Avoids the cold pip install cost.

3. **Launch on a free ephemeral port** with a hard timeout (default
   12s). Probe ``/`` and ``/docs`` until one returns < 500 or the
   timeout hits. Kill the process unconditionally.

4. **On failure**, surface a single failure with category
   ``entrypoint_crash`` and the captured stderr tail. The LLM repair
   prompt sees the real error.

Cost
----

cost_estimate_s = 20. Runs AFTER the static chain + smoke import probe
(thanks to gate's cheap-first ordering). With `fail_fast=True`, it
only fires when the cheap chain is clean — at which point the ~15 s
spend is the cost of converting a static "passed" into a real
"this thing actually boots".
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import yaml

from agent_orchestrator.core.verification_gate import VerifierFailure
from agent_orchestrator.core.verifiers.runtime_smoke import (
    _canonical_requirements_set,
    _find_requirements,
    _hash_set,
)

_HEALTH_PATHS = ("/health", "/", "/docs", "/api/health")
_LAUNCH_TIMEOUT_S = 12
_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules"}


class EntrypointVerifier:
    name = "entrypoint"
    cost_estimate_s = 20.0

    def __init__(
        self,
        *,
        cache_root: Path | None = None,
        timeout_s: int = _LAUNCH_TIMEOUT_S,
    ) -> None:
        import tempfile

        self._cache_root = cache_root or Path(tempfile.gettempdir()) / "ao-smoke-venvs"
        self._timeout_s = timeout_s

    async def verify(self, workdir: Path) -> list[VerifierFailure]:
        entry = _detect_entrypoint(workdir)
        if entry is None:
            return []  # nothing uvicorn-shaped to launch → silent
        cmd, cwd = entry
        req = _find_requirements(workdir)
        if req is None:
            return []  # no Python project
        venv_dir = self._cache_root / _hash_set(_canonical_requirements_set(req))
        if not (venv_dir / ".smoke-ok").exists():
            # The smoke verifier has not run yet (or failed). Don't
            # second-guess it — if there's no venv, we have nothing to
            # launch against.
            return []
        py = venv_dir / "bin" / "python"
        if not py.exists():
            py = venv_dir / "Scripts" / "python.exe"
        if not py.exists():
            return []

        port = _free_port()
        # Rebuild the command list with the chosen port + 127.0.0.1.
        full_cmd = _materialise_uvicorn_cmd(cmd, py, port)
        env = os.environ.copy()
        # Make sure the entrypoint's own dir is on PYTHONPATH so relative
        # imports of sibling files can resolve as TOP-LEVEL imports under
        # uvicorn's standard launch.
        env["PYTHONPATH"] = str(cwd) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        proc = subprocess.Popen(
            full_cmd,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            failure = _probe(proc, port, self._timeout_s, workdir, cwd)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        return [failure] if failure is not None else []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_entrypoint(workdir: Path) -> tuple[str, Path] | None:
    """Return (cmd_string, cwd_path) for the first uvicorn-shaped entrypoint
    found. Tries docker-compose.yml first, then any backend/Dockerfile CMD."""
    compose = _find_compose(workdir)
    if compose is not None:
        cmd_cwd = _from_compose(compose, workdir)
        if cmd_cwd:
            return cmd_cwd
    # Fall back to Dockerfile CMD scanning. Look in workdir/ and one-level
    # subdirs for `Dockerfile`.
    for df in [workdir / "Dockerfile", *workdir.glob("*/Dockerfile")]:
        if not df.exists():
            continue
        cmd_cwd = _from_dockerfile(df, workdir)
        if cmd_cwd:
            return cmd_cwd
    return None


def _find_compose(workdir: Path) -> Path | None:
    for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        p = workdir / name
        if p.exists():
            return p
    return None


def _from_compose(compose: Path, workdir: Path) -> tuple[str, Path] | None:
    try:
        data = yaml.safe_load(compose.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — broken YAML is SyntaxVerifier's job
        return None
    if not isinstance(data, dict):
        return None
    services = data.get("services") or {}
    if not isinstance(services, dict):
        return None
    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        cmd = svc.get("command")
        if not cmd:
            continue
        cmd_str = _stringify_compose_command(cmd)
        if "uvicorn" not in cmd_str.lower():
            continue
        build = svc.get("build")
        ctx_rel = "."
        if isinstance(build, dict) and isinstance(build.get("context"), str):
            ctx_rel = build["context"]
        elif isinstance(build, str):
            ctx_rel = build
        cwd = (workdir / ctx_rel).resolve()
        # If the service mounts an explicit volume into /app, that's the dir
        # the command runs from. Honour it for accuracy.
        volumes = svc.get("volumes") or []
        if isinstance(volumes, list):
            for v in volumes:
                if isinstance(v, str) and ":" in v:
                    src, dst = v.split(":", 1)
                    dst = dst.split(":", 1)[0]
                    if dst == "/app":
                        cwd = (workdir / src).resolve()
                        break
        if cwd.exists() and cwd.is_dir():
            return cmd_str, cwd
    return None


def _stringify_compose_command(cmd: object) -> str:
    if isinstance(cmd, str):
        return cmd
    if isinstance(cmd, list):
        return " ".join(str(x) for x in cmd)
    return ""


def _from_dockerfile(df: Path, workdir: Path) -> tuple[str, Path] | None:
    try:
        text = df.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    # CMD can be JSON-array form (`CMD ["uvicorn", ...]`) or shell form.
    m = re.search(r"^\s*CMD\s+(.+)$", text, re.MULTILINE)
    if not m:
        return None
    raw = m.group(1).strip()
    if raw.startswith("["):
        # JSON-array form. Strip brackets + quotes.
        try:
            import json as _json

            parts = _json.loads(raw)
            cmd_str = " ".join(parts)
        except Exception:
            return None
    else:
        cmd_str = raw
    if "uvicorn" not in cmd_str.lower():
        return None
    return cmd_str, df.parent


def _materialise_uvicorn_cmd(cmd_str: str, py: Path, port: int) -> list[str]:
    """Take the raw entrypoint command string and produce an executable list
    bound to the chosen Python + an ephemeral host/port."""
    tokens = cmd_str.split()
    if "uvicorn" in tokens:
        # Replace `uvicorn` with `python -m uvicorn` so it runs inside the
        # smoke-cached venv regardless of PATH.
        idx = tokens.index("uvicorn")
        tokens = [str(py), "-m", "uvicorn"] + tokens[idx + 1 :]
    # Force --host / --port to our values to avoid colliding with the
    # operator's choice + skip any container-only flags like --reload.
    cleaned: list[str] = []
    skip_next = False
    for t in tokens:
        if skip_next:
            skip_next = False
            continue
        if t in ("--reload", "--reload-dir"):
            continue
        if t in ("--host", "--port"):
            skip_next = True
            continue
        if t.startswith("--host=") or t.startswith("--port="):
            continue
        cleaned.append(t)
    cleaned += ["--host", "127.0.0.1", "--port", str(port)]
    return cleaned


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _probe(
    proc: subprocess.Popen[bytes],
    port: int,
    timeout_s: int,
    workdir: Path,
    cwd: Path,
) -> VerifierFailure | None:
    """Poll the launched process. Return a failure if (a) it dies or
    (b) no health endpoint responds < 500 within the timeout."""
    deadline = time.time() + timeout_s
    last_path: str | None = None
    while time.time() < deadline:
        if proc.poll() is not None:
            # Process exited before serving anything → it crashed at import.
            stderr = (proc.stderr.read() or b"").decode(errors="replace") if proc.stderr else ""
            tail = "\n".join(stderr.strip().splitlines()[-15:])[:2048]
            rel = _try_relative(cwd, workdir)
            return VerifierFailure(
                verifier="entrypoint",
                severity="error",
                category="entrypoint_crash",
                message=f"entrypoint exited (code={proc.returncode}) before serving",
                detail=tail or "(no stderr captured)",
                file=str(rel),
                exit_code=proc.returncode,
            )
        for path in _HEALTH_PATHS:
            try:
                r = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=1.5)
                if r.status < 500:
                    return None  # alive, served a non-error → pass
                last_path = path
            except Exception:
                last_path = path
        time.sleep(0.5)
    # Timed out without a healthy response.
    stderr = (proc.stderr.read() or b"").decode(errors="replace") if proc.stderr else ""
    tail = "\n".join(stderr.strip().splitlines()[-15:])[:2048]
    rel = _try_relative(cwd, workdir)
    return VerifierFailure(
        verifier="entrypoint",
        severity="error",
        category="entrypoint_timeout",
        message=f"entrypoint never served < 500 within {timeout_s}s",
        detail=tail or f"last probed: {last_path}",
        file=str(rel),
        exit_code=None,
    )


def _try_relative(p: Path, root: Path) -> Path:
    try:
        return p.relative_to(root)
    except ValueError:
        return p
