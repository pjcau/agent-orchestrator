"""Tests for dashboard user_store — admin-controlled access via GITHUB_USERNAME."""

from pathlib import Path
from unittest.mock import patch

from agent_orchestrator.dashboard.user_store import (
    approve_user,
    deactivate_user,
    delete_user,
    get_or_create_user,
    list_users,
    update_user_role,
)


class TestGetOrCreateUser:
    def test_admin_auto_created(self, tmp_path: Path):
        users_file = tmp_path / "users.json"
        with (
            patch("agent_orchestrator.dashboard.user_store.USERS_FILE", users_file),
            patch(
                "agent_orchestrator.dashboard.user_store._get_admin_github", return_value="pjcau"
            ),
        ):
            user = get_or_create_user("pjcau", "admin@test.com", "Admin")
        assert user is not None
        assert user["role"] == "admin"
        assert user["active"] is True
        assert users_file.exists()

    def test_admin_case_insensitive(self, tmp_path: Path):
        users_file = tmp_path / "users.json"
        with (
            patch("agent_orchestrator.dashboard.user_store.USERS_FILE", users_file),
            patch(
                "agent_orchestrator.dashboard.user_store._get_admin_github", return_value="pjcau"
            ),
        ):
            user = get_or_create_user("PjCau", "admin@test.com", "Admin")
        assert user is not None
        assert user["role"] == "admin"

    def test_unknown_user_denied(self, tmp_path: Path):
        users_file = tmp_path / "users.json"
        with (
            patch("agent_orchestrator.dashboard.user_store.USERS_FILE", users_file),
            patch(
                "agent_orchestrator.dashboard.user_store._get_admin_github", return_value="pjcau"
            ),
        ):
            user = get_or_create_user("stranger", "stranger@test.com", "Stranger")
        assert user is None

    def test_approved_user_allowed(self, tmp_path: Path):
        users_file = tmp_path / "users.json"
        with (
            patch("agent_orchestrator.dashboard.user_store.USERS_FILE", users_file),
            patch(
                "agent_orchestrator.dashboard.user_store._get_admin_github", return_value="pjcau"
            ),
        ):
            approve_user("bob", role="developer")
            user = get_or_create_user("bob", "bob@test.com", "Bob")
        assert user is not None
        assert user["role"] == "developer"

    def test_deactivated_user_denied(self, tmp_path: Path):
        users_file = tmp_path / "users.json"
        with (
            patch("agent_orchestrator.dashboard.user_store.USERS_FILE", users_file),
            patch(
                "agent_orchestrator.dashboard.user_store._get_admin_github", return_value="pjcau"
            ),
        ):
            approve_user("bob", role="developer")
            deactivate_user("bob")
            user = get_or_create_user("bob", "bob@test.com", "Bob")
        assert user is None

    def test_updates_name_email_on_login(self, tmp_path: Path):
        users_file = tmp_path / "users.json"
        with (
            patch("agent_orchestrator.dashboard.user_store.USERS_FILE", users_file),
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
        users_file = tmp_path / "users.json"
        with patch("agent_orchestrator.dashboard.user_store.USERS_FILE", users_file):
            approve_user("alice", role="viewer", name="Alice")
            approve_user("bob", role="developer", name="Bob")
            users = list_users()
        assert len(users) == 2
        names = {u["name"] for u in users}
        assert names == {"Alice", "Bob"}

    def test_update_role(self, tmp_path: Path):
        users_file = tmp_path / "users.json"
        with patch("agent_orchestrator.dashboard.user_store.USERS_FILE", users_file):
            approve_user("alice", role="viewer")
            ok = update_user_role("alice", "developer")
            assert ok is True
            users = list_users()
        assert users[0]["role"] == "developer"

    def test_update_role_unknown_user(self, tmp_path: Path):
        users_file = tmp_path / "users.json"
        with patch("agent_orchestrator.dashboard.user_store.USERS_FILE", users_file):
            ok = update_user_role("ghost", "admin")
        assert ok is False

    def test_delete_user(self, tmp_path: Path):
        users_file = tmp_path / "users.json"
        with patch("agent_orchestrator.dashboard.user_store.USERS_FILE", users_file):
            approve_user("alice", role="viewer")
            ok = delete_user("alice")
            assert ok is True
            users = list_users()
        assert len(users) == 0

    def test_delete_unknown(self, tmp_path: Path):
        users_file = tmp_path / "users.json"
        with patch("agent_orchestrator.dashboard.user_store.USERS_FILE", users_file):
            ok = delete_user("ghost")
        assert ok is False
