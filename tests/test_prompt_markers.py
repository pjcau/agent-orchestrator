"""Tests for marker-based prompt injection (PR #57)."""

from agent_orchestrator.core.prompt_markers import (
    diff_sections,
    extract_marker_sections,
    inject_marker_sections,
)


class TestInjectMarkerSections:
    def test_appends_new_section_when_marker_absent(self):
        base = "You are helpful."
        out = inject_marker_sections(base, {"RULES": "1. be concise"})
        assert "<!-- RULES START -->" in out
        assert "<!-- RULES END -->" in out
        assert "1. be concise" in out
        assert out.startswith("You are helpful.")

    def test_replaces_existing_section_in_place(self):
        base = "Header text.\n<!-- RULES START -->\nold rules\n<!-- RULES END -->\nFooter text."
        out = inject_marker_sections(base, {"RULES": "new rules"})
        assert "old rules" not in out
        assert "new rules" in out
        assert "Header text." in out
        assert "Footer text." in out

    def test_is_idempotent(self):
        base = "Base prompt."
        once = inject_marker_sections(base, {"A": "alpha"})
        twice = inject_marker_sections(once, {"A": "alpha"})
        assert once == twice

    def test_preserves_unrelated_sections(self):
        base = "<!-- A START -->\nalpha\n<!-- A END -->\n<!-- B START -->\nbeta\n<!-- B END -->"
        out = inject_marker_sections(base, {"A": "ALPHA"})
        sections = extract_marker_sections(out)
        assert sections["A"] == "ALPHA"
        assert sections["B"] == "beta"

    def test_multiple_sections_in_one_call(self):
        base = "base"
        out = inject_marker_sections(base, {"X": "x-content", "Y": "y-content"})
        assert "x-content" in out
        assert "y-content" in out

    def test_does_not_mutate_input(self):
        base = "original"
        sections = {"X": "x"}
        out = inject_marker_sections(base, sections)
        assert base == "original"
        assert sections == {"X": "x"}
        assert out != base

    def test_section_with_multiline_content(self):
        base = ""
        multi = "line1\nline2\nline3"
        out = inject_marker_sections(base, {"M": multi})
        extracted = extract_marker_sections(out)
        assert extracted["M"] == multi


class TestExtractMarkerSections:
    def test_empty_prompt_returns_empty_dict(self):
        assert extract_marker_sections("") == {}

    def test_extracts_all_sections(self):
        p = "<!-- A START -->\none\n<!-- A END -->\n<!-- B START -->\ntwo\n<!-- B END -->"
        assert extract_marker_sections(p) == {"A": "one", "B": "two"}

    def test_ignores_mismatched_tags(self):
        p = "<!-- A START -->\nfoo\n<!-- B END -->"
        assert extract_marker_sections(p) == {}


class TestDiffSections:
    def test_identical_sections_omitted(self):
        a = "<!-- X START -->\nsame\n<!-- X END -->"
        b = "<!-- X START -->\nsame\n<!-- X END -->"
        assert diff_sections(a, b) == {}

    def test_different_sections_returned(self):
        a = "<!-- X START -->\none\n<!-- X END -->"
        b = "<!-- X START -->\ntwo\n<!-- X END -->"
        result = diff_sections(a, b)
        assert result == {"X": ("one", "two")}

    def test_section_added_on_one_side(self):
        a = ""
        b = "<!-- NEW START -->\nfresh\n<!-- NEW END -->"
        result = diff_sections(a, b)
        assert result == {"NEW": ("", "fresh")}


class TestAgentIntegration:
    """Verify Agent.build_system_prompt applies sections without drift."""

    def test_agent_build_system_prompt_without_sections_returns_role(self):
        from agent_orchestrator.core.agent import Agent, AgentConfig
        from agent_orchestrator.core.skill import SkillRegistry

        config = AgentConfig(name="t", role="base role", provider_key="x", tools=[])
        agent = Agent(
            config=config,
            provider=None,  # type: ignore[arg-type]
            skill_registry=SkillRegistry(),
        )
        assert agent.build_system_prompt() == "base role"

    def test_agent_set_prompt_section_updates_effective_prompt(self):
        from agent_orchestrator.core.agent import Agent, AgentConfig
        from agent_orchestrator.core.skill import SkillRegistry

        config = AgentConfig(name="t", role="base role", provider_key="x", tools=[])
        agent = Agent(
            config=config,
            provider=None,  # type: ignore[arg-type]
            skill_registry=SkillRegistry(),
        )
        agent.set_prompt_section("RULES", "be careful")
        built = agent.build_system_prompt()
        assert "be careful" in built
        assert "<!-- RULES START -->" in built

    def test_agent_set_prompt_section_increments_metric(self):
        from agent_orchestrator.core.agent import Agent, AgentConfig
        from agent_orchestrator.core.metrics import MetricsRegistry
        from agent_orchestrator.core.skill import SkillRegistry

        reg = MetricsRegistry()
        config = AgentConfig(name="alpha", role="base", provider_key="x", tools=[])
        agent = Agent(
            config=config,
            provider=None,  # type: ignore[arg-type]
            skill_registry=SkillRegistry(),
            metrics=reg,
        )
        agent.set_prompt_section("A", "one")
        agent.set_prompt_section("A", "two")
        agent.set_prompt_section("B", "three")

        counter = reg.counter("marker_updates_total", "", labels={"agent": "alpha"})
        assert counter.get() == 3
