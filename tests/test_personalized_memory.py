"""Tests for the Personalized Memory feature (P4).

Covers:
- PersonalizedMemory put / get / list / delete / wipe round trips
- MemoryFilter integration
- ProfileExtractorSkill: happy path and provider failure
- Agent system-prompt injection (with and without memory)
- HTTP endpoints via ASGITransport + httpx
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_orchestrator.core.memory_filter import MemoryFilter
from agent_orchestrator.core.personalized_memory import PersonalizedMemory
from agent_orchestrator.core.store import InMemoryStore


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def store():
    return InMemoryStore()


@pytest.fixture
def pm(store):
    return PersonalizedMemory(store)


@pytest.fixture
def pm_with_filter(store):
    mf = MemoryFilter()
    return PersonalizedMemory(store, memory_filter=mf)


# ─── PersonalizedMemory: put / get ───────────────────────────────────────────


class TestPutGet:
    @pytest.mark.asyncio
    async def test_put_and_get_returns_value(self, pm):
        await pm.put("alice", "profile", {"preferences": ["dark-mode"]})
        result = await pm.get("alice", "profile")
        assert result == {"preferences": ["dark-mode"]}

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, pm):
        result = await pm.get("bob", "profile")
        assert result is None

    @pytest.mark.asyncio
    async def test_put_overwrites_existing(self, pm):
        await pm.put("alice", "profile", {"preferences": ["dark-mode"]})
        await pm.put("alice", "profile", {"preferences": ["light-mode"]})
        result = await pm.get("alice", "profile")
        assert result == {"preferences": ["light-mode"]}

    @pytest.mark.asyncio
    async def test_namespace_isolation_between_users(self, pm):
        await pm.put("alice", "profile", {"name": "Alice"})
        await pm.put("bob", "profile", {"name": "Bob"})
        assert (await pm.get("alice", "profile")) == {"name": "Alice"}
        assert (await pm.get("bob", "profile")) == {"name": "Bob"}

    @pytest.mark.asyncio
    async def test_namespace_isolation_different_keys(self, pm):
        await pm.put("alice", "profile", {"name": "Alice"})
        await pm.put("alice", "settings", {"theme": "dark"})
        profile = await pm.get("alice", "profile")
        settings = await pm.get("alice", "settings")
        assert profile == {"name": "Alice"}
        assert settings == {"theme": "dark"}


# ─── PersonalizedMemory: list ─────────────────────────────────────────────────


class TestList:
    @pytest.mark.asyncio
    async def test_list_empty_user_returns_empty(self, pm):
        entries = await pm.list("nobody")
        assert entries == []

    @pytest.mark.asyncio
    async def test_list_returns_all_entries(self, pm):
        await pm.put("alice", "profile", {"x": 1})
        await pm.put("alice", "settings", {"y": 2})
        entries = await pm.list("alice")
        keys = {e["key"] for e in entries}
        assert keys == {"profile", "settings"}

    @pytest.mark.asyncio
    async def test_list_respects_limit(self, pm):
        for i in range(10):
            await pm.put("alice", f"key-{i}", {"i": i})
        entries = await pm.list("alice", limit=3)
        assert len(entries) == 3

    @pytest.mark.asyncio
    async def test_list_entries_have_metadata(self, pm):
        await pm.put("alice", "profile", {"x": 1})
        entries = await pm.list("alice")
        assert len(entries) == 1
        e = entries[0]
        assert "key" in e
        assert "value" in e
        assert "created_at" in e
        assert "updated_at" in e

    @pytest.mark.asyncio
    async def test_list_does_not_bleed_between_users(self, pm):
        await pm.put("alice", "profile", {"a": 1})
        await pm.put("bob", "profile", {"b": 2})
        alice_entries = await pm.list("alice")
        assert all(e["value"] == {"a": 1} for e in alice_entries)


# ─── PersonalizedMemory: delete / wipe ────────────────────────────────────────


class TestDeleteWipe:
    @pytest.mark.asyncio
    async def test_delete_existing_returns_true(self, pm):
        await pm.put("alice", "profile", {"x": 1})
        result = await pm.delete("alice", "profile")
        assert result is True
        assert await pm.get("alice", "profile") is None

    @pytest.mark.asyncio
    async def test_delete_missing_returns_false(self, pm):
        result = await pm.delete("alice", "ghost")
        assert result is False

    @pytest.mark.asyncio
    async def test_wipe_removes_all_entries(self, pm):
        await pm.put("alice", "a", {"x": 1})
        await pm.put("alice", "b", {"y": 2})
        count = await pm.wipe("alice")
        assert count == 2
        assert await pm.list("alice") == []

    @pytest.mark.asyncio
    async def test_wipe_returns_zero_for_empty_user(self, pm):
        count = await pm.wipe("nobody")
        assert count == 0

    @pytest.mark.asyncio
    async def test_wipe_does_not_affect_other_users(self, pm):
        await pm.put("alice", "profile", {"a": 1})
        await pm.put("bob", "profile", {"b": 2})
        await pm.wipe("alice")
        assert await pm.get("bob", "profile") == {"b": 2}


# ─── MemoryFilter integration ─────────────────────────────────────────────────


class TestMemoryFilterIntegration:
    @pytest.mark.asyncio
    async def test_string_fields_are_filtered(self, pm_with_filter):
        # jobs/... matches SESSION_FILE_PATTERNS and gets replaced
        await pm_with_filter.put(
            "alice",
            "notes",
            {"note": "See jobs/job_abc123def456/output.txt for results"},
        )
        result = await pm_with_filter.get("alice", "notes")
        assert result is not None
        assert "jobs/job_abc123def456" not in result["note"]
        assert "[session-file]" in result["note"]

    @pytest.mark.asyncio
    async def test_list_values_are_filtered(self, pm_with_filter):
        # String items in list fields should also be filtered
        await pm_with_filter.put(
            "alice",
            "refs",
            {"paths": ["/tmp/abc123def456-data.bin", "normal_value"]},
        )
        result = await pm_with_filter.get("alice", "refs")
        assert result is not None
        paths = result["paths"]
        assert "[session-file]" in paths[0]
        assert paths[1] == "normal_value"

    @pytest.mark.asyncio
    async def test_non_string_fields_are_not_touched(self, pm_with_filter):
        await pm_with_filter.put("alice", "meta", {"count": 42, "active": True})
        result = await pm_with_filter.get("alice", "meta")
        assert result == {"count": 42, "active": True}


# ─── ProfileExtractorSkill ────────────────────────────────────────────────────


def _make_mock_provider(json_response: str) -> Any:
    """Return a mock Provider whose complete() returns json_response."""
    completion = MagicMock()
    completion.content = json_response
    provider = MagicMock()
    provider.complete = AsyncMock(return_value=completion)
    return provider


class TestProfileExtractorSkill:
    def _skill(self, provider, memory):
        from agent_orchestrator.skills.profile_extractor_skill import ProfileExtractorSkill

        return ProfileExtractorSkill(provider=provider, personalized_memory=memory)

    @pytest.mark.asyncio
    async def test_happy_path_persists_profile(self, store):
        pm = PersonalizedMemory(store)
        profile_json = json.dumps(
            {
                "preferences": ["dark-mode", "concise answers"],
                "style_notes": ["prefers bullet points"],
                "recurring_topics": ["Python", "AI"],
            }
        )
        skill = self._skill(_make_mock_provider(profile_json), pm)
        result = await skill.execute(
            {
                "user_id": "alice",
                "recent_messages": [
                    {"role": "user", "content": "I like dark-mode"},
                    {"role": "user", "content": "Talk about Python AI please"},
                ],
            }
        )
        assert result.success is True
        saved = await pm.get("alice", "profile")
        assert saved is not None
        assert "dark-mode" in saved["preferences"]

    @pytest.mark.asyncio
    async def test_result_metadata_has_saved_keys(self, store):
        pm = PersonalizedMemory(store)
        profile_json = json.dumps({"preferences": [], "style_notes": [], "recurring_topics": []})
        skill = self._skill(_make_mock_provider(profile_json), pm)
        result = await skill.execute(
            {"user_id": "bob", "recent_messages": [{"role": "user", "content": "hi"}]}
        )
        assert result.success is True
        assert result.metadata["saved_keys"] == ["profile"]

    @pytest.mark.asyncio
    async def test_provider_failure_returns_graceful_error(self, store):
        pm = PersonalizedMemory(store)
        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=RuntimeError("network error"))
        skill = self._skill(provider, pm)
        result = await skill.execute(
            {"user_id": "charlie", "recent_messages": [{"role": "user", "content": "hi"}]}
        )
        assert result.success is False
        assert "Provider call failed" in (result.error or "")

    @pytest.mark.asyncio
    async def test_invalid_json_response_returns_graceful_error(self, store):
        pm = PersonalizedMemory(store)
        skill = self._skill(_make_mock_provider("not json at all {{"), pm)
        result = await skill.execute(
            {"user_id": "dave", "recent_messages": [{"role": "user", "content": "hi"}]}
        )
        assert result.success is False
        assert "parse" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_missing_user_id_returns_error(self, store):
        pm = PersonalizedMemory(store)
        skill = self._skill(_make_mock_provider("{}"), pm)
        result = await skill.execute({"recent_messages": []})
        assert result.success is False
        assert "user_id" in (result.error or "")

    @pytest.mark.asyncio
    async def test_markdown_fenced_json_is_parsed_correctly(self, store):
        pm = PersonalizedMemory(store)
        fenced = (
            "```json\n"
            + json.dumps({"preferences": ["p1"], "style_notes": [], "recurring_topics": []})
            + "\n```"
        )
        skill = self._skill(_make_mock_provider(fenced), pm)
        result = await skill.execute(
            {"user_id": "eve", "recent_messages": [{"role": "user", "content": "hi"}]}
        )
        assert result.success is True
        saved = await pm.get("eve", "profile")
        assert saved is not None and saved["preferences"] == ["p1"]


# ─── Agent system-prompt injection ────────────────────────────────────────────


class TestAgentUserProfileInjection:
    def _make_agent(self, pm=None, user_id=None):
        from agent_orchestrator.core.agent import Agent, AgentConfig
        from agent_orchestrator.core.skill import SkillRegistry

        config = AgentConfig(
            name="test-agent", role="You are a helpful assistant.", provider_key="mock"
        )
        provider = MagicMock()
        registry = SkillRegistry()
        return Agent(
            config=config,
            provider=provider,
            skill_registry=registry,
            personalized_memory=pm,
            user_id=user_id,
        )

    def test_no_memory_no_profile_block(self):
        agent = self._make_agent()
        prompt = agent.build_system_prompt()
        assert "<user_profile>" not in prompt

    def test_memory_but_no_user_id_no_profile_block(self, store):
        pm = PersonalizedMemory(store)
        agent = self._make_agent(pm=pm, user_id=None)
        prompt = agent.build_system_prompt()
        assert "<user_profile>" not in prompt

    @pytest.mark.asyncio
    async def test_memory_and_user_id_profile_block_appears(self, store):
        pm = PersonalizedMemory(store)
        await pm.put("alice", "profile", {"preferences": ["verbose"]})
        agent = self._make_agent(pm=pm, user_id="alice")
        await agent.prefetch_user_profile()
        prompt = agent.build_system_prompt()
        assert "<user_profile>" in prompt
        assert "verbose" in prompt

    @pytest.mark.asyncio
    async def test_profile_block_absent_before_prefetch(self, store):
        pm = PersonalizedMemory(store)
        await pm.put("alice", "profile", {"preferences": ["verbose"]})
        agent = self._make_agent(pm=pm, user_id="alice")
        # No prefetch called — cache is empty
        prompt = agent.build_system_prompt()
        assert "<user_profile>" not in prompt

    @pytest.mark.asyncio
    async def test_profile_block_respects_top_n(self, store):
        pm = PersonalizedMemory(store)
        for i in range(10):
            await pm.put("alice", f"key-{i}", {"i": i})
        agent = self._make_agent(pm=pm, user_id="alice")
        await agent.prefetch_user_profile(top_n=3)
        # The cache holds top 3; build_system_prompt slices to top_n=5 (default)
        # so all 3 cached entries appear
        prompt = agent.build_system_prompt()
        assert "<user_profile>" in prompt


# ─── HTTP endpoints ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestMemoryHTTPEndpoints:
    async def _client(self):
        import os

        os.environ.setdefault("ALLOW_DEV_MODE", "true")
        from httpx import ASGITransport, AsyncClient
        from agent_orchestrator.dashboard.app import create_dashboard_app

        app = create_dashboard_app()
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async def test_list_empty_user(self):
        async with await self._client() as client:
            resp = await client.get("/api/user-memory/users/nobody")
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_id"] == "nobody"
        assert body["entries"] == []

    async def test_put_and_list(self):
        import os

        os.environ.setdefault("ALLOW_DEV_MODE", "true")
        from httpx import ASGITransport, AsyncClient
        from agent_orchestrator.dashboard.app import create_dashboard_app

        app = create_dashboard_app()
        pm = app.state.personalized_memory
        await pm.put("user1", "profile", {"preferences": ["dark"]})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/user-memory/users/user1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["entries"][0]["key"] == "profile"

    async def test_get_single_entry(self):
        import os

        os.environ.setdefault("ALLOW_DEV_MODE", "true")
        from httpx import ASGITransport, AsyncClient
        from agent_orchestrator.dashboard.app import create_dashboard_app

        app = create_dashboard_app()
        pm = app.state.personalized_memory
        await pm.put("user2", "settings", {"theme": "dark"})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/user-memory/users/user2/settings")
        assert resp.status_code == 200
        body = resp.json()
        assert body["key"] == "settings"
        assert body["value"]["theme"] == "dark"

    async def test_get_missing_entry_returns_404(self):
        import os

        os.environ.setdefault("ALLOW_DEV_MODE", "true")
        from httpx import ASGITransport, AsyncClient
        from agent_orchestrator.dashboard.app import create_dashboard_app

        app = create_dashboard_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/user-memory/users/user3/ghost")
        assert resp.status_code == 404

    async def test_delete_existing_entry(self):
        import os

        os.environ.setdefault("ALLOW_DEV_MODE", "true")
        from httpx import ASGITransport, AsyncClient
        from agent_orchestrator.dashboard.app import create_dashboard_app

        app = create_dashboard_app()
        pm = app.state.personalized_memory
        await pm.put("user4", "token", {"val": "abc"})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete("/api/user-memory/users/user4/token")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert await pm.get("user4", "token") is None

    async def test_delete_missing_entry_returns_404(self):
        import os

        os.environ.setdefault("ALLOW_DEV_MODE", "true")
        from httpx import ASGITransport, AsyncClient
        from agent_orchestrator.dashboard.app import create_dashboard_app

        app = create_dashboard_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete("/api/user-memory/users/user5/ghost")
        assert resp.status_code == 404

    async def test_wipe_all_entries(self):
        import os

        os.environ.setdefault("ALLOW_DEV_MODE", "true")
        from httpx import ASGITransport, AsyncClient
        from agent_orchestrator.dashboard.app import create_dashboard_app

        app = create_dashboard_app()
        pm = app.state.personalized_memory
        await pm.put("user6", "a", {"x": 1})
        await pm.put("user6", "b", {"y": 2})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete("/api/user-memory/users/user6")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["removed"] == 2
        assert await pm.list("user6") == []
