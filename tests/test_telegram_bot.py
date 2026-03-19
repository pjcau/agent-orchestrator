"""Tests for Telegram bot integration."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Stub the telegram package so tests run without python-telegram-bot installed
# ---------------------------------------------------------------------------

_filters_mod = MagicMock()
_filters_mod.TEXT = MagicMock()
_filters_mod.COMMAND = MagicMock()
# TEXT & ~COMMAND must return a truthy filter mock
_filters_mod.TEXT.__and__ = lambda self, other: MagicMock()
_filters_mod.TEXT.__rand__ = lambda self, other: MagicMock()

_ext_mod = MagicMock()
_ext_mod.filters = _filters_mod
_ext_mod.CommandHandler = MagicMock()
_ext_mod.MessageHandler = MagicMock()

# Application builder pattern
_app_instance = MagicMock()
_app_instance.initialize = AsyncMock()
_app_instance.start = AsyncMock()
_app_instance.stop = AsyncMock()
_app_instance.shutdown = AsyncMock()
_app_instance.updater = MagicMock()
_app_instance.updater.start_polling = AsyncMock()
_app_instance.updater.stop = AsyncMock()
_app_instance.add_handler = MagicMock()

_builder = MagicMock()
_builder.token.return_value = _builder
_builder.build.return_value = _app_instance
_ext_mod.Application.builder.return_value = _builder

_telegram_mod = MagicMock()
_telegram_mod.Update = MagicMock()

sys.modules.setdefault("telegram", _telegram_mod)
sys.modules.setdefault("telegram.ext", _ext_mod)

from agent_orchestrator.integrations.telegram_bot import TelegramBot  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeOrchestratorClient:
    """In-memory orchestrator client for testing."""

    def __init__(
        self,
        agents: list[dict[str, Any]] | None = None,
        status: dict[str, Any] | None = None,
        response: dict[str, Any] | None = None,
    ):
        self.agents = [{"name": "backend", "role": "API developer"}] if agents is None else agents
        self.status = {"healthy": True, "agents": 3} if status is None else status
        self.response = {"response": "Hello from agent"} if response is None else response
        self.last_message: str | None = None
        self.last_agent: str | None = None
        self.last_conversation_id: str | None = None

    async def send_message(
        self, message: str, *, agent: str | None = None, conversation_id: str | None = None
    ) -> dict[str, Any]:
        self.last_message = message
        self.last_agent = agent
        self.last_conversation_id = conversation_id
        return self.response

    async def list_agents(self) -> list[dict[str, Any]]:
        return self.agents

    async def get_status(self) -> dict[str, Any]:
        return self.status


def _make_update(user_id: int = 111, chat_id: int = 222, text: str = "hello"):
    """Create a fake Telegram Update-like object."""
    update = MagicMock()
    update.effective_user = SimpleNamespace(id=user_id)
    update.effective_chat = SimpleNamespace(id=chat_id)
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTelegramBotInit:
    def test_init_basic(self):
        client = FakeOrchestratorClient()
        bot = TelegramBot(client, bot_token="TOKEN123")
        assert bot.bot_token == "TOKEN123"
        assert bot.default_agent == "backend"
        assert bot.allowed_user_ids == []
        assert bot.running is False

    def test_init_with_allowed_users(self):
        client = FakeOrchestratorClient()
        bot = TelegramBot(client, bot_token="T", allowed_user_ids=[1, 2, 3])
        assert bot.allowed_user_ids == [1, 2, 3]

    def test_init_custom_agent(self):
        client = FakeOrchestratorClient()
        bot = TelegramBot(client, bot_token="T", default_agent="ai-engineer")
        assert bot.default_agent == "ai-engineer"


class TestAuthCheck:
    async def test_allows_when_no_restrictions(self):
        client = FakeOrchestratorClient()
        bot = TelegramBot(client, bot_token="T")
        update = _make_update(user_id=999)
        assert await bot._check_auth(update) is True

    async def test_allows_authorized_user(self):
        client = FakeOrchestratorClient()
        bot = TelegramBot(client, bot_token="T", allowed_user_ids=[111])
        update = _make_update(user_id=111)
        assert await bot._check_auth(update) is True

    async def test_blocks_unauthorized_user(self):
        client = FakeOrchestratorClient()
        bot = TelegramBot(client, bot_token="T", allowed_user_ids=[111])
        update = _make_update(user_id=999)
        assert await bot._check_auth(update) is False

    async def test_blocks_missing_user(self):
        client = FakeOrchestratorClient()
        bot = TelegramBot(client, bot_token="T", allowed_user_ids=[111])
        update = MagicMock()
        update.effective_user = None
        assert await bot._check_auth(update) is False


class TestSendChunked:
    async def test_short_message_single_call(self):
        update = _make_update()
        bot = TelegramBot(FakeOrchestratorClient(), bot_token="T")
        await bot._send_chunked(update, "short text")
        update.message.reply_text.assert_called_once_with("short text")

    async def test_empty_message_sends_placeholder(self):
        update = _make_update()
        bot = TelegramBot(FakeOrchestratorClient(), bot_token="T")
        await bot._send_chunked(update, "")
        update.message.reply_text.assert_called_once_with("(empty response)")

    async def test_long_message_chunked_at_4096(self):
        update = _make_update()
        bot = TelegramBot(FakeOrchestratorClient(), bot_token="T")
        long_text = "A" * 8200
        await bot._send_chunked(update, long_text)
        calls = update.message.reply_text.call_args_list
        # Should be 3 chunks: 4096 + 4096 + 8
        assert len(calls) == 3
        assert len(calls[0][0][0]) == 4096
        assert len(calls[1][0][0]) == 4096
        assert len(calls[2][0][0]) == 8

    async def test_custom_chunk_size(self):
        update = _make_update()
        bot = TelegramBot(FakeOrchestratorClient(), bot_token="T")
        await bot._send_chunked(update, "ABCDEFGHIJ", chunk_size=3)
        calls = update.message.reply_text.call_args_list
        # 10 chars / 3 = 4 chunks (3+3+3+1)
        assert len(calls) == 4


class TestConversationIdMapping:
    def test_creates_conversation_id(self):
        bot = TelegramBot(FakeOrchestratorClient(), bot_token="T")
        cid = bot._get_conversation_id(42)
        assert cid == "tg-42"

    def test_returns_same_id_for_same_chat(self):
        bot = TelegramBot(FakeOrchestratorClient(), bot_token="T")
        cid1 = bot._get_conversation_id(42)
        cid2 = bot._get_conversation_id(42)
        assert cid1 == cid2

    def test_different_chats_different_ids(self):
        bot = TelegramBot(FakeOrchestratorClient(), bot_token="T")
        cid1 = bot._get_conversation_id(1)
        cid2 = bot._get_conversation_id(2)
        assert cid1 != cid2


class TestCommandStart:
    async def test_start_authorized(self):
        bot = TelegramBot(FakeOrchestratorClient(), bot_token="T")
        update = _make_update()
        await bot._cmd_start(update, None)
        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "Welcome" in text

    async def test_start_unauthorized(self):
        bot = TelegramBot(FakeOrchestratorClient(), bot_token="T", allowed_user_ids=[999])
        update = _make_update(user_id=111)
        await bot._cmd_start(update, None)
        update.message.reply_text.assert_called_once_with("Unauthorized.")


class TestCommandNew:
    async def test_new_resets_conversation(self):
        client = FakeOrchestratorClient()
        bot = TelegramBot(client, bot_token="T")
        # Establish a conversation first
        bot._get_conversation_id(222)
        old_cid = bot._conversations[222]
        update = _make_update(chat_id=222)
        await bot._cmd_new(update, None)
        new_cid = bot._conversations[222]
        assert new_cid != old_cid
        assert "new" in new_cid


class TestCommandStatus:
    async def test_status_success(self):
        client = FakeOrchestratorClient(status={"healthy": True, "uptime": "2h"})
        bot = TelegramBot(client, bot_token="T")
        update = _make_update()
        await bot._cmd_status(update, None)
        text = update.message.reply_text.call_args[0][0]
        assert "healthy" in text

    async def test_status_error(self):
        client = FakeOrchestratorClient()
        client.get_status = AsyncMock(side_effect=RuntimeError("down"))
        bot = TelegramBot(client, bot_token="T")
        update = _make_update()
        await bot._cmd_status(update, None)
        text = update.message.reply_text.call_args[0][0]
        assert "Error" in text


class TestCommandAgents:
    async def test_agents_list(self):
        client = FakeOrchestratorClient(
            agents=[
                {"name": "backend", "role": "API"},
                {"name": "frontend", "role": "UI"},
            ]
        )
        bot = TelegramBot(client, bot_token="T")
        update = _make_update()
        await bot._cmd_agents(update, None)
        text = update.message.reply_text.call_args[0][0]
        assert "backend" in text
        assert "frontend" in text

    async def test_agents_empty(self):
        client = FakeOrchestratorClient(agents=[])
        bot = TelegramBot(client, bot_token="T")
        update = _make_update()
        await bot._cmd_agents(update, None)
        text = update.message.reply_text.call_args[0][0]
        assert "No agents" in text


class TestCommandHelp:
    async def test_help_shows_commands(self):
        bot = TelegramBot(FakeOrchestratorClient(), bot_token="T")
        update = _make_update()
        await bot._cmd_help(update, None)
        text = update.message.reply_text.call_args[0][0]
        assert "/start" in text
        assert "/new" in text
        assert "/status" in text
        assert "/agents" in text
        assert "/help" in text


class TestHandleMessage:
    async def test_routes_to_agent(self):
        client = FakeOrchestratorClient(response={"response": "done!"})
        bot = TelegramBot(client, bot_token="T", default_agent="ai-engineer")
        update = _make_update(chat_id=42, text="build a feature")
        await bot._handle_message(update, None)

        assert client.last_message == "build a feature"
        assert client.last_agent == "ai-engineer"
        assert client.last_conversation_id == "tg-42"
        text = update.message.reply_text.call_args[0][0]
        assert text == "done!"

    async def test_message_unauthorized(self):
        bot = TelegramBot(FakeOrchestratorClient(), bot_token="T", allowed_user_ids=[999])
        update = _make_update(user_id=111, text="hello")
        await bot._handle_message(update, None)
        update.message.reply_text.assert_called_once_with("Unauthorized.")

    async def test_message_error(self):
        client = FakeOrchestratorClient()
        client.send_message = AsyncMock(side_effect=RuntimeError("boom"))
        bot = TelegramBot(client, bot_token="T")
        update = _make_update(text="hello")
        await bot._handle_message(update, None)
        text = update.message.reply_text.call_args[0][0]
        assert "Error" in text


class TestLifecycle:
    async def test_start_sets_running(self):
        bot = TelegramBot(FakeOrchestratorClient(), bot_token="T")
        assert bot.running is False
        await bot.start()
        assert bot.running is True

    async def test_stop_clears_running(self):
        bot = TelegramBot(FakeOrchestratorClient(), bot_token="T")
        await bot.start()
        await bot.stop()
        assert bot.running is False

    async def test_stop_idempotent(self):
        bot = TelegramBot(FakeOrchestratorClient(), bot_token="T")
        # stop without start should not raise
        await bot.stop()
        assert bot.running is False
