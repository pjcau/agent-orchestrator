"""Job logger — persists all agent/graph task results to disk.

Each user session gets a working directory under `jobs/job_<session>/`.
Agent-created files (file_write, shell_exec) go into this directory.
Sessions expire after a configurable inactivity timeout and a new session
starts automatically.
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

    - Creates `jobs/job_<session>/` for each session
    - Agent file operations use `session_dir` as working directory
    - Session resets after `inactivity_timeout_s` seconds of no activity
    """

    def __init__(
        self,
        jobs_dir: str | Path | None = None,
        inactivity_timeout_s: float = 1800.0,  # 30 minutes
    ):
        if jobs_dir is None:
            jobs_dir = Path(__file__).parent.parent.parent.parent / "jobs"
        self._base_dir = Path(jobs_dir)
        self._inactivity_timeout = inactivity_timeout_s
        self._session_id: str = ""
        self._session_dir: Path = Path()
        self._job_counter: int = 0
        self._last_activity: float = 0.0
        self._start_new_session()

    def _start_new_session(self) -> None:
        """Create a fresh session directory."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        short_id = uuid.uuid4().hex[:6]
        self._session_id = f"{ts}_{short_id}"
        self._session_dir = self._base_dir / f"job_{self._session_id}"
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._job_counter = 0
        self._last_activity = time.monotonic()

    def _check_session(self) -> None:
        """Start a new session if the current one has timed out."""
        elapsed = time.monotonic() - self._last_activity
        if elapsed > self._inactivity_timeout:
            self._start_new_session()

    def touch(self) -> None:
        """Update last activity timestamp (call on any user interaction)."""
        self._check_session()
        self._last_activity = time.monotonic()

    @property
    def session_id(self) -> str:
        self._check_session()
        return self._session_id

    @property
    def session_dir(self) -> Path:
        """Working directory for the current session (agent files go here)."""
        self._check_session()
        return self._session_dir

    def log(self, job_type: str, data: dict[str, Any]) -> Path:
        """Save a job result to disk. Returns the path of the saved file."""
        self._check_session()
        self._last_activity = time.monotonic()
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
