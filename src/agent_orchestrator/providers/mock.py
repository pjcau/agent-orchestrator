"""Scripted mock provider for tests, CI, and offline simulation.

Promoted to a first-class provider (research-scout consolidated finding #12):
test suites previously each declared their own throwaway mock; this one is the
canonical implementation, and it lets library users exercise orchestrator
flows without spending a single API token.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from ..core.provider import (
    Completion,
    Message,
    ModelCapabilities,
    Provider,
    StreamChunk,
    ToolDefinition,
    Usage,
)


class MockProvider(Provider):
    """Provider that replays a predefined script of responses.

    Each entry in *script* is either a plain string (wrapped into a
    :class:`Completion` with zero cost) or a full :class:`Completion` (for
    scripted tool calls, custom usage, or stop reasons). Calls past the end
    of the script replay the last entry, so a single-entry script behaves as
    a constant responder.

    Every call is captured in :attr:`calls` (messages, tools, system,
    kwargs) so tests can assert on exactly what the orchestrator sent.

    Usage::

        provider = MockProvider(["step one", "done"])
        completion = await provider.complete([Message(Role.USER, "hi")])
        assert completion.content == "step one"
        assert provider.call_count == 1
    """

    def __init__(
        self,
        script: list[str | Completion] | None = None,
        model_id: str = "mock-model",
        latency_seconds: float = 0.0,
        max_context: int = 128_000,
    ) -> None:
        if script is not None and not script:
            raise ValueError("script must be None or non-empty")
        self._script: list[str | Completion] = list(script) if script else ["ok"]
        self._model_id = model_id
        self._latency = latency_seconds
        self._max_context = max_context
        self._call_count = 0
        #: Captured inputs of every complete()/stream() call, oldest first.
        self.calls: list[dict] = []

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def call_count(self) -> int:
        return self._call_count

    def _next_completion(self) -> Completion:
        idx = min(self._call_count, len(self._script) - 1)
        self._call_count += 1
        entry = self._script[idx]
        if isinstance(entry, Completion):
            return entry
        return Completion(content=entry, tool_calls=[], usage=Usage(0, 0, 0.0))

    # ------------------------------------------------------------------
    # Provider interface
    # ------------------------------------------------------------------

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Completion:
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "system": system,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        if self._latency:
            await asyncio.sleep(self._latency)
        return self._next_completion()

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> AsyncIterator[StreamChunk]:
        completion = await self.complete(
            messages, tools=tools, system=system, max_tokens=max_tokens
        )
        # Stream the scripted content word by word, then tool calls, then final.
        for word in completion.content.split(" "):
            yield StreamChunk(content=word + " ")
        for tc in completion.tool_calls:
            yield StreamChunk(tool_call=tc)
        yield StreamChunk(is_final=True)

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            max_context=self._max_context,
            supports_tools=True,
            supports_vision=False,
            supports_streaming=True,
        )

    @property
    def input_cost_per_million(self) -> float:
        return 0.0

    @property
    def output_cost_per_million(self) -> float:
        return 0.0
