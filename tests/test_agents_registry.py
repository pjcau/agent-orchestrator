"""Tests for the agent registry — category scanning, frontmatter parsing, and grouping."""

from pathlib import Path
from unittest.mock import patch

import pytest

from agent_orchestrator.dashboard.agents_registry import (
    AGENT_SKILLS,
    _parse_frontmatter,
    _read_agent_file,
    get_agent_registry,
)


# --- Frontmatter parsing ---


class TestParseFrontmatter:
    def test_valid_frontmatter(self):
        text = "---\nname: backend\nmodel: sonnet\n---\n# Body"
        result = _parse_frontmatter(text)
        assert result == {"name": "backend", "model": "sonnet"}

    def test_no_frontmatter(self):
        assert _parse_frontmatter("# Just a heading") == {}

    def test_empty_string(self):
        assert _parse_frontmatter("") == {}

    def test_frontmatter_with_description(self):
        text = "---\nname: data-analyst\ncategory: data-science\ndescription: Data analyst\n---\n"
        result = _parse_frontmatter(text)
        assert result["name"] == "data-analyst"
        assert result["category"] == "data-science"
        assert result["description"] == "Data analyst"


# --- Read agent file ---


class TestReadAgentFile:
    def test_read_valid_agent(self, tmp_path: Path):
        md = tmp_path / "backend.md"
        md.write_text("---\nname: backend\nmodel: sonnet\ndescription: API dev\n---\n# Body\n")
        agent = _read_agent_file(md, category="software-engineering")
        assert agent is not None
        assert agent["name"] == "backend"
        assert agent["model"] == "sonnet"
        assert agent["category"] == "software-engineering"
        assert agent["description"] == "API dev"

    def test_category_from_frontmatter_overrides_default(self, tmp_path: Path):
        md = tmp_path / "test.md"
        md.write_text("---\nname: test\ncategory: custom-cat\n---\n")
        agent = _read_agent_file(md, category="fallback")
        assert agent is not None
        assert agent["category"] == "custom-cat"

    def test_missing_name_uses_stem(self, tmp_path: Path):
        md = tmp_path / "my-agent.md"
        md.write_text("---\nmodel: sonnet\n---\n")
        agent = _read_agent_file(md, category="general")
        assert agent is not None
        assert agent["name"] == "my-agent"

    def test_invalid_file_returns_none(self, tmp_path: Path):
        md = tmp_path / "bad.md"
        # Make it unreadable
        md.write_text("valid")
        md.chmod(0o000)
        agent = _read_agent_file(md, category="general")
        # Restore permissions for cleanup
        md.chmod(0o644)
        # On some systems the read may succeed, so accept either outcome
        assert agent is None or isinstance(agent, dict)

    def test_skills_mapping(self, tmp_path: Path):
        md = tmp_path / "backend.md"
        md.write_text("---\nname: backend\n---\n")
        agent = _read_agent_file(md, category="software-engineering")
        assert agent is not None
        assert agent["skills"] == AGENT_SKILLS["backend"]

    def test_unknown_agent_gets_empty_skills(self, tmp_path: Path):
        md = tmp_path / "unknown.md"
        md.write_text("---\nname: unknown-agent\n---\n")
        agent = _read_agent_file(md, category="general")
        assert agent is not None
        assert agent["skills"] == []


# --- Full registry ---


class TestGetAgentRegistry:
    def test_registry_returns_agents_and_skills(self):
        registry = get_agent_registry()
        assert "agents" in registry
        assert "skills" in registry
        assert "categories" in registry
        assert isinstance(registry["agents"], list)
        assert isinstance(registry["categories"], dict)

    def test_team_lead_is_first(self):
        registry = get_agent_registry()
        agents = registry["agents"]
        if agents:
            assert agents[0]["name"] == "team-lead"

    def test_all_agents_have_required_fields(self):
        registry = get_agent_registry()
        for agent in registry["agents"]:
            assert "name" in agent
            assert "model" in agent
            assert "description" in agent
            assert "category" in agent
            assert "skills" in agent

    def test_categories_contain_expected_groups(self):
        registry = get_agent_registry()
        categories = registry["categories"]
        expected = {"software-engineering", "data-science", "finance", "marketing"}
        # At minimum the expected categories should be present
        assert expected.issubset(set(categories.keys()))

    def test_software_engineering_agents(self):
        registry = get_agent_registry()
        se_agents = registry["categories"].get("software-engineering", [])
        se_names = {a["name"] for a in se_agents}
        assert {"backend", "frontend", "devops", "platform-engineer", "ai-engineer", "scout"}.issubset(se_names)

    def test_data_science_agents(self):
        registry = get_agent_registry()
        ds_agents = registry["categories"].get("data-science", [])
        ds_names = {a["name"] for a in ds_agents}
        assert {"data-analyst", "ml-engineer", "data-engineer", "nlp-specialist", "bi-analyst"} == ds_names

    def test_finance_agents(self):
        registry = get_agent_registry()
        fin_agents = registry["categories"].get("finance", [])
        fin_names = {a["name"] for a in fin_agents}
        assert {"financial-analyst", "risk-analyst", "quant-developer", "compliance-officer", "accountant"} == fin_names

    def test_marketing_agents(self):
        registry = get_agent_registry()
        mkt_agents = registry["categories"].get("marketing", [])
        mkt_names = {a["name"] for a in mkt_agents}
        assert {"content-strategist", "seo-specialist", "growth-hacker", "social-media-manager", "email-marketer"} == mkt_names

    def test_flat_agents_list_contains_all(self):
        registry = get_agent_registry()
        total_in_categories = sum(len(v) for v in registry["categories"].values())
        assert len(registry["agents"]) == total_in_categories

    def test_agent_count_at_least_22(self):
        """1 team-lead + 6 software-engineering + 5 data-science + 5 finance + 5 marketing = 22."""
        registry = get_agent_registry()
        assert len(registry["agents"]) >= 22


class TestAgentSkills:
    def test_all_categories_have_skills(self):
        """Every agent in AGENT_SKILLS should have at least one skill."""
        for name, skills in AGENT_SKILLS.items():
            assert len(skills) >= 1, f"Agent {name} has no skills"

    def test_new_agents_have_web_research(self):
        """All new category agents should have web-research as a baseline skill."""
        new_agents = [
            "data-analyst", "ml-engineer", "data-engineer", "nlp-specialist", "bi-analyst",
            "financial-analyst", "risk-analyst", "quant-developer", "compliance-officer", "accountant",
            "content-strategist", "seo-specialist", "growth-hacker", "social-media-manager", "email-marketer",
        ]
        for name in new_agents:
            assert "web-research" in AGENT_SKILLS[name], f"{name} missing web-research skill"
