"""Tests for scripts/archive_jobs.py."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Load the script as a module (mirrors the pattern used in test_research_scout)
# ---------------------------------------------------------------------------

SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "archive_jobs.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("archive_jobs", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def arch():
    """Load archive_jobs module once for all tests in this module."""
    return _load_module()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(tmp_path: Path, name: str, age_days: float = 8.0) -> Path:
    """Create a fake session directory with one JSON record and one extra file.

    All files are back-dated by *age_days* days.
    """
    session_dir = tmp_path / name
    session_dir.mkdir()

    record = {
        "session_id": name[4:],
        "job_number": 1,
        "job_type": "agent_task",
        "timestamp": "2024-01-01T00:00:00+00:00",
        "prompt": "hello world",
    }
    json_file = session_dir / "0001_agent_task.json"
    json_file.write_text(json.dumps(record, indent=2), encoding="utf-8")

    extra_file = session_dir / "output.txt"
    extra_file.write_text("some output", encoding="utf-8")

    # Back-date both files
    old_mtime = time.time() - age_days * 86_400
    for f in [json_file, extra_file]:
        import os

        os.utime(f, (old_mtime, old_mtime))

    return session_dir


# ---------------------------------------------------------------------------
# find_old_sessions
# ---------------------------------------------------------------------------


class TestFindOldSessions:
    def test_returns_empty_when_jobs_dir_missing(self, arch, tmp_path):
        missing = tmp_path / "no_such_dir"
        result = arch.find_old_sessions(missing, max_age_days=7)
        assert result == []

    def test_ignores_non_job_dirs(self, arch, tmp_path):
        (tmp_path / "something_else").mkdir()
        result = arch.find_old_sessions(tmp_path, max_age_days=7)
        assert result == []

    def test_finds_old_session(self, arch, tmp_path):
        _make_session(tmp_path, "job_20240101_000000_aabbcc", age_days=10)
        result = arch.find_old_sessions(tmp_path, max_age_days=7)
        assert len(result) == 1
        assert result[0].name == "job_20240101_000000_aabbcc"

    def test_skips_recent_session(self, arch, tmp_path):
        _make_session(tmp_path, "job_20240101_000000_aabbcc", age_days=2)
        result = arch.find_old_sessions(tmp_path, max_age_days=7)
        assert result == []

    def test_skips_if_any_file_is_recent(self, arch, tmp_path):
        """A session with even one recent file must NOT be archived."""
        session_dir = _make_session(tmp_path, "job_20240101_000000_xxyyzz", age_days=10)
        # Add a fresh file
        fresh = session_dir / "new_file.txt"
        fresh.write_text("fresh")
        result = arch.find_old_sessions(tmp_path, max_age_days=7)
        assert result == []

    def test_empty_session_dir_is_included(self, arch, tmp_path):
        empty_dir = tmp_path / "job_20240101_000000_empty"
        empty_dir.mkdir()
        result = arch.find_old_sessions(tmp_path, max_age_days=7)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# collect_session_metadata
# ---------------------------------------------------------------------------


class TestCollectSessionMetadata:
    def test_basic_metadata(self, arch, tmp_path):
        session_dir = _make_session(tmp_path, "job_20240101_000000_aabbcc", age_days=10)
        meta = arch.collect_session_metadata(session_dir)
        assert meta["session_id"] == "20240101_000000_aabbcc"
        assert meta["record_count"] == 1
        assert meta["file_count"] == 2  # .json + output.txt
        assert meta["total_size_bytes"] > 0
        assert meta["first_timestamp"] == "2024-01-01T00:00:00+00:00"
        assert meta["last_timestamp"] == "2024-01-01T00:00:00+00:00"

    def test_empty_session_dir(self, arch, tmp_path):
        session_dir = tmp_path / "job_empty_session"
        session_dir.mkdir()
        meta = arch.collect_session_metadata(session_dir)
        assert meta["session_id"] == "empty_session"
        assert meta["record_count"] == 0
        assert meta["file_count"] == 0
        assert meta["total_size_bytes"] == 0
        assert meta["first_timestamp"] == ""
        assert meta["last_timestamp"] == ""


# ---------------------------------------------------------------------------
# create_tarball
# ---------------------------------------------------------------------------


class TestCreateTarball:
    def test_creates_valid_tarball(self, arch, tmp_path):
        import tarfile

        session_dir = _make_session(tmp_path, "job_20240101_000000_tgz", age_days=10)
        tarball = arch.create_tarball(session_dir)
        assert len(tarball) > 0
        # Verify it is actually a valid gzip tarball
        import io

        with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
            names = tar.getnames()
        assert any("job_20240101_000000_tgz" in n for n in names)

    def test_top_level_is_session_dir_name(self, arch, tmp_path):
        import io
        import tarfile

        session_dir = _make_session(tmp_path, "job_20240101_000000_named", age_days=10)
        tarball = arch.create_tarball(session_dir)
        with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
            names = tar.getnames()
        # Top-level entry should be the bare directory name
        assert "job_20240101_000000_named" in names


# ---------------------------------------------------------------------------
# _s3_key
# ---------------------------------------------------------------------------


class TestS3Key:
    def test_key_format(self, arch):
        from datetime import datetime, timezone

        dt = datetime(2024, 3, 5, tzinfo=timezone.utc)
        key = arch._s3_key("20240305_120000_abcdef", dt)
        assert key == "archives/2024/03/20240305_120000_abcdef.tar.gz"

    def test_key_zero_pads_month(self, arch):
        from datetime import datetime, timezone

        dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        key = arch._s3_key("sess", dt)
        assert "/01/" in key


# ---------------------------------------------------------------------------
# archive_session — dry-run
# ---------------------------------------------------------------------------


class TestArchiveSessionDryRun:
    def test_dry_run_does_not_upload_or_delete(self, arch, tmp_path):
        session_dir = _make_session(tmp_path, "job_20240101_000000_dry", age_days=10)
        result = arch.archive_session(session_dir, conn=None, dry_run=True)
        assert result is True
        assert session_dir.exists(), "dry-run must not delete local directory"

    def test_dry_run_does_not_touch_db(self, arch, tmp_path):
        session_dir = _make_session(tmp_path, "job_20240101_000000_drydb", age_days=10)
        conn = MagicMock()
        result = arch.archive_session(session_dir, conn=conn, dry_run=True)
        assert result is True
        conn.cursor.assert_not_called()


# ---------------------------------------------------------------------------
# archive_session — real path (mocked S3 + DB)
# ---------------------------------------------------------------------------


class TestArchiveSessionReal:
    def test_happy_path_deletes_local_dir(self, arch, tmp_path):
        session_dir = _make_session(tmp_path, "job_20240101_000000_ok", age_days=10)
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: cursor
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(arch, "upload_to_s3"):
            result = arch.archive_session(session_dir, conn=conn, dry_run=False)

        assert result is True
        assert not session_dir.exists(), "session directory should be removed after archive"

    def test_s3_failure_keeps_local_dir(self, arch, tmp_path):
        session_dir = _make_session(tmp_path, "job_20240101_000000_s3fail", age_days=10)
        conn = MagicMock()

        with patch.object(arch, "upload_to_s3", side_effect=RuntimeError("S3 down")):
            result = arch.archive_session(session_dir, conn=conn, dry_run=False)

        assert result is False
        assert session_dir.exists(), "local directory must be preserved when S3 fails"

    def test_db_failure_keeps_local_dir(self, arch, tmp_path):
        session_dir = _make_session(tmp_path, "job_20240101_000000_dbfail", age_days=10)
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("DB error")
        conn.cursor.return_value.__enter__ = lambda s: cursor
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(arch, "upload_to_s3"):
            result = arch.archive_session(session_dir, conn=conn, dry_run=False)

        assert result is False
        assert session_dir.exists(), "local directory must be preserved when DB insert fails"


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------


class TestParseArgs:
    def test_defaults(self, arch):
        args = arch.parse_args([])
        assert args.jobs_dir == "./jobs"
        assert args.days == 7
        assert args.dry_run is False

    def test_custom_flags(self, arch):
        args = arch.parse_args(["--jobs-dir", "/data/jobs", "--days", "14", "--dry-run"])
        assert args.jobs_dir == "/data/jobs"
        assert args.days == 14
        assert args.dry_run is True


# ---------------------------------------------------------------------------
# main — integration smoke test
# ---------------------------------------------------------------------------


class TestMain:
    def test_main_returns_0_when_nothing_to_archive(self, arch, tmp_path):
        result = arch.main(["--jobs-dir", str(tmp_path), "--days", "7"])
        assert result == 0

    def test_main_dry_run_no_db_needed(self, arch, tmp_path):
        _make_session(tmp_path, "job_20240101_000000_maindr", age_days=10)
        result = arch.main(["--jobs-dir", str(tmp_path), "--days", "7", "--dry-run"])
        assert result == 0

    def test_main_returns_1_on_db_error(self, arch, tmp_path):
        _make_session(tmp_path, "job_20240101_000000_mainfail", age_days=10)

        # psycopg2 may not be installed in the dev venv (it lives inside Docker).
        # Patch the import inside the archive_jobs module's main() function so
        # that the import succeeds but connect() raises.
        fake_psycopg2 = MagicMock()
        fake_psycopg2.connect.side_effect = RuntimeError("cannot connect")

        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://bad:bad@bad/bad"}):
            with patch.dict(sys.modules, {"psycopg2": fake_psycopg2}):
                result = arch.main(["--jobs-dir", str(tmp_path), "--days", "7"])
        assert result == 1
