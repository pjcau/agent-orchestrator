"""Skill — provider-independent capabilities that agents can invoke."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class SkillResult:
    success: bool
    output: Any
    error: str | None = None

    def __str__(self) -> str:
        if self.success:
            return str(self.output)
        return f"Error: {self.error}"


class Skill(ABC):
    """A tool/capability that agents can use. Provider-independent."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def parameters(self) -> dict:
        """JSON Schema for the skill's parameters."""
        ...

    @abstractmethod
    async def execute(self, params: dict) -> SkillResult: ...


class SkillRegistry:
    """Central registry of all available skills."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    async def execute(self, name: str, params: dict) -> SkillResult:
        skill = self._skills.get(name)
        if skill is None:
            return SkillResult(success=False, output=None, error=f"Unknown skill: {name}")
        try:
            return await skill.execute(params)
        except Exception as e:
            return SkillResult(success=False, output=None, error=str(e))

    def list_skills(self) -> list[str]:
        return list(self._skills.keys())

    def to_tool_definitions(self) -> list[dict]:
        """Export all skills as tool definitions (for LLM APIs)."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "parameters": s.parameters,
            }
            for s in self._skills.values()
        ]
