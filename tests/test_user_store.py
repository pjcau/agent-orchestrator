"""Tests for dashboard user_store — admin-controlled access via GITHUB_USERNAME."""

from pathlib import Path
from unittest.mock import patch

from agent_orchestrator.dashboard.user_store import (
    approve_pending,
    approve_user,
    deactivate_user,
    delete_user,
    get_or_create_user,
    list_pending,
    list_users,
    reject_pending,
    update_user_role,
)


def _patch_files(tmp_path: Path):
    """Patch both USERS_FILE and PENDING_FILE to use tmp_path."""
    return (
        patch("agent_orchestrator.dashboard.user_store.USERS_FILE", tmp_path / "users.json"),
        patch("agent_orchestrator.dashboard.user_store.PENDING_FILE", tmp_path / "pending.json"),
    )


class TestGetOrCreateUser:
    def test_admin_auto_created(self, tmp_path: Path):
        p1, p2 = _patch_files(tmp_path)
        with (
            p1,
            p2,
            patch(
                "agent_orchestrator.dashboard.user_store._get_admin_github", return_value="pjcau"
            ),
        ):
            user = get_or_create_user("pjcau", "admin@test.com", "Admin")
        assert user is not None
        assert user["role"] == "admin"
        assert user["active"] is True
        assert (tmp_path / "users.json").exists()

    def test_admin_case_insensitive(self, tmp_path: Path):
        p1, p2 = _patch_files(tmp_path)
        with (
            p1,
            p2,
            patch(
                "agent_orchestrator.dashboard.user_store._get_admin_github", return_value="pjcau"
            ),
        ):
            user = get_or_create_user("PjCau", "admin@test.com", "Admin")
        assert user is not None
        assert user["role"] == "admin"

    def test_unknown_user_denied_and_pending(self, tmp_path: Path):
        p1, p2 = _patch_files(tmp_path)
        with (
            p1,
            p2,
            patch(
                "agent_orchestrator.dashboard.user_store._get_admin_github", return_value="pjcau"
            ),
        ):
            user = get_or_create_user("stranger", "stranger@test.com", "Stranger")
            pending = list_pending()
        assert user is None
        assert len(pending) == 1
        assert pending[0]["github_login"] == "stranger"
        assert pending[0]["email"] == "stranger@test.com"

    def test_approved_user_allowed(self, tmp_path: Path):
        p1, p2 = _patch_files(tmp_path)
        with (
            p1,
            p2,
            patch(
                "agent_orchestrator.dashboard.user_store._get_admin_github", return_value="pjcau"
            ),
        ):
            approve_user("bob", role="developer")
            user = get_or_create_user("bob", "bob@test.com", "Bob")
        assert user is not None
        assert user["role"] == "developer"

    def test_deactivated_user_denied(self, tmp_path: Path):
        p1, p2 = _patch_files(tmp_path)
        with (
            p1,
            p2,
            patch(
                "agent_orchestrator.dashboard.user_store._get_admin_github", return_value="pjcau"
            ),
        ):
            approve_user("bob", role="developer")
            deactivate_user("bob")
            user = get_or_create_user("bob", "bob@test.com", "Bob")
        assert user is None

    def test_updates_name_email_on_login(self, tmp_path: Path):
        p1, p2 = _patch_files(tmp_path)
        with (
            p1,
            p2,
            patch(
                "agent_orchestrator.dashboard.user_store._get_admin_github", return_value="pjcau"
            ),
        ):
            get_or_create_user("pjcau", "old@test.com", "Old Name")
            user = get_or_create_user("pjcau", "new@test.com", "New Name")
        assert user["email"] == "new@test.com"
        assert user["name"] == "New Name"


class TestAdminActions:
    def test_approve_and_list(self, tmp_path: Path):
        p1, p2 = _patch_files(tmp_path)
        with p1, p2:
            approve_user("alice", role="viewer", name="Alice")
            approve_user("bob", role="developer", name="Bob")
            users = list_users()
        assert len(users) == 2
        names = {u["name"] for u in users}
        assert names == {"Alice", "Bob"}

    def test_update_role(self, tmp_path: Path):
        p1, p2 = _patch_files(tmp_path)
        with p1, p2:
            approve_user("alice", role="viewer")
            ok = update_user_role("alice", "developer")
            assert ok is True
            users = list_users()
        assert users[0]["role"] == "developer"

    def test_update_role_unknown_user(self, tmp_path: Path):
        p1, p2 = _patch_files(tmp_path)
        with p1, p2:
            ok = update_user_role("ghost", "admin")
        assert ok is False

    def test_delete_user(self, tmp_path: Path):
        p1, p2 = _patch_files(tmp_path)
        with p1, p2:
            approve_user("alice", role="viewer")
            ok = delete_user("alice")
            assert ok is True
            users = list_users()
        assert len(users) == 0

    def test_delete_unknown(self, tmp_path: Path):
        p1, p2 = _patch_files(tmp_path)
        with p1, p2:
            ok = delete_user("ghost")
        assert ok is False


class TestPendingRequests:
    def test_denied_login_creates_pending(self, tmp_path: Path):
        p1, p2 = _patch_files(tmp_path)
        with (
            p1,
            p2,
            patch(
                "agent_orchestrator.dashboard.user_store._get_admin_github", return_value="pjcau"
            ),
        ):
            get_or_create_user("newguy", "new@test.com", "New Guy")
            pending = list_pending()
        assert len(pending) == 1
        assert pending[0]["github_login"] == "newguy"

    def test_repeat_login_updates_pending(self, tmp_path: Path):
        p1, p2 = _patch_files(tmp_path)
        with (
            p1,
            p2,
            patch(
                "agent_orchestrator.dashboard.user_store._get_admin_github", return_value="pjcau"
            ),
        ):
            get_or_create_user("newguy", "old@test.com", "Old Name")
            get_or_create_user("newguy", "new@test.com", "New Name")
            pending = list_pending()
        assert len(pending) == 1
        assert pending[0]["email"] == "new@test.com"
        assert pending[0]["name"] == "New Name"

    def test_approve_pending(self, tmp_path: Path):
        p1, p2 = _patch_files(tmp_path)
        with (
            p1,
            p2,
            patch(
                "agent_orchestrator.dashboard.user_store._get_admin_github", return_value="pjcau"
            ),
        ):
            get_or_create_user("newguy", "new@test.com", "New Guy")
            user = approve_pending("newguy", role="developer")
            pending = list_pending()
            users = list_users()
        assert user is not None
        assert user["role"] == "developer"
        assert len(pending) == 0
        assert len(users) == 1

    def test_reject_pending(self, tmp_path: Path):
        p1, p2 = _patch_files(tmp_path)
        with (
            p1,
            p2,
            patch(
                "agent_orchestrator.dashboard.user_store._get_admin_github", return_value="pjcau"
            ),
        ):
            get_or_create_user("newguy", "new@test.com", "New Guy")
            ok = reject_pending("newguy")
            pending = list_pending()
        assert ok is True
        assert len(pending) == 0

    def test_reject_unknown_returns_false(self, tmp_path: Path):
        p1, p2 = _patch_files(tmp_path)
        with p1, p2:
            ok = reject_pending("ghost")
        assert ok is False

    def test_approved_user_not_in_pending(self, tmp_path: Path):
        """After approval, a new login should NOT create a pending request."""
        p1, p2 = _patch_files(tmp_path)
        with (
            p1,
            p2,
            patch(
                "agent_orchestrator.dashboard.user_store._get_admin_github", return_value="pjcau"
            ),
        ):
            get_or_create_user("newguy", "new@test.com", "New Guy")
            approve_pending("newguy", role="developer")
            # Login again — should succeed, no new pending
            user = get_or_create_user("newguy", "new@test.com", "New Guy")
            pending = list_pending()
        assert user is not None
        assert len(pending) == 0
