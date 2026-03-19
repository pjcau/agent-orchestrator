"""Slack bot integration using Socket Mode (no public IP required).

Connects the orchestrator to Slack, mapping Slack threads to orchestrator
conversation threads and routing tasks via category auto-detection.

Usage:
    bot = SlackBot(
        orchestrator_client=my_client,
        config=SlackBotConfig(
            bot_token="xoxb-...",
            app_token="xapp-...",
        ),
    )
    await bot.start()

Requires: ``pip install agent-orchestrator[slack]``
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Category auto-detection (mirrors dashboard/agent_runner.py keywords)
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "finance": [
        "finance",
        "financial",
        "stock",
        "portfolio",
        "trading",
        "investment",
        "risk",
        "valuation",
        "dcf",
        "revenue",
        "forecast",
        "budget",
        "cash flow",
        "balance sheet",
        "p&l",
        "profit",
        "loss",
        "hedge",
        "option",
        "derivative",
        "bond",
        "equity",
        "market",
        "compliance",
        "audit",
        "accounting",
        "tax",
        "roi",
        "irr",
        "npv",
    ],
    "data-science": [
        "data",
        "dataset",
        "analysis",
        "machine learning",
        "ml",
        "model",
        "prediction",
        "classification",
        "regression",
        "clustering",
        "nlp",
        "embeddings",
        "eda",
        "visualization",
        "statistics",
        "etl",
        "pipeline",
        "kpi",
        "metrics",
        "bi",
    ],
    "marketing": [
        "marketing",
        "seo",
        "content",
        "social media",
        "email",
        "campaign",
        "funnel",
        "conversion",
        "growth",
        "brand",
        "audience",
        "keyword",
        "engagement",
        "newsletter",
        "ad",
        "advertising",
        "copy",
        "cro",
    ],
}


def detect_category(text: str) -> str:
    """Detect the most likely agent category from message text.

    Returns the category name (e.g. ``"finance"``, ``"data-science"``,
    ``"marketing"``, ``"software-engineering"``). Falls back to
    ``"software-engineering"`` when no keywords match.
    """
    text_lower = text.lower()
    scores: dict[str, int] = {}
    for category, keywords in _CATEGORY_KEYWORDS.items():
        scores[category] = sum(1 for kw in keywords if kw in text_lower)
    best = max(scores, key=scores.get) if scores else "software-engineering"
    return best if scores.get(best, 0) > 0 else "software-engineering"


# ---------------------------------------------------------------------------
# Orchestrator client protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class OrchestratorClient(Protocol):
    """Minimal interface the SlackBot expects from an orchestrator client.

    This can be the ``Orchestrator`` class directly, a dashboard HTTP client,
    or any object providing these two async methods.
    """

    async def run_agent(
        self,
        task: str,
        agent: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]: ...

    async def run_team(
        self,
        task: str,
        conversation_id: str | None = None,
    ) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class SlackBotConfig:
    """Configuration for the Slack bot."""

    bot_token: str = ""
    app_token: str = ""
    default_agent: str = "backend"
    max_response_length: int = 3000
    # Additional kwargs forwarded to slack_bolt.App
    app_kwargs: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Default agents per category (used when no specific agent is requested)
# ---------------------------------------------------------------------------

_CATEGORY_DEFAULT_AGENTS: dict[str, str] = {
    "finance": "financial-analyst",
    "data-science": "data-analyst",
    "marketing": "content-strategist",
    "software-engineering": "backend",
}


# ---------------------------------------------------------------------------
# Slack Bot
# ---------------------------------------------------------------------------


def _truncate(text: str, max_length: int) -> str:
    """Truncate text to max_length, appending an ellipsis if truncated."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def _thread_to_conversation_id(channel: str, thread_ts: str | None) -> str:
    """Map a Slack thread to an orchestrator conversation ID.

    Format: ``slack-<channel>-<thread_ts>``
    If no thread_ts (top-level message), uses the message ts.
    """
    ts = thread_ts or "0"
    return f"slack-{channel}-{ts}"


def _extract_task_from_mention(text: str) -> str:
    """Strip the bot mention from an app_mention event text.

    Slack formats mentions as ``<@U12345> some task``. We remove the
    leading mention tag to get the pure task text.
    """
    import re

    return re.sub(r"<@\w+>\s*", "", text).strip()


class SlackBot:
    """Slack bot using Socket Mode (no public IP required).

    Handles:
    - ``@bot`` mentions: routes to a single agent based on category detection
    - ``/agent <task>``: routes to a specific or auto-detected agent
    - ``/team <task>``: triggers a multi-agent team run

    Thread-based conversations are mapped to orchestrator conversation threads
    using the format ``slack-{channel}-{thread_ts}``.
    """

    def __init__(
        self,
        orchestrator_client: OrchestratorClient,
        config: SlackBotConfig | None = None,
        *,
        bot_token: str | None = None,
        app_token: str | None = None,
    ):
        if config is None:
            config = SlackBotConfig(
                bot_token=bot_token or "",
                app_token=app_token or "",
            )
        self.config = config
        self.client = orchestrator_client
        self._app: Any = None
        self._handler: Any = None
        self._running = False

    def _ensure_app(self) -> Any:
        """Lazily create the slack_bolt App (only when actually starting)."""
        if self._app is not None:
            return self._app

        try:
            from slack_bolt.async_app import AsyncApp
        except ImportError as exc:
            raise ImportError(
                "slack-bolt is required for Slack integration. "
                "Install with: pip install agent-orchestrator[slack]"
            ) from exc

        self._app = AsyncApp(
            token=self.config.bot_token,
            **self.config.app_kwargs,
        )
        self._register_handlers()
        return self._app

    def _register_handlers(self) -> None:
        """Register event and command handlers on the Slack app."""
        app = self._app

        @app.event("app_mention")
        async def handle_mention(event: dict, say: Any) -> None:
            await self._handle_mention(event, say)

        @app.command("/agent")
        async def handle_agent_command(ack: Any, command: dict, respond: Any) -> None:
            await ack()
            await self._handle_agent_command(command, respond)

        @app.command("/team")
        async def handle_team_command(ack: Any, command: dict, respond: Any) -> None:
            await ack()
            await self._handle_team_command(command, respond)

    async def _handle_mention(self, event: dict, say: Any) -> None:
        """Handle @bot mention events."""
        text = event.get("text", "")
        task = _extract_task_from_mention(text)
        if not task:
            await say(
                text="Please provide a task after mentioning me.",
                thread_ts=event.get("thread_ts") or event.get("ts"),
            )
            return

        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts")
        conversation_id = _thread_to_conversation_id(channel, thread_ts)

        category = detect_category(task)
        agent = _CATEGORY_DEFAULT_AGENTS.get(category, self.config.default_agent)

        try:
            result = await self.client.run_agent(
                task=task,
                agent=agent,
                conversation_id=conversation_id,
            )
            output = result.get("output", "No output returned.")
            response = _truncate(output, self.config.max_response_length)
        except Exception as exc:
            logger.error("Orchestrator error on mention: %s", exc)
            response = f"Sorry, something went wrong: {exc}"

        await say(text=response, thread_ts=thread_ts)

    async def _handle_agent_command(self, command: dict, respond: Any) -> None:
        """Handle /agent slash command."""
        text = command.get("text", "").strip()
        if not text:
            await respond("Usage: `/agent <task>` or `/agent <agent_name> <task>`")
            return

        channel = command.get("channel_id", "")
        # Slash commands do not have thread_ts — use channel as context
        conversation_id = _thread_to_conversation_id(channel, None)

        # Check if first word is a known agent name
        parts = text.split(None, 1)
        agent: str | None = None
        task = text
        if len(parts) == 2:
            # Tentatively treat first word as agent name
            candidate = parts[0].lower()
            if candidate in _CATEGORY_DEFAULT_AGENTS.values() or candidate in {
                "backend",
                "frontend",
                "devops",
                "platform-engineer",
                "ai-engineer",
                "scout",
                "research-scout",
                "security-auditor",
                "data-analyst",
                "ml-engineer",
                "data-engineer",
                "nlp-specialist",
                "bi-analyst",
                "financial-analyst",
                "risk-analyst",
                "quant-developer",
                "compliance-officer",
                "accountant",
                "content-strategist",
                "seo-specialist",
                "growth-hacker",
                "social-media-manager",
                "email-marketer",
            }:
                agent = candidate
                task = parts[1]

        if agent is None:
            category = detect_category(task)
            agent = _CATEGORY_DEFAULT_AGENTS.get(category, self.config.default_agent)

        try:
            result = await self.client.run_agent(
                task=task,
                agent=agent,
                conversation_id=conversation_id,
            )
            output = result.get("output", "No output returned.")
            response = _truncate(output, self.config.max_response_length)
        except Exception as exc:
            logger.error("Orchestrator error on /agent: %s", exc)
            response = f"Sorry, something went wrong: {exc}"

        await respond(response)

    async def _handle_team_command(self, command: dict, respond: Any) -> None:
        """Handle /team slash command."""
        text = command.get("text", "").strip()
        if not text:
            await respond("Usage: `/team <task description>`")
            return

        channel = command.get("channel_id", "")
        conversation_id = _thread_to_conversation_id(channel, None)

        try:
            result = await self.client.run_team(
                task=text,
                conversation_id=conversation_id,
            )
            output = result.get("output", "No output returned.")
            response = _truncate(output, self.config.max_response_length)
        except Exception as exc:
            logger.error("Orchestrator error on /team: %s", exc)
            response = f"Sorry, something went wrong: {exc}"

        await respond(response)

    async def start(self) -> None:
        """Start the Slack bot using Socket Mode.

        Requires ``app_token`` (xapp-...) for Socket Mode connection.
        Blocks until :meth:`stop` is called.
        """
        app = self._ensure_app()

        try:
            from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
        except ImportError as exc:
            raise ImportError(
                "slack-bolt is required for Slack integration. "
                "Install with: pip install agent-orchestrator[slack]"
            ) from exc

        self._handler = AsyncSocketModeHandler(app, self.config.app_token)
        self._running = True
        logger.info("Starting Slack bot (Socket Mode)")
        await self._handler.start_async()

    async def stop(self) -> None:
        """Stop the Slack bot gracefully."""
        self._running = False
        if self._handler is not None:
            logger.info("Stopping Slack bot")
            await self._handler.close_async()
            self._handler = None
        self._app = None

    @property
    def is_running(self) -> bool:
        return self._running
