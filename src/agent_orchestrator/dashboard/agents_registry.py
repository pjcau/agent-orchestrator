"""Agent and skill registry for the dashboard.

Reads agent definitions from .claude/agents/ and skill definitions from
.claude/skills/ to build a hierarchy for the dashboard UI.
"""

from __future__ import annotations

import re
from pathlib import Path

# Project root: go up from dashboard/ -> agent_orchestrator/ -> src/ -> project root
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

AGENTS_DIR = PROJECT_ROOT / ".claude" / "agents"
SKILLS_DIR = PROJECT_ROOT / ".claude" / "skills"

# Map agents to their skills (hardcoded based on project structure)
AGENT_SKILLS = {
    "team-lead": ["web-research"],
    "backend": ["test-runner", "lint-check", "code-review", "web-research"],
    "frontend": ["website-dev", "lint-check", "web-research"],
    "devops": ["docker-build", "deploy", "web-research"],
    "platform-engineer": ["docker-build", "deploy", "web-research"],
    "ai-engineer": ["code-review", "test-runner", "web-research"],
    "scout": ["scout", "web-research"],
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


def get_agent_registry() -> dict:
    """Build the agent/skill registry from project files."""
    agents = []
    skills = []

    # Read agent definitions
    if AGENTS_DIR.is_dir():
        for md_file in sorted(AGENTS_DIR.glob("*.md")):
            try:
                text = md_file.read_text()
                fm = _parse_frontmatter(text)
                name = fm.get("name", md_file.stem)
                agents.append(
                    {
                        "name": name,
                        "model": fm.get("model", ""),
                        "description": fm.get("description", ""),
                        "skills": AGENT_SKILLS.get(name, []),
                    }
                )
            except Exception:
                continue

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

    # Sort: team-lead first
    agents.sort(key=lambda a: (0 if a["name"] == "team-lead" else 1, a["name"]))

    return {"agents": agents, "skills": skills}
