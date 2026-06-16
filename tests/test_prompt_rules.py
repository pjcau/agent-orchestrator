"""Regression tests: generic software-engineering rules must stay in the
agent prompts.

These rules were added after the 2026-04-14 learning-path-test surfaced
recurring failure modes (hallucinated version pins, parallel layouts,
README routed to marketing, agents looping past max_steps). Keep this
test tight — asserts the presence of the key phrases, not the exact
wording, so rewording stays cheap but accidental deletion is caught.
"""

from __future__ import annotations

import inspect

from agent_orchestrator.dashboard import agent_runner


def test_software_engineering_role_contains_generic_rules() -> None:
    role = agent_runner._build_role_for_agent(
        {"name": "backend", "description": "API", "category": "software-engineering"}
    )
    low = role.lower()
    # dependency pinning
    assert "dependency pins" in low
    assert "NEVER invent a version" in role
    # smoke-test mention (optional but should be documented)
    assert "smoke-test" in low or "smoke test" in low
    assert "python -c" in role
    # wiring integrity — agents must know to register routers/blueprints/etc.
    assert "registered" in low or "wiring" in low
    assert "include_router" in role
    assert "register_blueprint" in role
    assert "add_command" in role
    # single source of truth
    assert "single source of truth" in low or "parallel layout" in low
    # finish cleanly + STATUS.md handoff
    assert "finish cleanly" in low or "stop" in low
    assert "STATUS.md" in role


def test_finance_role_does_not_contain_engineering_rules() -> None:
    """The SE-specific rules must not leak into other categories."""
    role = agent_runner._build_role_for_agent(
        {"name": "financial-analyst", "description": "Financial modelling", "category": "finance"}
    )
    assert "Dependency pins" not in role
    assert "Smoke-test" not in role


def test_team_lead_plan_prompt_routes_docs_to_engineering() -> None:
    """team-lead plan system prompt must reject routing technical docs to
    content-strategist."""
    src = inspect.getsource(agent_runner.run_team)
    assert "content-strategist" in src
    assert "marketing copy, not technical writing" in src


def test_team_lead_plan_prompt_forbids_over_decomposition() -> None:
    src = inspect.getsource(agent_runner.run_team)
    assert "over-decompose" in src or "single agent" in src.lower()


def test_team_lead_plan_prompt_routes_by_file_ownership() -> None:
    """A failing command / Docker mention must not auto-route to devops; the
    plan prompt must route code fixes to the file owner (backend/frontend) and
    keep devops scoped to infra. Guards the always-devops regression seen in
    the 2026-06-16 agent-host session (team-lead → devops → 0 file_write)."""
    src = inspect.getsource(agent_runner.run_team)
    low = src.lower()
    assert "file ownership" in low
    assert "do not default to devops" in low
    # devops must be explicitly scoped to infra, not general code
    assert "only dockerfile" in low or "infra config" in low


def test_team_lead_plan_prompt_demands_applied_changes() -> None:
    """The plan must produce applied edits, not analysis-only output."""
    src = inspect.getsource(agent_runner.run_team)
    low = src.lower()
    assert "outcome first" in low
    assert "result in applied changes" in low


def test_minimal_changes_steer_requires_applying_the_change() -> None:
    """Every sub-agent gets a standing rule: a fix/implement task that ends
    with no file change is a failure (analysis-only tasks exempt)."""
    steer = agent_runner._MINIMAL_CHANGES_STEER.lower()
    assert "outcome requirement" in steer
    assert "must apply the change" in steer
    assert "no file change is a failure" in steer
    # analysis/review tasks must stay exempt so we don't force pointless writes
    assert "exempt" in steer


def test_team_lead_validation_has_wiring_check() -> None:
    """Validation must now check wiring + deps coherence + smoke-test evidence."""
    src = inspect.getsource(agent_runner.run_team)
    low = src.lower()
    assert "entry-point wiring" in low or "wiring" in low
    assert "dependency coherence" in low or "orphan imports" in low
    assert "smoke test evidence" in low or "smoke test" in low
