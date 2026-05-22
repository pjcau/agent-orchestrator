"""Job logger — persists all agent/graph task results to disk.

Each user session gets a working directory under `jobs/job_<session>/`.
Agent-created files (file_write, shell_exec) go into this directory.
Sessions expire after a configurable inactivity timeout and a new session
starts automatically. Empty session directories are cleaned up automatically.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JobLogger:
    """Session-aware job logger with inactivity timeout.

    - Creates `jobs/job_<session>/` lazily (only when first file is written)
    - Agent file operations use `session_dir` as working directory
    - Session resets after `inactivity_timeout_s` seconds of no activity
    - Empty session dirs are cleaned up after `empty_cleanup_s` seconds
    """

    def __init__(
        self,
        jobs_dir: str | Path | None = None,
        inactivity_timeout_s: float = 1800.0,  # 30 minutes
        empty_cleanup_s: float = 30.0,
    ):
        if jobs_dir is None:
            jobs_dir = Path(__file__).parent.parent.parent.parent / "jobs"
        self._base_dir = Path(jobs_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._inactivity_timeout = inactivity_timeout_s
        self._empty_cleanup_s = empty_cleanup_s
        self._session_id: str = ""
        self._session_dir: Path = Path()
        self._job_counter: int = 0
        self._last_activity: float = 0.0
        self._dir_created: bool = False
        self._start_new_session()

    def _start_new_session(self) -> None:
        """Prepare a fresh session (directory created lazily on first write)."""
        # Clean up previous session dir if it was empty
        self._cleanup_empty_current()
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        short_id = uuid.uuid4().hex[:6]
        self._session_id = f"{ts}_{short_id}"
        self._session_dir = self._base_dir / f"job_{self._session_id}"
        self._job_counter = 0
        self._last_activity = time.monotonic()
        self._dir_created = False

    def _ensure_dir(self) -> None:
        """Create the session directory if it doesn't exist yet."""
        if not self._dir_created:
            self._session_dir.mkdir(parents=True, exist_ok=True)
            self._dir_created = True

    def _cleanup_empty_current(self) -> None:
        """Remove current session dir if it's empty."""
        if (
            self._dir_created
            and self._session_dir.exists()
            and not any(self._session_dir.iterdir())
        ):
            try:
                self._session_dir.rmdir()
            except OSError:
                pass

    def cleanup_empty_sessions(self) -> int:
        """Remove all empty session directories older than empty_cleanup_s.

        Returns the number of directories removed.
        """
        if not self._base_dir.exists():
            return 0
        removed = 0
        now = time.time()
        for d in self._base_dir.iterdir():
            if not d.is_dir() or not d.name.startswith("job_"):
                continue
            # Skip current session
            sid = d.name[4:]
            if sid == self._session_id:
                continue
            # Check if empty
            if any(d.iterdir()):
                continue
            # Check age
            try:
                age = now - d.stat().st_mtime
                if age >= self._empty_cleanup_s:
                    d.rmdir()
                    removed += 1
            except OSError:
                continue
        return removed

    def _check_session(self) -> None:
        """Start a new session if the current one has timed out."""
        elapsed = time.monotonic() - self._last_activity
        if elapsed > self._inactivity_timeout:
            self._start_new_session()
        # Periodic cleanup of old empty dirs
        self.cleanup_empty_sessions()

    def touch(self) -> None:
        """Update last activity timestamp (call on any user interaction)."""
        self._check_session()
        self._last_activity = time.monotonic()

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def session_dir(self) -> Path:
        """Working directory for the current session (agent files go here)."""
        self._ensure_dir()
        return self._session_dir

    def log(self, job_type: str, data: dict[str, Any]) -> Path:
        """Save a job result to disk. Returns the path of the saved file."""
        self._check_session()
        self._last_activity = time.monotonic()
        self._ensure_dir()
        self._job_counter += 1

        record = {
            "session_id": self._session_id,
            "job_number": self._job_counter,
            "job_type": job_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **data,
        }

        filename = f"{self._job_counter:04d}_{job_type}.json"
        filepath = self._session_dir / filename
        filepath.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        return filepath

    def get_history(self) -> list[dict[str, Any]]:
        """Load all job records from the current session directory (sorted)."""
        records: list[dict[str, Any]] = []
        if not self._session_dir.exists():
            return records
        for f in sorted(self._session_dir.glob("*.json")):
            try:
                records.append(json.loads(f.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return records

    # ------------------------------------------------------------------
    # Ownership (per-user history isolation) — Phase 7.15
    # ------------------------------------------------------------------
    #
    # Each session dir may carry a sidecar `.owner` file containing a
    # single line — the canonical owner identifier (typically the logged-
    # in user's email from the JWT `sub` claim). The dashboard writes it
    # at session-create time and reads it for filtering. Sessions without
    # the file are treated as `_ANON_OWNER` ("shared"), which preserves
    # backwards compatibility with pre-existing on-disk sessions AND with
    # dev mode (ALLOW_DEV_MODE=true), where there is no authenticated
    # user. Only an explicit owner match unlocks ownership-gated
    # endpoints; the shared bucket stays visible to everyone.

    _ANON_OWNER = "shared"
    _OWNER_FILE = ".owner"

    def set_session_owner(self, owner: str | None) -> None:
        """Record the owner of the CURRENT session, if not already set.

        Idempotent: subsequent calls on the same session with a different
        owner are ignored. ``None`` / empty string falls back to the
        shared anonymous owner. The session directory is created if it
        doesn't exist yet (the owner file is the first persisted
        artefact of an authenticated session)."""
        canonical = (owner or self._ANON_OWNER).strip() or self._ANON_OWNER
        self._ensure_dir()
        owner_path = self._session_dir / self._OWNER_FILE
        if owner_path.exists():
            return
        try:
            owner_path.write_text(canonical, encoding="utf-8")
        except OSError:
            pass  # never crash a team_run because of an ownership write

    def get_session_owner(self, session_id: str) -> str:
        """Return the owner of a session, or ``"shared"`` if not labelled."""
        owner_file = self._base_dir / f"job_{session_id}" / self._OWNER_FILE
        try:
            return owner_file.read_text(encoding="utf-8").strip() or self._ANON_OWNER
        except OSError:
            return self._ANON_OWNER

    def user_can_access(self, session_id: str, user: str | None) -> bool:
        """Authorisation gate: a session is visible to its owner AND to
        anyone when the session is in the shared bucket. ``user=None`` is
        treated as the shared user (dev / unauthenticated)."""
        owner = self.get_session_owner(session_id)
        viewer = (user or self._ANON_OWNER).strip() or self._ANON_OWNER
        return owner == self._ANON_OWNER or owner == viewer

    def list_sessions(self, user: str | None = None) -> list[dict[str, Any]]:
        """List job sessions visible to *user* (None → shared/dev mode).

        A session is included when:
          - it has at least one file (empty dirs are filtered)
          - AND its owner equals *user* OR is the shared anonymous bucket.
        """
        viewer = (user or self._ANON_OWNER).strip() or self._ANON_OWNER
        sessions: list[dict[str, Any]] = []
        if not self._base_dir.exists():
            return sessions
        for d in sorted(self._base_dir.iterdir(), reverse=True):
            if not d.is_dir() or not d.name.startswith("job_"):
                continue
            # Skip empty directories (they'll be cleaned up). The sidecar
            # .owner file does NOT count as content for this check.
            content = [c for c in d.iterdir() if c.name != self._OWNER_FILE]
            if not content:
                continue
            session_id = d.name[4:]
            # Ownership filter.
            owner = self.get_session_owner(session_id)
            if not (owner == self._ANON_OWNER or owner == viewer):
                continue
            json_files = sorted(d.glob("*.json"))
            record_count = len(json_files)
            first_prompt = ""
            last_type = ""
            if json_files:
                try:
                    first = json.loads(json_files[0].read_text(encoding="utf-8"))
                    first_prompt = first.get("prompt", first.get("task", ""))[:80]
                    last = json.loads(json_files[-1].read_text(encoding="utf-8"))
                    last_type = last.get("job_type", "")
                except (json.JSONDecodeError, OSError):
                    pass
            all_files = [f for f in d.iterdir()
                         if f.is_file() and f.suffix != ".json" and f.name != self._OWNER_FILE]
            sessions.append(
                {
                    "session_id": session_id,
                    "dir_name": d.name,
                    "records": record_count,
                    "files": len(all_files),
                    "first_prompt": first_prompt,
                    "last_type": last_type,
                    "is_current": session_id == self._session_id,
                    "owner": owner,
                }
            )
        return sessions

    def load_session(self, session_id: str) -> list[dict[str, Any]]:
        """Load all records from a specific session."""
        session_dir = self._base_dir / f"job_{session_id}"
        if not session_dir.exists() or not session_dir.is_dir():
            return []
        records: list[dict[str, Any]] = []
        for f in sorted(session_dir.glob("*.json")):
            try:
                records.append(json.loads(f.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return records

    def switch_session(self, session_id: str) -> bool:
        """Switch to an existing session to continue work in it."""
        session_dir = self._base_dir / f"job_{session_id}"
        if not session_dir.exists() or not session_dir.is_dir():
            return False
        self._session_id = session_id
        self._session_dir = session_dir
        self._dir_created = True
        # Count existing records to continue numbering
        self._job_counter = len(list(session_dir.glob("*.json")))
        self._last_activity = time.monotonic()
        return True
