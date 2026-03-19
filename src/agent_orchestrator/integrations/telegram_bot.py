"""Telegram bot integration using long-polling (no public IP required).

Connects Telegram chat to the orchestrator dashboard API, routing messages
to agents and streaming responses back.

Requires: pip install agent-orchestrator[telegram]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Telegram message chunk limit (API max is 4096 chars).
MAX_CHUNK_SIZE = 4096


class OrchestratorClient(Protocol):
    """Minimal interface the bot needs from an orchestrator / HTTP client."""

    async def send_message(
        self, message: str, *, agent: str | None = None, conversation_id: str | None = None
    ) -> dict[str, Any]:
        """Send a user message and return the agent response dict."""
        ...

    async def list_agents(self) -> list[dict[str, Any]]:
        """Return available agents."""
        ...

    async def get_status(self) -> dict[str, Any]:
        """Return orchestrator status (health, metrics, etc.)."""
        ...


@dataclass
class TelegramBotConfig:
    """Configuration for the Telegram bot."""

    bot_token: str
    allowed_user_ids: list[int] = field(default_factory=list)
    default_agent: str = "backend"


class TelegramBot:
    """Telegram bot using long-polling (no public IP required).

    Bridges Telegram users to the orchestrator: each Telegram chat maps to
    a conversation_id, and free-text messages are forwarded to the configured
    default agent.

    Commands:
        /start  — welcome message
        /new    — reset conversation
        /status — orchestrator status
        /agents — list available agents
        /help   — show help
    """

    def __init__(
        self,
        orchestrator_client: OrchestratorClient,
        bot_token: str,
        allowed_user_ids: list[int] | None = None,
        default_agent: str = "backend",
    ) -> None:
        try:
            from telegram.ext import Application
        except ImportError as exc:
            raise ImportError(
                "python-telegram-bot is required. "
                "Install with: pip install agent-orchestrator[telegram]"
            ) from exc

        self.client = orchestrator_client
        self.bot_token = bot_token
        self.allowed_user_ids: list[int] = allowed_user_ids or []
        self.default_agent = default_agent

        # chat_id -> conversation_id mapping
        self._conversations: dict[int, str] = {}

        # Build the application
        self._app: Application = Application.builder().token(bot_token).build()  # type: ignore[assignment]
        self._register_handlers()
        self._running = False

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------

    def _register_handlers(self) -> None:
        from telegram.ext import CommandHandler, MessageHandler, filters

        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("new", self._cmd_new))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("agents", self._cmd_agents))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def _check_auth(self, update: Any) -> bool:
        """Return True if the user is authorised.

        If ``allowed_user_ids`` is empty, all users are allowed.
        """
        if not self.allowed_user_ids:
            return True
        user = update.effective_user
        if user is None:
            return False
        return user.id in self.allowed_user_ids

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _send_chunked(update: Any, text: str, chunk_size: int = MAX_CHUNK_SIZE) -> None:
        """Send a long message in chunks that fit Telegram's limit."""
        if not text:
            text = "(empty response)"
        for i in range(0, len(text), chunk_size):
            await update.message.reply_text(text[i : i + chunk_size])

    def _get_conversation_id(self, chat_id: int) -> str:
        """Return (or create) a conversation_id for the given chat."""
        if chat_id not in self._conversations:
            self._conversations[chat_id] = f"tg-{chat_id}"
        return self._conversations[chat_id]

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    async def _cmd_start(self, update: Any, context: Any) -> None:
        if not await self._check_auth(update):
            await update.message.reply_text("Unauthorized.")
            return
        await update.message.reply_text(
            "Welcome to Agent Orchestrator!\n\n"
            "Send any message to chat with an agent.\n"
            "Commands: /new /status /agents /help"
        )

    async def _cmd_new(self, update: Any, context: Any) -> None:
        if not await self._check_auth(update):
            await update.message.reply_text("Unauthorized.")
            return
        chat_id = update.effective_chat.id
        self._conversations[chat_id] = f"tg-{chat_id}-new-{id(update)}"
        await update.message.reply_text("Conversation reset. Send a new message to start.")

    async def _cmd_status(self, update: Any, context: Any) -> None:
        if not await self._check_auth(update):
            await update.message.reply_text("Unauthorized.")
            return
        try:
            status = await self.client.get_status()
            text = "\n".join(f"{k}: {v}" for k, v in status.items())
            await self._send_chunked(update, text or "No status available.")
        except Exception as exc:
            logger.exception("Failed to get status")
            await update.message.reply_text(f"Error: {exc}")

    async def _cmd_agents(self, update: Any, context: Any) -> None:
        if not await self._check_auth(update):
            await update.message.reply_text("Unauthorized.")
            return
        try:
            agents = await self.client.list_agents()
            if not agents:
                await update.message.reply_text("No agents available.")
                return
            lines = []
            for a in agents:
                name = a.get("name", "unknown")
                role = a.get("role", "")
                lines.append(f"- {name}" + (f" ({role})" if role else ""))
            await self._send_chunked(update, "\n".join(lines))
        except Exception as exc:
            logger.exception("Failed to list agents")
            await update.message.reply_text(f"Error: {exc}")

    async def _cmd_help(self, update: Any, context: Any) -> None:
        if not await self._check_auth(update):
            await update.message.reply_text("Unauthorized.")
            return
        await update.message.reply_text(
            "Agent Orchestrator Bot\n\n"
            "/start  — Welcome message\n"
            "/new    — Reset conversation\n"
            "/status — Orchestrator status\n"
            "/agents — List available agents\n"
            "/help   — This help message\n\n"
            "Send any text to chat with the default agent."
        )

    # ------------------------------------------------------------------
    # Free-text handler
    # ------------------------------------------------------------------

    async def _handle_message(self, update: Any, context: Any) -> None:
        if not await self._check_auth(update):
            await update.message.reply_text("Unauthorized.")
            return
        chat_id = update.effective_chat.id
        conversation_id = self._get_conversation_id(chat_id)
        user_text = update.message.text

        try:
            result = await self.client.send_message(
                user_text,
                agent=self.default_agent,
                conversation_id=conversation_id,
            )
            response_text = result.get("response", result.get("text", str(result)))
            await self._send_chunked(update, response_text)
        except Exception as exc:
            logger.exception("Failed to handle message")
            await update.message.reply_text(f"Error: {exc}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Initialise the bot and start long-polling."""
        logger.info("Starting Telegram bot (long-polling)...")
        self._running = True
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()  # type: ignore[union-attr]
        logger.info("Telegram bot started.")

    async def stop(self) -> None:
        """Gracefully shut down the bot."""
        if not self._running:
            return
        logger.info("Stopping Telegram bot...")
        self._running = False
        await self._app.updater.stop()  # type: ignore[union-attr]
        await self._app.stop()
        await self._app.shutdown()
        logger.info("Telegram bot stopped.")

    @property
    def running(self) -> bool:
        return self._running
