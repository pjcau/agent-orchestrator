"""Profile extractor skill — distils user preferences from recent messages.

Calls a Provider to summarise the user's preferences, communication style,
and recurring topics into a compact JSON profile, then persists it via
``PersonalizedMemory.put(user_id, "profile", value)``.

The skill is designed for **best-effort, async-friendly** execution: any
provider or persistence failure is caught, logged, and surfaced as a
``SkillResult(success=False)`` without propagating exceptions.  This means
agents can fire-and-forget without worrying about this skill blocking their
main flow.

The ``Provider`` is injected at construction time — no real LLMs are ever
constructed inside this skill.

Skill name: ``extract_user_profile``
Category:   ``memory``
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from ..core.skill import Skill, SkillResult

if TYPE_CHECKING:
    from ..core.personalized_memory import PersonalizedMemory
    from ..core.provider import Provider

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT_HEADER = """\
You are a profile-extraction assistant. Analyse the conversation messages
provided and produce a concise JSON object summarising the user's preferences,
communication style, and recurring topics.

Return ONLY valid JSON in this exact shape — no prose, no markdown fences:
{"preferences": ["list of inferred user preferences"], "style_notes": ["list of communication style observations"], "recurring_topics": ["list of topics the user often mentions"]}

Messages to analyse:
"""


class ProfileExtractorSkill(Skill):
    """Extracts and persists a user preference profile from recent messages.

    Args:
        provider: LLM provider used to summarise preferences.
        personalized_memory: Destination store for the extracted profile.
        max_messages: Maximum number of messages forwarded to the provider
            (oldest are trimmed first).  Defaults to 20.
    """

    def __init__(
        self,
        provider: "Provider",
        personalized_memory: "PersonalizedMemory",
        max_messages: int = 20,
    ) -> None:
        self._provider = provider
        self._memory = personalized_memory
        self._max_messages = max_messages

    # ── Skill interface ────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "extract_user_profile"

    @property
    def description(self) -> str:
        return (
            "Analyse a user's recent messages to extract their preferences, "
            "communication style, and recurring topics, then persist the result "
            "in long-term personalized memory."
        )

    @property
    def category(self) -> str:
        return "memory"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "The unique identifier for the user whose profile should be extracted.",
                },
                "recent_messages": {
                    "type": "array",
                    "description": (
                        "List of recent message dicts with at least a 'content' key and "
                        "optionally a 'role' key (e.g. 'user', 'assistant')."
                    ),
                    "items": {"type": "object"},
                },
            },
            "required": ["user_id", "recent_messages"],
        }

    # ── Execution ──────────────────────────────────────────────────────

    async def execute(self, params: dict[str, Any]) -> SkillResult:
        """Extract and persist the user profile.

        Failures (provider errors, JSON parse errors, persistence errors) are
        all caught and returned as ``SkillResult(success=False)``.
        """
        user_id = str(params.get("user_id", "")).strip()
        if not user_id:
            return SkillResult(success=False, output=None, error="user_id is required")

        raw_messages: list[Any] = params.get("recent_messages", [])
        if not isinstance(raw_messages, list):
            return SkillResult(success=False, output=None, error="recent_messages must be a list")

        # Trim to max_messages (keep most recent)
        messages = raw_messages[-self._max_messages :]

        # Format messages for the prompt
        formatted = _format_messages(messages)

        prompt = _EXTRACTION_PROMPT_HEADER + formatted

        # ── Call provider ──────────────────────────────────────────────
        try:
            from ..core.provider import Message, Role

            completion = await self._provider.complete(
                messages=[Message(role=Role.USER, content=prompt)],
            )
            raw_output = completion.content.strip()
        except Exception as exc:
            logger.warning(
                "ProfileExtractorSkill: provider call failed for user=%s: %s",
                user_id,
                exc,
                exc_info=True,
            )
            return SkillResult(
                success=False,
                output=None,
                error=f"Provider call failed: {exc}",
            )

        # ── Parse JSON ─────────────────────────────────────────────────
        try:
            profile = _parse_profile_json(raw_output)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "ProfileExtractorSkill: JSON parse failed for user=%s: %s",
                user_id,
                exc,
            )
            return SkillResult(
                success=False,
                output=raw_output,
                error=f"Profile JSON parse failed: {exc}",
            )

        # ── Persist ────────────────────────────────────────────────────
        try:
            await self._memory.put(user_id, "profile", profile)
        except Exception as exc:
            logger.warning(
                "ProfileExtractorSkill: persistence failed for user=%s: %s",
                user_id,
                exc,
                exc_info=True,
            )
            return SkillResult(
                success=False,
                output=json.dumps(profile),
                error=f"Persistence failed: {exc}",
            )

        return SkillResult(
            success=True,
            output=json.dumps(profile),
            metadata={"saved_keys": ["profile"]},
        )


# ── Helpers ────────────────────────────────────────────────────────────────


def _format_messages(messages: list[Any]) -> str:
    """Render the message list as a readable text block for the prompt."""
    lines: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "unknown"))
        content = str(msg.get("content", "")).strip()
        if content:
            lines.append(f"[{role}]: {content}")
    return "\n".join(lines) if lines else "(no messages)"


def _parse_profile_json(raw: str) -> dict[str, Any]:
    """Parse and validate the profile JSON returned by the provider.

    Strips optional markdown fences before parsing.  Raises ``ValueError``
    when the parsed object is not a dict with the expected keys.
    """
    text = raw.strip()
    # Strip ```json ... ``` fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop first (fence) and last line if it is a closing fence
        inner = lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:]
        text = "\n".join(inner).strip()

    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("Profile must be a JSON object")

    # Normalise: ensure all expected keys exist
    for key in ("preferences", "style_notes", "recurring_topics"):
        if key not in parsed:
            parsed[key] = []
        elif not isinstance(parsed[key], list):
            parsed[key] = [str(parsed[key])]

    return parsed
