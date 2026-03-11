"""LLM provider abstraction — the core interface that makes everything swappable."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Message:
    role: Role
    content: str
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] | None = None


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict  # JSON Schema


@dataclass
class ModelCapabilities:
    max_context: int
    supports_tools: bool = True
    supports_vision: bool = False
    supports_streaming: bool = True
    coding_quality: float = 0.0  # 0-1 relative score
    reasoning_quality: float = 0.0  # 0-1 relative score
    max_output_tokens: int = 4096  # dynamic per-model output limit


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class Completion:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = "end_turn"


@dataclass
class StreamChunk:
    content: str = ""
    tool_call: ToolCall | None = None
    is_final: bool = False


class Provider(ABC):
    """Abstract LLM provider. Implement this to add a new backend."""

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Completion:
        """Generate a completion from the model."""
        ...

    @abstractmethod
    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a completion from the model."""
        ...

    @property
    @abstractmethod
    def model_id(self) -> str:
        """Unique identifier for this model (e.g. 'claude-sonnet-4-6')."""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> ModelCapabilities:
        """What this model can do."""
        ...

    @property
    @abstractmethod
    def input_cost_per_million(self) -> float:
        """Cost per 1M input tokens in USD."""
        ...

    @property
    @abstractmethod
    def output_cost_per_million(self) -> float:
        """Cost per 1M output tokens in USD."""
        ...

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost for a given token count."""
        return (
            input_tokens * self.input_cost_per_million / 1_000_000
            + output_tokens * self.output_cost_per_million / 1_000_000
        )
