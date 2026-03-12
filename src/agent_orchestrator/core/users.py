"""User management — multi-user support with role-based access."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    import bcrypt

    HAS_BCRYPT = True
except ImportError:
    HAS_BCRYPT = False


class UserRole(str, Enum):
    ADMIN = "admin"
    DEVELOPER = "developer"
    VIEWER = "viewer"


# Permission matrix: role -> set of allowed actions
ROLE_PERMISSIONS: dict[UserRole, set[str]] = {
    UserRole.ADMIN: {
        "config.read",
        "config.write",
        "agents.read",
        "agents.write",
        "agents.execute",
        "projects.read",
        "projects.write",
        "users.read",
        "users.write",
        "dashboard.read",
        "audit.read",
    },
    UserRole.DEVELOPER: {
        "config.read",
        "agents.read",
        "agents.write",
        "agents.execute",
        "projects.read",
        "users.read",
        "dashboard.read",
        "audit.read",
    },
    UserRole.VIEWER: {
        "config.read",
        "agents.read",
        "projects.read",
        "dashboard.read",
    },
}


@dataclass
class User:
    """A user of the orchestrator."""

    user_id: str
    username: str
    role: UserRole
    api_key: str = ""
    created_at: float = 0.0
    active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    _password_hash: str = ""


class UserManager:
    """Manage users and role-based access control.

    Passwords are hashed with SHA-256 + salt. API keys are generated
    as random hex tokens for programmatic access.
    """

    def __init__(self) -> None:
        self._users: dict[str, User] = {}
        self._api_key_index: dict[str, str] = {}  # api_key -> user_id

    def create_user(
        self,
        user_id: str,
        username: str,
        password: str,
        role: UserRole = UserRole.DEVELOPER,
    ) -> User:
        """Create a new user with a hashed password and auto-generated API key."""
        if user_id in self._users:
            raise ValueError(f"User '{user_id}' already exists")
        # Check username uniqueness
        for u in self._users.values():
            if u.username == username:
                raise ValueError(f"Username '{username}' already taken")

        api_key = secrets.token_hex(32)
        user = User(
            user_id=user_id,
            username=username,
            role=role,
            api_key=api_key,
            created_at=time.time(),
        )
        user._password_hash = _hash_password(password)
        self._users[user_id] = user
        self._api_key_index[api_key] = user_id
        return user

    def get_user(self, user_id: str) -> User | None:
        """Get a user by ID."""
        return self._users.get(user_id)

    def get_by_username(self, username: str) -> User | None:
        """Get a user by username."""
        for u in self._users.values():
            if u.username == username:
                return u
        return None

    def get_by_api_key(self, api_key: str) -> User | None:
        """Look up a user by API key."""
        user_id = self._api_key_index.get(api_key)
        if user_id is None:
            return None
        return self._users.get(user_id)

    def authenticate(self, username: str, password: str) -> User | None:
        """Authenticate by username+password. Returns user or None."""
        user = self.get_by_username(username)
        if user is None or not user.active:
            return None
        if not _verify_password(password, user._password_hash):
            return None
        return user

    def list_users(self, active_only: bool = False) -> list[User]:
        """List all users."""
        users = list(self._users.values())
        if active_only:
            users = [u for u in users if u.active]
        return users

    def update_role(self, user_id: str, role: UserRole) -> bool:
        """Change a user's role. Returns True if user found."""
        user = self._users.get(user_id)
        if user is None:
            return False
        user.role = role
        return True

    def deactivate(self, user_id: str) -> bool:
        """Deactivate a user. Returns True if found."""
        user = self._users.get(user_id)
        if user is None:
            return False
        user.active = False
        return True

    def activate(self, user_id: str) -> bool:
        """Re-activate a user. Returns True if found."""
        user = self._users.get(user_id)
        if user is None:
            return False
        user.active = True
        return True

    def regenerate_api_key(self, user_id: str) -> str | None:
        """Generate a new API key for a user. Returns the new key or None."""
        user = self._users.get(user_id)
        if user is None:
            return None
        # Remove old key from index
        if user.api_key in self._api_key_index:
            del self._api_key_index[user.api_key]
        new_key = secrets.token_hex(32)
        user.api_key = new_key
        self._api_key_index[new_key] = user_id
        return new_key

    def delete_user(self, user_id: str) -> bool:
        """Permanently delete a user. Returns True if found."""
        user = self._users.get(user_id)
        if user is None:
            return False
        if user.api_key in self._api_key_index:
            del self._api_key_index[user.api_key]
        del self._users[user_id]
        return True

    def has_permission(self, user_id: str, permission: str) -> bool:
        """Check if a user has a specific permission."""
        user = self._users.get(user_id)
        if user is None or not user.active:
            return False
        allowed = ROLE_PERMISSIONS.get(user.role, set())
        return permission in allowed

    def check_permission(self, user_id: str, permission: str) -> None:
        """Raise PermissionError if user lacks the permission."""
        if not self.has_permission(user_id, permission):
            raise PermissionError(f"User '{user_id}' lacks permission '{permission}'")


def _hash_password(password: str) -> str:
    """Hash a password using bcrypt (cost=12).

    Falls back to SHA-256 with per-hash random salt only if bcrypt is not installed.
    Install bcrypt for production: pip install bcrypt
    """
    if HAS_BCRYPT:
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
    # Fallback: PBKDF2-SHA256 with random salt (use bcrypt for production)
    salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), iterations=100_000
    ).hex()
    return f"pbkdf2${salt}${hashed}"


def _verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a stored hash (bcrypt or SHA-256 fallback)."""
    if hashed.startswith("$2b$") or hashed.startswith("$2a$"):
        if not HAS_BCRYPT:
            return False
        return bcrypt.checkpw(password.encode(), hashed.encode())
    if hashed.startswith("pbkdf2$"):
        _, salt, expected = hashed.split("$", 2)
        computed = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt.encode(), iterations=100_000
        ).hex()
        return hmac.compare_digest(computed, expected)
    # Legacy: SHA-256 with random salt (pre-PBKDF2 migration)
    if hashed.startswith("sha256$"):
        _, salt, expected = hashed.split("$", 2)
        computed = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
        return hmac.compare_digest(computed, expected)
    # Legacy: fixed-salt PBKDF2-SHA256 (migration path)
    legacy = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), b"agent-orchestrator", iterations=100_000
    ).hex()
    return hmac.compare_digest(hashed, legacy)
