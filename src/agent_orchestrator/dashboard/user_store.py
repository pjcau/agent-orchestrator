"""Persistent user store — PostgreSQL-backed, admin-controlled access.

The admin is identified by GITHUB_USERNAME env var. They are auto-created
on first login. All other users must be approved by the admin.

Storage: PostgreSQL (dashboard_users + dashboard_pending tables).
Falls back to JSON files (dashboard-users.json, dashboard-pending.json) when
the database is unavailable.
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from ..core.users import ROLE_PERMISSIONS, UserRole

logger = logging.getLogger(__name__)

# JSON fallback paths (used when Postgres is unavailable)
USERS_FILE = Path("dashboard-users.json")
PENDING_FILE = Path("dashboard-pending.json")

# Module-level DB state (set by setup_db)
_pool = None
_db_available = False


# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _acquire():
    """Acquire a DB connection with auto-reconnect on stale pool."""
    global _pool, _db_available
    try:
        async with _pool.acquire() as conn:
            yield conn
    except Exception as exc:
        exc_name = type(exc).__name__
        if "ConnectionDoesNotExist" in exc_name or "InterfaceError" in exc_name:
            logger.warning("User store DB connection lost (%s), reconnecting", exc_name)
            await _reconnect_pool()
            async with _pool.acquire() as conn:
                yield conn
        else:
            raise


async def _reconnect_pool() -> None:
    """Close stale pool and create a fresh one."""
    global _pool, _db_available
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        _db_available = False
        return
    try:
        if _pool:
            await _pool.close()
    except Exception:
        pass
    try:
        import asyncpg

        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3, command_timeout=10)
        _db_available = True
    except Exception:
        _db_available = False


async def setup_db(dsn: str | None = None) -> bool:
    """Initialize DB connection pool and create tables.

    Called once at app startup. Returns True if DB is available.
    """
    global _pool, _db_available
    dsn = dsn or os.environ.get("DATABASE_URL", "")
    if not dsn:
        return False
    try:
        import asyncpg

        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3, command_timeout=10)
        async with _acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS dashboard_users (
                    github_login TEXT PRIMARY KEY,
                    email TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT 'viewer',
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS dashboard_pending (
                    github_login TEXT PRIMARY KEY,
                    email TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    requested_at DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
                )
            """)
        _db_available = True
        # Migrate JSON data to DB if files exist
        await _migrate_json_to_db()
        return True
    except Exception:
        _db_available = False
        return False


async def _migrate_json_to_db() -> None:
    """One-time migration: import JSON file data into Postgres, then remove files."""
    if not _db_available or not _pool:
        return

    # Migrate users
    if USERS_FILE.exists():
        try:
            users = json.loads(USERS_FILE.read_text(encoding="utf-8"))
            async with _acquire() as conn:
                for _key, u in users.items():
                    await conn.execute(
                        """INSERT INTO dashboard_users
                           (github_login, email, name, role, active, created_at)
                           VALUES ($1, $2, $3, $4, $5, $6)
                           ON CONFLICT (github_login) DO NOTHING""",
                        u.get("github_login", _key).lower(),
                        u.get("email", ""),
                        u.get("name", ""),
                        u.get("role", "viewer"),
                        u.get("active", True),
                        u.get("created_at", time.time()),
                    )
            USERS_FILE.rename(USERS_FILE.with_suffix(".json.migrated"))
        except Exception:
            pass

    # Migrate pending
    if PENDING_FILE.exists():
        try:
            pending = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
            async with _acquire() as conn:
                for _key, p in pending.items():
                    await conn.execute(
                        """INSERT INTO dashboard_pending
                           (github_login, email, name, requested_at)
                           VALUES ($1, $2, $3, $4)
                           ON CONFLICT (github_login) DO NOTHING""",
                        p.get("github_login", _key).lower(),
                        p.get("email", ""),
                        p.get("name", ""),
                        p.get("requested_at", time.time()),
                    )
            PENDING_FILE.rename(PENDING_FILE.with_suffix(".json.migrated"))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# JSON fallback (sync functions for when DB is unavailable)
# ---------------------------------------------------------------------------


def _load_users_json() -> dict[str, Any]:
    if not USERS_FILE.exists():
        return {}
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_users_json(users: dict[str, Any]) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")


def _load_pending_json() -> dict[str, Any]:
    if not PENDING_FILE.exists():
        return {}
    try:
        return json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_pending_json(pending: dict[str, Any]) -> None:
    PENDING_FILE.write_text(json.dumps(pending, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_admin_github() -> str:
    """Return the GitHub username of the admin (from GITHUB_USERNAME env var)."""
    return os.environ.get("GITHUB_USERNAME", "").lower()


def _row_to_user(row) -> dict[str, Any]:
    """Convert an asyncpg Record to a user dict."""
    return {
        "github_login": row["github_login"],
        "email": row["email"],
        "name": row["name"],
        "role": row["role"],
        "active": row["active"],
        "created_at": row["created_at"],
    }


def _row_to_pending(row) -> dict[str, Any]:
    """Convert an asyncpg Record to a pending dict."""
    return {
        "github_login": row["github_login"],
        "email": row["email"],
        "name": row["name"],
        "requested_at": row["requested_at"],
    }


# ---------------------------------------------------------------------------
# User CRUD — DB with JSON fallback
# ---------------------------------------------------------------------------


async def async_get_or_create_user(
    github_login: str, email: str, name: str
) -> dict[str, Any] | None:
    """Async version — use this from async contexts (e.g. OAuth callbacks).

    Calls the DB directly without thread-pool workarounds.
    """
    if _db_available and _pool:
        try:
            return await _get_or_create_user_db(github_login, email, name)
        except Exception:
            pass  # Fall through to JSON
    return _get_or_create_user_json(github_login, email, name)


def get_or_create_user(github_login: str, email: str, name: str) -> dict[str, Any] | None:
    """Get or create a user after GitHub OAuth login.

    - Admin (GITHUB_USERNAME) is auto-created with admin role.
    - Known users are returned if active.
    - Unknown users are rejected (return None) and saved as pending.

    Note: Prefer async_get_or_create_user from async contexts.
    This sync version uses a thread-pool wrapper for DB access.
    """
    if _db_available and _pool:
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    return executor.submit(
                        _sync_run, _get_or_create_user_db(github_login, email, name)
                    ).result(timeout=5)
            else:
                return loop.run_until_complete(_get_or_create_user_db(github_login, email, name))
        except Exception:
            pass  # Fall through to JSON
    return _get_or_create_user_json(github_login, email, name)


def _sync_run(coro):
    """Run a coroutine in a new event loop (for thread-pool executor)."""
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _get_or_create_user_db(github_login: str, email: str, name: str) -> dict[str, Any] | None:
    key = github_login.lower()
    admin_login = _get_admin_github()

    async with _acquire() as conn:
        # Admin auto-creation
        if key == admin_login:
            await conn.execute(
                """INSERT INTO dashboard_users (github_login, email, name, role, active, created_at)
                   VALUES ($1, $2, $3, $4, TRUE, $5)
                   ON CONFLICT (github_login) DO UPDATE
                   SET email = COALESCE(NULLIF($2, ''), dashboard_users.email),
                       name = COALESCE(NULLIF($3, ''), dashboard_users.name)""",
                key,
                email,
                name,
                UserRole.ADMIN.value,
                time.time(),
            )

        row = await conn.fetchrow("SELECT * FROM dashboard_users WHERE github_login = $1", key)

        if row:
            # Update name/email on each login
            await conn.execute(
                """UPDATE dashboard_users
                   SET email = COALESCE(NULLIF($2, ''), email),
                       name = COALESCE(NULLIF($3, ''), name)
                   WHERE github_login = $1""",
                key,
                email,
                name,
            )
            if not row["active"]:
                return None
            user = _row_to_user(row)
            user["email"] = email or user["email"]
            user["name"] = name or user["name"]
            return user

        # Unknown user — save as pending
        await conn.execute(
            """INSERT INTO dashboard_pending (github_login, email, name, requested_at)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (github_login)
               DO UPDATE SET email = $2, name = $3, requested_at = $4""",
            key,
            email,
            name or github_login,
            time.time(),
        )
        return None


def _get_or_create_user_json(github_login: str, email: str, name: str) -> dict[str, Any] | None:
    """JSON fallback for get_or_create_user."""
    users = _load_users_json()
    key = github_login.lower()
    admin_login = _get_admin_github()

    if key == admin_login and key not in users:
        users[key] = {
            "github_login": github_login,
            "email": email,
            "name": name,
            "role": UserRole.ADMIN.value,
            "active": True,
            "created_at": time.time(),
        }
        _save_users_json(users)

    if key in users:
        user = users[key]
        user["email"] = email or user.get("email", "")
        user["name"] = name or user.get("name", "")
        _save_users_json(users)
        if not user.get("active", False):
            return None
        return user

    _add_pending_request_json(github_login, email, name)
    return None


# ---------------------------------------------------------------------------
# List users
# ---------------------------------------------------------------------------


def list_users() -> list[dict[str, Any]]:
    """List all users (for admin)."""
    if _db_available and _pool:
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    return executor.submit(_sync_run, _list_users_db()).result(timeout=5)
            return loop.run_until_complete(_list_users_db())
        except Exception:
            pass
    users = _load_users_json()
    return list(users.values())


async def _list_users_db() -> list[dict[str, Any]]:
    async with _acquire() as conn:
        rows = await conn.fetch("SELECT * FROM dashboard_users ORDER BY created_at")
        return [_row_to_user(r) for r in rows]


# ---------------------------------------------------------------------------
# Approve user
# ---------------------------------------------------------------------------


def approve_user(
    github_login: str,
    role: str = "developer",
    email: str = "",
    name: str = "",
) -> dict[str, Any]:
    """Approve a new user (admin action). Creates or updates user entry."""
    if _db_available and _pool:
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    return executor.submit(
                        _sync_run, _approve_user_db(github_login, role, email, name)
                    ).result(timeout=5)
            return loop.run_until_complete(_approve_user_db(github_login, role, email, name))
        except Exception:
            pass
    return _approve_user_json(github_login, role, email, name)


async def _approve_user_db(github_login: str, role: str, email: str, name: str) -> dict[str, Any]:
    key = github_login.lower()
    async with _acquire() as conn:
        await conn.execute(
            """INSERT INTO dashboard_users (github_login, email, name, role, active, created_at)
               VALUES ($1, $2, $3, $4, TRUE, $5)
               ON CONFLICT (github_login)
               DO UPDATE SET role = $4, active = TRUE""",
            key,
            email,
            name or github_login,
            role,
            time.time(),
        )
        row = await conn.fetchrow("SELECT * FROM dashboard_users WHERE github_login = $1", key)
        return _row_to_user(row)


def _approve_user_json(github_login: str, role: str, email: str, name: str) -> dict[str, Any]:
    users = _load_users_json()
    key = github_login.lower()
    if key in users:
        users[key]["role"] = role
        users[key]["active"] = True
    else:
        users[key] = {
            "github_login": github_login,
            "email": email,
            "name": name or github_login,
            "role": role,
            "active": True,
            "created_at": time.time(),
        }
    _save_users_json(users)
    return users[key]


# ---------------------------------------------------------------------------
# Update role
# ---------------------------------------------------------------------------


def update_user_role(github_login: str, role: str) -> bool:
    """Change a user's role. Returns True if user found."""
    if _db_available and _pool:
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    return executor.submit(
                        _sync_run, _update_user_role_db(github_login, role)
                    ).result(timeout=5)
            return loop.run_until_complete(_update_user_role_db(github_login, role))
        except Exception:
            pass
    users = _load_users_json()
    key = github_login.lower()
    if key not in users:
        return False
    users[key]["role"] = role
    _save_users_json(users)
    return True


async def _update_user_role_db(github_login: str, role: str) -> bool:
    key = github_login.lower()
    async with _acquire() as conn:
        result = await conn.execute(
            "UPDATE dashboard_users SET role = $2 WHERE github_login = $1", key, role
        )
        return result != "UPDATE 0"


# ---------------------------------------------------------------------------
# Deactivate user
# ---------------------------------------------------------------------------


def deactivate_user(github_login: str) -> bool:
    """Deactivate a user (deny access). Returns True if found."""
    if _db_available and _pool:
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    return executor.submit(_sync_run, _deactivate_user_db(github_login)).result(
                        timeout=5
                    )
            return loop.run_until_complete(_deactivate_user_db(github_login))
        except Exception:
            pass
    users = _load_users_json()
    key = github_login.lower()
    if key not in users:
        return False
    users[key]["active"] = False
    _save_users_json(users)
    return True


async def _deactivate_user_db(github_login: str) -> bool:
    key = github_login.lower()
    async with _acquire() as conn:
        result = await conn.execute(
            "UPDATE dashboard_users SET active = FALSE WHERE github_login = $1", key
        )
        return result != "UPDATE 0"


# ---------------------------------------------------------------------------
# Delete user
# ---------------------------------------------------------------------------


def delete_user(github_login: str) -> bool:
    """Delete a user entirely. Returns True if found."""
    if _db_available and _pool:
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    return executor.submit(_sync_run, _delete_user_db(github_login)).result(
                        timeout=5
                    )
            return loop.run_until_complete(_delete_user_db(github_login))
        except Exception:
            pass
    users = _load_users_json()
    key = github_login.lower()
    if key not in users:
        return False
    del users[key]
    _save_users_json(users)
    return True


async def _delete_user_db(github_login: str) -> bool:
    key = github_login.lower()
    async with _acquire() as conn:
        result = await conn.execute("DELETE FROM dashboard_users WHERE github_login = $1", key)
        return result != "DELETE 0"


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


def get_permissions(role: str) -> set[str]:
    """Get permissions for a role."""
    try:
        return ROLE_PERMISSIONS[UserRole(role)]
    except (ValueError, KeyError):
        return ROLE_PERMISSIONS[UserRole.VIEWER]


# ---------------------------------------------------------------------------
# Pending access requests
# ---------------------------------------------------------------------------


def _add_pending_request_json(github_login: str, email: str, name: str) -> None:
    """JSON fallback: Record a pending access request."""
    pending = _load_pending_json()
    key = github_login.lower()
    pending[key] = {
        "github_login": github_login,
        "email": email,
        "name": name or github_login,
        "requested_at": time.time(),
    }
    _save_pending_json(pending)


def list_pending() -> list[dict[str, Any]]:
    """List all pending access requests (for admin)."""
    if _db_available and _pool:
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    return executor.submit(_sync_run, _list_pending_db()).result(timeout=5)
            return loop.run_until_complete(_list_pending_db())
        except Exception:
            pass
    pending = _load_pending_json()
    return list(pending.values())


async def _list_pending_db() -> list[dict[str, Any]]:
    async with _acquire() as conn:
        rows = await conn.fetch("SELECT * FROM dashboard_pending ORDER BY requested_at DESC")
        return [_row_to_pending(r) for r in rows]


def approve_pending(github_login: str, role: str = "developer") -> dict[str, Any] | None:
    """Approve a pending request: move to users, remove from pending."""
    if _db_available and _pool:
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    return executor.submit(
                        _sync_run, _approve_pending_db(github_login, role)
                    ).result(timeout=5)
            return loop.run_until_complete(_approve_pending_db(github_login, role))
        except Exception:
            pass
    return _approve_pending_json(github_login, role)


async def _approve_pending_db(github_login: str, role: str) -> dict[str, Any] | None:
    key = github_login.lower()
    async with _acquire() as conn:
        row = await conn.fetchrow(
            "DELETE FROM dashboard_pending WHERE github_login = $1 RETURNING *", key
        )
        email = row["email"] if row else ""
        name = row["name"] if row else ""
    return await _approve_user_db(github_login, role, email, name)


def _approve_pending_json(github_login: str, role: str) -> dict[str, Any] | None:
    pending = _load_pending_json()
    key = github_login.lower()
    info = pending.pop(key, None)
    _save_pending_json(pending)
    if info:
        return _approve_user_json(
            github_login,
            role=role,
            email=info.get("email", ""),
            name=info.get("name", ""),
        )
    return _approve_user_json(github_login, role=role, email="", name="")


def reject_pending(github_login: str) -> bool:
    """Reject and remove a pending request."""
    if _db_available and _pool:
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    return executor.submit(_sync_run, _reject_pending_db(github_login)).result(
                        timeout=5
                    )
            return loop.run_until_complete(_reject_pending_db(github_login))
        except Exception:
            pass
    pending = _load_pending_json()
    key = github_login.lower()
    if key not in pending:
        return False
    del pending[key]
    _save_pending_json(pending)
    return True


async def _reject_pending_db(github_login: str) -> bool:
    key = github_login.lower()
    async with _acquire() as conn:
        result = await conn.execute("DELETE FROM dashboard_pending WHERE github_login = $1", key)
        return result != "DELETE 0"
