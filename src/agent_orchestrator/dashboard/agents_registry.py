"""Agent and skill registry for the dashboard.

Reads agent definitions from .claude/agents/ (including category sub-folders)
and skill definitions from .claude/skills/ to build a hierarchy for the
dashboard UI.

Agent files can live at:
  .claude/agents/team-lead.md          (root-level, no category)
  .claude/agents/software-engineering/backend.md  (categorised)
"""

from __future__ import annotations

import re
from pathlib import Path

# Project root: go up from dashboard/ -> agent_orchestrator/ -> src/ -> project root
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

AGENTS_DIR = PROJECT_ROOT / ".claude" / "agents"
SKILLS_DIR = PROJECT_ROOT / ".claude" / "skills"

# Map agents to their skills (hardcoded based on project structure)
AGENT_SKILLS: dict[str, list[str]] = {
    # software-engineering
    "team-lead": ["web-research"],
    "backend": ["test-runner", "lint-check", "code-review", "web-research"],
    "frontend": ["website-dev", "lint-check", "web-research"],
    "devops": ["docker-build", "deploy", "web-research"],
    "platform-engineer": ["docker-build", "deploy", "web-research"],
    "ai-engineer": ["code-review", "test-runner", "web-research"],
    "scout": ["scout", "web-research"],
    "research-scout": ["scout", "web-research"],
    # data-science
    "data-analyst": ["web-research"],
    "ml-engineer": ["web-research"],
    "data-engineer": ["web-research"],
    "nlp-specialist": ["web-research"],
    "bi-analyst": ["web-research"],
    # finance
    "financial-analyst": ["web-research"],
    "risk-analyst": ["web-research"],
    "quant-developer": ["web-research"],
    "compliance-officer": ["web-research"],
    "accountant": ["web-research"],
    # marketing
    "content-strategist": ["web-research"],
    "seo-specialist": ["web-research"],
    "growth-hacker": ["web-research"],
    "social-media-manager": ["web-research"],
    "email-marketer": ["web-research"],
}


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Extract YAML frontmatter from markdown file."""
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return {}
    result = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


def _read_agent_file(md_file: Path, category: str) -> dict | None:
    """Parse one agent markdown file into a registry dict."""
    try:
        text = md_file.read_text()
        fm = _parse_frontmatter(text)
        name = fm.get("name", md_file.stem)
        return {
            "name": name,
            "model": fm.get("model", ""),
            "description": fm.get("description", ""),
            "category": fm.get("category", category),
            "skills": AGENT_SKILLS.get(name, []),
        }
    except Exception:
        return None


def get_agent_registry() -> dict:
    """Build the agent/skill registry from project files.

    Scans .claude/agents/ for root-level .md files and category
    sub-directories (e.g. software-engineering/, data-science/).
    Returns agents grouped by category and a flat skill list.
    """
    agents: list[dict] = []
    skills: list[dict] = []

    if AGENTS_DIR.is_dir():
        # Root-level agents (e.g. team-lead.md)
        for md_file in sorted(AGENTS_DIR.glob("*.md")):
            agent = _read_agent_file(md_file, category="general")
            if agent:
                agents.append(agent)

        # Category sub-directories
        for subdir in sorted(AGENTS_DIR.iterdir()):
            if subdir.is_dir():
                category = subdir.name
                for md_file in sorted(subdir.glob("*.md")):
                    agent = _read_agent_file(md_file, category=category)
                    if agent:
                        agents.append(agent)

    # Read skill definitions
    if SKILLS_DIR.is_dir():
        for skill_dir in sorted(SKILLS_DIR.iterdir()):
            skill_md = skill_dir / "SKILL.md"
            if skill_md.is_file():
                try:
                    text = skill_md.read_text()
                    fm = _parse_frontmatter(text)
                    skills.append(
                        {
                            "name": fm.get("name", skill_dir.name),
                            "description": fm.get("description", ""),
                        }
                    )
                except Exception:
                    continue

    # Sort: team-lead first, then alphabetically within each category
    agents.sort(key=lambda a: (0 if a["name"] == "team-lead" else 1, a["category"], a["name"]))

    # Build categories dict for grouped view
    categories: dict[str, list[dict]] = {}
    for agent in agents:
        cat = agent["category"]
        categories.setdefault(cat, []).append(agent)

    return {"agents": agents, "categories": categories, "skills": skills}
