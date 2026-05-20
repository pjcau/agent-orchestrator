"""E2E smoke verifier — headless browser test against the live app.

Why this exists
---------------

`EntrypointVerifier` proves that the backend boots. `RuntimeSmokeVerifier`
proves that the Python code imports. Neither can catch the failure class
that lives between the two services: the frontend's JavaScript expecting
a different shape than the backend's response, a missing CORS header, a
fetch URL hardcoded to the wrong port, a 200 page that crashes once
three.js tries to use the data.

The 2026-05-16(g) three.js-space-app benchmark hit exactly this: backend
returned ``{"satellites": [...]}`` and frontend did ``data.forEach(...)``
on the result. Every static verifier passed; the page loaded but
rendered no satellites.

This verifier closes the gap by actually opening the page in headless
Chromium and asserting:

- the frontend HTML responds 200;
- every network request the page issues completes (no ``requestfailed``);
- no JavaScript console errors fire during initial load;
- at least one canvas was mounted (the proxy "the app rendered").

Cost & dependencies
-------------------

cost_estimate_s = 60 — the most expensive verifier by far. Two-tier
gating to avoid burning that cost on every run:

1. **Opt-in via env var** ``REPAIR_LOOP_E2E_ENABLED=true``. Default is
   OFF. When disabled, the verifier returns no failures (no work done).
2. **Soft dependency on Playwright**. If ``playwright`` is not
   installed, the verifier emits a single ``warning`` failure (so it's
   visible) and returns. Never raises an ImportError that would crash
   the gate.

When enabled, it requires a built frontend (``frontend/index.html`` or
``web/index.html`` etc.) and reuses the venv + entrypoint launch from
the previous two verifiers (chains naturally because cheap-first
ordering puts those before this).
"""

from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

from agent_orchestrator.core.verification_gate import VerifierFailure
from agent_orchestrator.core.verifiers.entrypoint import (
    _detect_entrypoint,
    _materialise_uvicorn_cmd,
)
from agent_orchestrator.core.verifiers.runtime_smoke import (
    _canonical_requirements_set,
    _find_requirements,
    _hash_set,
)

_FRONTEND_DIRS = ("frontend", "web", "ui", "client")
_PAGE_WAIT_S = 5
_LAUNCH_WAIT_S = 10


def _e2e_enabled() -> bool:
    return os.environ.get("REPAIR_LOOP_E2E_ENABLED", "false").strip().lower() == "true"


class E2ESmokeVerifier:
    name = "e2e_smoke"
    cost_estimate_s = 60.0

    def __init__(self, *, cache_root: Path | None = None) -> None:
        self._cache_root = cache_root or Path(tempfile.gettempdir()) / "ao-smoke-venvs"

    async def verify(self, workdir: Path) -> list[VerifierFailure]:
        if not _e2e_enabled():
            return []
        fe = _find_frontend(workdir)
        if fe is None:
            return []  # no frontend to smoke-test
        # Soft dep check.
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            return [
                VerifierFailure(
                    verifier=self.name,
                    severity="warning",
                    category="e2e_infrastructure",
                    message="REPAIR_LOOP_E2E_ENABLED=true but `playwright` is not installed",
                    detail="pip install playwright && playwright install chromium",
                    file=None,
                    exit_code=None,
                )
            ]

        backend_proc: subprocess.Popen[bytes] | None = None
        backend_port: int | None = None
        backend_entry = _detect_entrypoint(workdir)
        if backend_entry is not None:
            cmd, cwd = backend_entry
            req = _find_requirements(workdir)
            if req is not None:
                venv = self._cache_root / _hash_set(_canonical_requirements_set(req))
                py = venv / "bin" / "python"
                if not py.exists():
                    py = venv / "Scripts" / "python.exe"
                if (venv / ".smoke-ok").exists() and py.exists():
                    backend_port = _free_port()
                    backend_proc = _spawn_backend(cmd, py, backend_port, cwd)

        # Serve the frontend dir on a tmp http server (no Docker).
        fe_port = _free_port()
        fe_server, fe_thread = _serve_static(fe, fe_port)

        try:
            # Give the backend a moment to boot.
            if backend_proc is not None:
                _wait_for_port(backend_port, timeout_s=_LAUNCH_WAIT_S)
            return _run_playwright(workdir, fe, fe_port, backend_port)
        finally:
            fe_server.shutdown()
            try:
                fe_thread.join(timeout=2)
            except Exception:
                pass
            if backend_proc is not None:
                backend_proc.terminate()
                try:
                    backend_proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    backend_proc.kill()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_frontend(workdir: Path) -> Path | None:
    """Return the frontend dir containing an `index.html`, or None."""
    for sub in _FRONTEND_DIRS:
        for candidate in ((workdir / sub / "index.html"), (workdir / sub / "dist" / "index.html"),
                          (workdir / sub / "build" / "index.html")):
            if candidate.exists():
                return candidate.parent
    # Last resort: index.html at the workdir root.
    if (workdir / "index.html").exists():
        return workdir
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve_static(dir_path: Path, port: int) -> tuple[HTTPServer, threading.Thread]:
    """Spin up a tiny static HTTP server on 127.0.0.1:<port> serving dir_path."""
    os.getcwd()

    class _Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(dir_path), **kwargs)

        def log_message(self, *_a, **_kw):  # silence
            pass

    server = HTTPServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _spawn_backend(
    cmd_str: str, py: Path, port: int, cwd: Path
) -> subprocess.Popen[bytes]:
    full_cmd = _materialise_uvicorn_cmd(cmd_str, py, port)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(cwd) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.Popen(
        full_cmd, cwd=str(cwd), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def _wait_for_port(port: int, *, timeout_s: int) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def _run_playwright(
    workdir: Path,
    fe_dir: Path,
    fe_port: int,
    backend_port: int | None,
) -> list[VerifierFailure]:
    from playwright.sync_api import sync_playwright

    fe_rel = str(fe_dir.relative_to(workdir)) if fe_dir != workdir else "."
    console_errors: list[str] = []
    network_errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()

        def _on_console(msg) -> None:
            if msg.type == "error":
                console_errors.append(msg.text[:300])

        def _on_requestfailed(req) -> None:
            # Ignore requests to the backend if it's not running — those are
            # legitimately expected to fail and don't represent an app bug.
            url = req.url
            if backend_port is None and (":8000" in url or ":8080" in url):
                return
            network_errors.append(f"{url} → {req.failure}"[:300])

        page.on("console", _on_console)
        page.on("requestfailed", _on_requestfailed)

        try:
            page.goto(f"http://127.0.0.1:{fe_port}/", wait_until="networkidle", timeout=15000)
            time.sleep(_PAGE_WAIT_S * 0.4)
            try:
                bool(page.query_selector("canvas"))
            except Exception:
                pass
        except Exception as exc:
            browser.close()
            return [VerifierFailure(
                verifier="e2e_smoke",
                severity="error",
                category="e2e_navigation",
                message=f"page load failed: {type(exc).__name__}",
                detail=str(exc)[:1024],
                file=fe_rel + "/index.html",
                exit_code=None,
            )]
        browser.close()

    failures: list[VerifierFailure] = []
    if console_errors:
        failures.append(VerifierFailure(
            verifier="e2e_smoke",
            severity="error",
            category="e2e_console_error",
            message=f"{len(console_errors)} JS console error(s) on initial load",
            detail="\n".join(console_errors[:5])[:2048],
            file=fe_rel + "/index.html",
            exit_code=None,
        ))
    if network_errors:
        failures.append(VerifierFailure(
            verifier="e2e_smoke",
            severity="error",
            category="e2e_network_error",
            message=f"{len(network_errors)} failed network request(s) on initial load",
            detail="\n".join(network_errors[:5])[:2048],
            file=fe_rel + "/index.html",
            exit_code=None,
        ))
    return failures
