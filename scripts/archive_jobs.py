"""Archive old job session logs from local filesystem to S3.

Scans the `./jobs/` directory for session directories (`job_<session_id>/`),
identifies sessions where ALL files are older than the configured threshold,
tarballs each qualifying session, uploads it to S3, records metadata in
PostgreSQL, and removes the local directory.

Usage:
    python scripts/archive_jobs.py [--jobs-dir PATH] [--days N] [--dry-run]

Required environment variables:
    DATABASE_URL — PostgreSQL DSN (e.g. postgresql://user:pass@host:5432/db)

AWS credentials are expected to come from an IAM role (instance profile or
ECS task role). No access keys are read from environment variables.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import shutil
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    stream=sys.stdout,
)
log = logging.getLogger("archive_jobs")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

S3_BUCKET = "agent-orchestrator-jobs-archive"
S3_PREFIX = "archives"
TABLE_DDL = """
CREATE TABLE IF NOT EXISTS job_archives (
    id              SERIAL PRIMARY KEY,
    session_id      TEXT        NOT NULL UNIQUE,
    s3_key          TEXT        NOT NULL,
    record_count    INTEGER     NOT NULL DEFAULT 0,
    file_count      INTEGER     NOT NULL DEFAULT 0,
    total_size_bytes BIGINT     NOT NULL DEFAULT 0,
    archived_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    first_timestamp TEXT        NOT NULL DEFAULT '',
    last_timestamp  TEXT        NOT NULL DEFAULT ''
)
"""


# ---------------------------------------------------------------------------
# Session scanning
# ---------------------------------------------------------------------------


def _all_files(session_dir: Path) -> list[Path]:
    """Return every regular file inside *session_dir* (non-recursive depth)."""
    return [f for f in session_dir.iterdir() if f.is_file()]


def _oldest_mtime(files: list[Path]) -> float:
    """Return the most-recent mtime (epoch seconds) across all files.

    We want sessions where ALL files are older than the threshold, so we need
    the *newest* mtime — if even the newest file is older than the threshold,
    the whole session qualifies.
    """
    if not files:
        return 0.0
    return max(f.stat().st_mtime for f in files)


def find_old_sessions(jobs_dir: Path, max_age_days: int) -> list[Path]:
    """Return session directories whose newest file is older than *max_age_days*.

    Args:
        jobs_dir: Root directory that contains ``job_<session_id>/`` dirs.
        max_age_days: Age threshold in days.

    Returns:
        Sorted list of qualifying session directory paths.
    """
    cutoff = time.time() - max_age_days * 86_400
    results: list[Path] = []

    if not jobs_dir.exists():
        log.warning("Jobs directory does not exist: %s", jobs_dir)
        return results

    for entry in sorted(jobs_dir.iterdir()):
        if not entry.is_dir() or not entry.name.startswith("job_"):
            continue
        files = _all_files(entry)
        if not files:
            # Empty directory — treat as old so it gets cleaned up
            results.append(entry)
            continue
        newest_mtime = _oldest_mtime(files)
        if newest_mtime < cutoff:
            results.append(entry)

    return results


# ---------------------------------------------------------------------------
# Session metadata extraction
# ---------------------------------------------------------------------------


def _session_id_from_dir(session_dir: Path) -> str:
    """Strip the ``job_`` prefix from a session directory name."""
    return session_dir.name[4:]  # strip "job_"


def _extract_timestamps(session_dir: Path) -> tuple[str, str]:
    """Return (first_timestamp, last_timestamp) from the JSON records.

    Timestamps are ISO-8601 strings taken from the ``timestamp`` field that
    ``JobLogger.log()`` writes.  Falls back to empty strings on any error.
    """
    json_files = sorted(session_dir.glob("*.json"))
    if not json_files:
        return ("", "")

    first_ts = ""
    last_ts = ""
    try:
        first_data = json.loads(json_files[0].read_text(encoding="utf-8"))
        first_ts = first_data.get("timestamp", "")
    except (json.JSONDecodeError, OSError):
        pass
    try:
        last_data = json.loads(json_files[-1].read_text(encoding="utf-8"))
        last_ts = last_data.get("timestamp", "")
    except (json.JSONDecodeError, OSError):
        pass

    return (first_ts, last_ts)


def collect_session_metadata(session_dir: Path) -> dict[str, Any]:
    """Gather all metadata needed for the ``job_archives`` row.

    Returns:
        Dict with keys: session_id, record_count, file_count, total_size_bytes,
        first_timestamp, last_timestamp.
    """
    files = _all_files(session_dir)
    record_count = len(list(session_dir.glob("*.json")))
    total_size = sum(f.stat().st_size for f in files)
    first_ts, last_ts = _extract_timestamps(session_dir)
    return {
        "session_id": _session_id_from_dir(session_dir),
        "record_count": record_count,
        "file_count": len(files),
        "total_size_bytes": total_size,
        "first_timestamp": first_ts,
        "last_timestamp": last_ts,
    }


# ---------------------------------------------------------------------------
# Archive creation (in-memory tar.gz)
# ---------------------------------------------------------------------------


def create_tarball(session_dir: Path) -> bytes:
    """Create a gzip-compressed tar archive of *session_dir* in memory.

    The archive preserves the directory name as the top-level entry so that
    extraction produces ``job_<session_id>/``.

    Returns:
        Raw bytes of the .tar.gz archive.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(session_dir, arcname=session_dir.name)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# S3 upload
# ---------------------------------------------------------------------------


def _s3_key(session_id: str, archived_at: datetime) -> str:
    """Construct the S3 object key.

    Pattern: ``archives/<year>/<month>/<session_id>.tar.gz``
    """
    return f"{S3_PREFIX}/{archived_at.year}/{archived_at.month:02d}/{session_id}.tar.gz"


def upload_to_s3(tarball: bytes, s3_key: str) -> None:
    """Upload *tarball* bytes to S3.

    Credentials are resolved by boto3's default chain (IAM role / instance
    profile / ECS task role).  No keys are read from the environment here.

    Args:
        tarball: Raw .tar.gz bytes.
        s3_key: Full S3 object key (without bucket name).

    Raises:
        Exception: Propagates any boto3 / botocore error to the caller.
    """
    import boto3  # type: ignore[import]

    client = boto3.client("s3")
    client.put_object(
        Bucket=S3_BUCKET,
        Key=s3_key,
        Body=tarball,
        ContentType="application/gzip",
    )
    log.info("Uploaded s3://%s/%s (%d bytes)", S3_BUCKET, s3_key, len(tarball))


# ---------------------------------------------------------------------------
# PostgreSQL — synchronous via psycopg2
# ---------------------------------------------------------------------------


def _get_dsn() -> str:
    """Return the PostgreSQL DSN from the environment.

    Raises:
        RuntimeError: If DATABASE_URL is not set.
    """
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return dsn


def ensure_table(conn: Any) -> None:
    """Create the ``job_archives`` table if it does not exist."""
    with conn.cursor() as cur:
        cur.execute(TABLE_DDL)
    conn.commit()


def insert_archive_record(
    conn: Any,
    *,
    session_id: str,
    s3_key: str,
    record_count: int,
    file_count: int,
    total_size_bytes: int,
    archived_at: datetime,
    first_timestamp: str,
    last_timestamp: str,
) -> None:
    """Insert one row into ``job_archives``.

    Uses ``INSERT ... ON CONFLICT (session_id) DO NOTHING`` to make the
    operation idempotent — re-running the script after a partial failure will
    not produce duplicate rows.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO job_archives
                (session_id, s3_key, record_count, file_count,
                 total_size_bytes, archived_at, first_timestamp, last_timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (session_id) DO NOTHING
            """,
            (
                session_id,
                s3_key,
                record_count,
                file_count,
                total_size_bytes,
                archived_at,
                first_timestamp,
                last_timestamp,
            ),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Per-session archiving
# ---------------------------------------------------------------------------


def archive_session(
    session_dir: Path,
    conn: Any,
    dry_run: bool,
) -> bool:
    """Archive one session directory.

    Steps:
        1. Collect metadata.
        2. Create in-memory tarball.
        3. Upload to S3.
        4. Insert metadata row in PostgreSQL.
        5. Remove the local directory.

    The local directory is only removed if steps 3 and 4 both succeed.

    Args:
        session_dir: Path to the ``job_<session_id>/`` directory.
        conn: Open psycopg2 connection (ignored in dry-run mode).
        dry_run: If True, log what would happen without taking any action.

    Returns:
        True if the session was successfully archived (or would be in dry-run),
        False on any error.
    """
    meta = collect_session_metadata(session_dir)
    session_id = meta["session_id"]
    archived_at = datetime.now(timezone.utc)
    key = _s3_key(session_id, archived_at)

    if dry_run:
        log.info(
            "[DRY-RUN] Would archive session=%s  records=%d  files=%d  size=%d bytes  s3_key=%s",
            session_id,
            meta["record_count"],
            meta["file_count"],
            meta["total_size_bytes"],
            key,
        )
        return True

    log.info(
        "Archiving session=%s  records=%d  files=%d  size=%d bytes",
        session_id,
        meta["record_count"],
        meta["file_count"],
        meta["total_size_bytes"],
    )

    # --- Step 1: create tarball ---
    try:
        tarball = create_tarball(session_dir)
    except Exception as exc:
        log.error("Failed to create tarball for session %s: %s", session_id, exc)
        return False

    # --- Step 2: upload to S3 (abort on failure — local files are safe) ---
    try:
        upload_to_s3(tarball, key)
    except Exception as exc:
        log.error(
            "S3 upload failed for session %s (local files NOT deleted): %s",
            session_id,
            exc,
        )
        return False

    # --- Step 3: record metadata in PostgreSQL ---
    try:
        insert_archive_record(
            conn,
            session_id=session_id,
            s3_key=key,
            record_count=meta["record_count"],
            file_count=meta["file_count"],
            total_size_bytes=meta["total_size_bytes"],
            archived_at=archived_at,
            first_timestamp=meta["first_timestamp"],
            last_timestamp=meta["last_timestamp"],
        )
    except Exception as exc:
        log.error(
            "DB insert failed for session %s (local files NOT deleted, S3 object kept): %s",
            session_id,
            exc,
        )
        return False

    # --- Step 4: delete local directory ---
    try:
        shutil.rmtree(session_dir)
        log.info("Deleted local directory: %s", session_dir)
    except Exception as exc:
        log.warning(
            "Could not delete local directory %s (archive is in S3): %s",
            session_dir,
            exc,
        )
        # Not a fatal error — the archive exists and metadata is recorded

    return True


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Archive old job session logs to S3 and record metadata in PostgreSQL.",
    )
    parser.add_argument(
        "--jobs-dir",
        default="./jobs",
        help="Path to the jobs root directory (default: ./jobs)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Archive sessions whose newest file is older than this many days (default: 7)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be archived without uploading, inserting, or deleting anything",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the archiver.

    Returns:
        0 on success, 1 if any session failed to archive.
    """
    args = parse_args(argv)
    jobs_dir = Path(args.jobs_dir).resolve()
    dry_run: bool = args.dry_run
    max_age_days: int = args.days

    log.info(
        "Starting archive run — jobs_dir=%s  threshold=%d days  dry_run=%s",
        jobs_dir,
        max_age_days,
        dry_run,
    )

    sessions = find_old_sessions(jobs_dir, max_age_days)
    if not sessions:
        log.info("No sessions older than %d days found. Nothing to do.", max_age_days)
        return 0

    log.info("Found %d session(s) to archive.", len(sessions))

    conn = None
    if not dry_run:
        try:
            import psycopg2  # type: ignore[import]

            dsn = _get_dsn()
            conn = psycopg2.connect(dsn)
            ensure_table(conn)
        except Exception as exc:
            log.error("Cannot connect to PostgreSQL: %s", exc)
            return 1

    succeeded = 0
    failed = 0
    try:
        for session_dir in sessions:
            ok = archive_session(session_dir, conn, dry_run=dry_run)
            if ok:
                succeeded += 1
            else:
                failed += 1
    finally:
        if conn is not None:
            conn.close()

    log.info(
        "Archive run complete — succeeded=%d  failed=%d",
        succeeded,
        failed,
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
