"""SkillLoaderSkill — meta-skill that loads full instructions for another skill on demand.

Part of the Progressive Skill Loading feature: system prompts include only compact
skill summaries (name + description + category).  When an agent needs detailed
instructions it invokes ``load_skill`` which fetches the full text from the
SkillRegistry and increments a ``skill_loads_total`` counter for token tracking.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..core.skill import Skill, SkillResult

if TYPE_CHECKING:
    from ..core.metrics import Counter
    from ..core.skill import SkillRegistry

logger = logging.getLogger(__name__)


class SkillLoaderSkill(Skill):
    """Meta-skill: loads full instructions for another skill on demand."""

    def __init__(
        self,
        registry: SkillRegistry,
        loads_counter: Counter | None = None,
    ) -> None:
        self._registry = registry
        self._counter = loads_counter
        self._loads_per_session: int = 0

    # ── Skill interface ──────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "load_skill"

    @property
    def description(self) -> str:
        return "Load full instructions for a skill by name"

    @property
    def category(self) -> str:
        return "meta"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Name of the skill to load full instructions for",
                },
            },
            "required": ["skill_name"],
        }

    @property
    def full_instructions(self) -> str | None:
        return (
            "Use load_skill to fetch detailed instructions for any registered skill. "
            "Pass the skill name as 'skill_name'. Returns the full instruction text "
            "or an error if the skill is not found."
        )

    # ── Execution ────────────────────────────────────────────────────

    async def execute(self, params: dict) -> SkillResult:
        skill_name = params.get("skill_name", "")
        if not skill_name:
            return SkillResult(
                success=False,
                output=None,
                error="Missing required parameter: skill_name",
            )

        instructions = self._registry.get_full_instructions(skill_name)
        if instructions is None:
            logger.warning("load_skill: unknown skill '%s'", skill_name)
            return SkillResult(
                success=False,
                output=f"Unknown skill: {skill_name}",
            )

        # Track the load
        self._loads_per_session += 1
        if self._counter is not None:
            self._counter.inc()
        logger.info(
            "load_skill: loaded '%s' (session total: %d)",
            skill_name,
            self._loads_per_session,
        )

        return SkillResult(success=True, output=instructions)

    # ── Accessors ────────────────────────────────────────────────────

    @property
    def loads_per_session(self) -> int:
        """Number of load_skill invocations in the current session."""
        return self._loads_per_session

    def reset_session(self) -> None:
        """Reset the per-session load counter (e.g. on new session start)."""
        self._loads_per_session = 0
