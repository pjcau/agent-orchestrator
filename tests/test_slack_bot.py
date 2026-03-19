"""Tests for the Slack bot integration.

Tests cover:
- Bot initialization with tokens
- app_mention handler extracts task from message
- /agent command handler (with and without explicit agent)
- /team command handler
- thread_ts -> conversation_id mapping
- Category auto-detection from message
- Response truncation at max_response_length
- Graceful handling when orchestrator returns error
- Bot stop/cleanup
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_orchestrator.integrations.slack_bot import (
    SlackBot,
    SlackBotConfig,
    _extract_task_from_mention,
    _thread_to_conversation_id,
    _truncate,
    detect_category,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeOrchestratorClient:
    """Fake orchestrator client implementing the OrchestratorClient protocol."""

    def __init__(self, output: str = "Task completed successfully."):
        self.output = output
        self.calls: list[dict[str, Any]] = []
        self.should_fail = False
        self.error_message = "Orchestrator unavailable"

    async def run_agent(
        self,
        task: str,
        agent: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "method": "run_agent",
                "task": task,
                "agent": agent,
                "conversation_id": conversation_id,
            }
        )
        if self.should_fail:
            raise RuntimeError(self.error_message)
        return {"output": self.output, "success": True}

    async def run_team(
        self,
        task: str,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "method": "run_team",
                "task": task,
                "conversation_id": conversation_id,
            }
        )
        if self.should_fail:
            raise RuntimeError(self.error_message)
        return {"output": self.output, "success": True}


@pytest.fixture
def fake_client() -> FakeOrchestratorClient:
    return FakeOrchestratorClient()


@pytest.fixture
def bot_config() -> SlackBotConfig:
    return SlackBotConfig(
        bot_token="xoxb-test-token",
        app_token="xapp-test-token",
        default_agent="backend",
        max_response_length=3000,
    )


@pytest.fixture
def bot(fake_client: FakeOrchestratorClient, bot_config: SlackBotConfig) -> SlackBot:
    return SlackBot(orchestrator_client=fake_client, config=bot_config)


# ---------------------------------------------------------------------------
# Test: Bot initialization
# ---------------------------------------------------------------------------


class TestBotInitialization:
    def test_init_with_config(self, fake_client: FakeOrchestratorClient) -> None:
        config = SlackBotConfig(
            bot_token="xoxb-123",
            app_token="xapp-456",
            default_agent="frontend",
            max_response_length=2000,
        )
        bot = SlackBot(orchestrator_client=fake_client, config=config)
        assert bot.config.bot_token == "xoxb-123"
        assert bot.config.app_token == "xapp-456"
        assert bot.config.default_agent == "frontend"
        assert bot.config.max_response_length == 2000
        assert bot.client is fake_client

    def test_init_with_token_kwargs(self, fake_client: FakeOrchestratorClient) -> None:
        bot = SlackBot(
            orchestrator_client=fake_client,
            bot_token="xoxb-abc",
            app_token="xapp-def",
        )
        assert bot.config.bot_token == "xoxb-abc"
        assert bot.config.app_token == "xapp-def"

    def test_init_defaults(self, fake_client: FakeOrchestratorClient) -> None:
        bot = SlackBot(orchestrator_client=fake_client)
        assert bot.config.bot_token == ""
        assert bot.config.app_token == ""
        assert bot.config.default_agent == "backend"
        assert bot.config.max_response_length == 3000
        assert not bot.is_running


# ---------------------------------------------------------------------------
# Test: Task extraction from @mention
# ---------------------------------------------------------------------------


class TestMentionExtraction:
    def test_extract_task_basic(self) -> None:
        assert _extract_task_from_mention("<@U123> build me an API") == "build me an API"

    def test_extract_task_no_mention(self) -> None:
        assert _extract_task_from_mention("build me an API") == "build me an API"

    def test_extract_task_multiple_mentions(self) -> None:
        result = _extract_task_from_mention("<@U123> <@U456> build API")
        assert result == "build API"

    def test_extract_task_empty_after_mention(self) -> None:
        assert _extract_task_from_mention("<@U123>") == ""

    def test_extract_task_whitespace(self) -> None:
        assert _extract_task_from_mention("<@U123>   deploy the app  ") == "deploy the app"


# ---------------------------------------------------------------------------
# Test: app_mention handler
# ---------------------------------------------------------------------------


class TestMentionHandler:
    async def test_mention_routes_to_agent(
        self, bot: SlackBot, fake_client: FakeOrchestratorClient
    ) -> None:
        say = AsyncMock()
        event = {
            "text": "<@U123> build a REST API",
            "channel": "C001",
            "ts": "1234567890.000001",
        }
        await bot._handle_mention(event, say)

        assert len(fake_client.calls) == 1
        call = fake_client.calls[0]
        assert call["method"] == "run_agent"
        assert call["task"] == "build a REST API"
        assert call["agent"] == "backend"  # software-engineering default
        assert call["conversation_id"] == "slack-C001-1234567890.000001"
        say.assert_called_once()

    async def test_mention_uses_thread_ts(
        self, bot: SlackBot, fake_client: FakeOrchestratorClient
    ) -> None:
        say = AsyncMock()
        event = {
            "text": "<@U123> analyze stock data",
            "channel": "C001",
            "ts": "1234567890.000001",
            "thread_ts": "1234567890.000000",
        }
        await bot._handle_mention(event, say)

        call = fake_client.calls[0]
        assert call["conversation_id"] == "slack-C001-1234567890.000000"
        # Finance category -> financial-analyst
        assert call["agent"] == "financial-analyst"

    async def test_mention_empty_task(self, bot: SlackBot) -> None:
        say = AsyncMock()
        event = {"text": "<@U123>", "channel": "C001", "ts": "123.001"}
        await bot._handle_mention(event, say)

        say.assert_called_once()
        call_kwargs = say.call_args
        assert (
            "provide a task"
            in call_kwargs.kwargs.get(
                "text", call_kwargs.args[0] if call_kwargs.args else ""
            ).lower()
        )


# ---------------------------------------------------------------------------
# Test: /agent command handler
# ---------------------------------------------------------------------------


class TestAgentCommand:
    async def test_agent_command_basic(
        self, bot: SlackBot, fake_client: FakeOrchestratorClient
    ) -> None:
        respond = AsyncMock()
        command = {"text": "fix the login bug", "channel_id": "C001"}
        await bot._handle_agent_command(command, respond)

        assert len(fake_client.calls) == 1
        call = fake_client.calls[0]
        assert call["method"] == "run_agent"
        assert call["task"] == "fix the login bug"
        respond.assert_called_once()

    async def test_agent_command_explicit_agent(
        self, bot: SlackBot, fake_client: FakeOrchestratorClient
    ) -> None:
        respond = AsyncMock()
        command = {"text": "frontend build a dashboard", "channel_id": "C001"}
        await bot._handle_agent_command(command, respond)

        call = fake_client.calls[0]
        assert call["agent"] == "frontend"
        assert call["task"] == "build a dashboard"

    async def test_agent_command_empty_text(self, bot: SlackBot) -> None:
        respond = AsyncMock()
        command = {"text": "", "channel_id": "C001"}
        await bot._handle_agent_command(command, respond)

        respond.assert_called_once()
        assert "Usage" in respond.call_args.args[0]

    async def test_agent_command_unknown_first_word(
        self, bot: SlackBot, fake_client: FakeOrchestratorClient
    ) -> None:
        """When first word is not a known agent, treat entire text as task."""
        respond = AsyncMock()
        command = {"text": "refactor the authentication module", "channel_id": "C001"}
        await bot._handle_agent_command(command, respond)

        call = fake_client.calls[0]
        assert call["task"] == "refactor the authentication module"
        # No category keywords match -> falls back to software-engineering -> backend
        assert call["agent"] == "backend"


# ---------------------------------------------------------------------------
# Test: /team command handler
# ---------------------------------------------------------------------------


class TestTeamCommand:
    async def test_team_command_basic(
        self, bot: SlackBot, fake_client: FakeOrchestratorClient
    ) -> None:
        respond = AsyncMock()
        command = {"text": "build a complete e-commerce site", "channel_id": "C002"}
        await bot._handle_team_command(command, respond)

        assert len(fake_client.calls) == 1
        call = fake_client.calls[0]
        assert call["method"] == "run_team"
        assert call["task"] == "build a complete e-commerce site"
        assert call["conversation_id"] == "slack-C002-0"
        respond.assert_called_once()

    async def test_team_command_empty_text(self, bot: SlackBot) -> None:
        respond = AsyncMock()
        command = {"text": "", "channel_id": "C002"}
        await bot._handle_team_command(command, respond)

        respond.assert_called_once()
        assert "Usage" in respond.call_args.args[0]


# ---------------------------------------------------------------------------
# Test: Thread-to-conversation ID mapping
# ---------------------------------------------------------------------------


class TestThreadMapping:
    def test_with_thread_ts(self) -> None:
        cid = _thread_to_conversation_id("C001", "1234567890.000000")
        assert cid == "slack-C001-1234567890.000000"

    def test_without_thread_ts(self) -> None:
        cid = _thread_to_conversation_id("C001", None)
        assert cid == "slack-C001-0"

    def test_different_channels(self) -> None:
        cid1 = _thread_to_conversation_id("C001", "123.001")
        cid2 = _thread_to_conversation_id("C002", "123.001")
        assert cid1 != cid2

    def test_different_threads(self) -> None:
        cid1 = _thread_to_conversation_id("C001", "123.001")
        cid2 = _thread_to_conversation_id("C001", "123.002")
        assert cid1 != cid2


# ---------------------------------------------------------------------------
# Test: Category auto-detection
# ---------------------------------------------------------------------------


class TestCategoryDetection:
    def test_finance_keywords(self) -> None:
        assert detect_category("analyze our stock portfolio performance") == "finance"

    def test_data_science_keywords(self) -> None:
        assert detect_category("train a machine learning model on this dataset") == "data-science"

    def test_marketing_keywords(self) -> None:
        assert detect_category("create an SEO campaign for our brand") == "marketing"

    def test_software_engineering_fallback(self) -> None:
        assert detect_category("build a REST API with authentication") == "software-engineering"

    def test_empty_text_fallback(self) -> None:
        assert detect_category("") == "software-engineering"

    def test_mixed_keywords_highest_wins(self) -> None:
        # "stock portfolio risk trading" -> 4 finance keywords
        result = detect_category("stock portfolio risk trading")
        assert result == "finance"


# ---------------------------------------------------------------------------
# Test: Response truncation
# ---------------------------------------------------------------------------


class TestTruncation:
    def test_no_truncation_needed(self) -> None:
        assert _truncate("short text", 100) == "short text"

    def test_exact_length(self) -> None:
        text = "a" * 3000
        assert _truncate(text, 3000) == text

    def test_truncation_applied(self) -> None:
        text = "a" * 4000
        result = _truncate(text, 3000)
        assert len(result) == 3000
        assert result.endswith("...")

    def test_truncation_preserves_prefix(self) -> None:
        text = "Hello World! " * 300
        result = _truncate(text, 50)
        assert len(result) == 50
        assert result.startswith("Hello World!")

    async def test_truncation_in_mention_handler(self, fake_client: FakeOrchestratorClient) -> None:
        """Verify that bot responses are actually truncated."""
        fake_client.output = "x" * 5000
        config = SlackBotConfig(
            bot_token="xoxb-test",
            app_token="xapp-test",
            max_response_length=100,
        )
        bot = SlackBot(orchestrator_client=fake_client, config=config)
        say = AsyncMock()
        event = {"text": "<@U123> do something", "channel": "C001", "ts": "1.0"}
        await bot._handle_mention(event, say)

        response_text = say.call_args.kwargs["text"]
        assert len(response_text) == 100
        assert response_text.endswith("...")


# ---------------------------------------------------------------------------
# Test: Graceful error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    async def test_mention_error_returns_message(
        self, bot: SlackBot, fake_client: FakeOrchestratorClient
    ) -> None:
        fake_client.should_fail = True
        say = AsyncMock()
        event = {"text": "<@U123> break something", "channel": "C001", "ts": "1.0"}
        await bot._handle_mention(event, say)

        response = say.call_args.kwargs["text"]
        assert "something went wrong" in response.lower()

    async def test_agent_command_error(
        self, bot: SlackBot, fake_client: FakeOrchestratorClient
    ) -> None:
        fake_client.should_fail = True
        respond = AsyncMock()
        command = {"text": "do something", "channel_id": "C001"}
        await bot._handle_agent_command(command, respond)

        response = respond.call_args.args[0]
        assert "something went wrong" in response.lower()

    async def test_team_command_error(
        self, bot: SlackBot, fake_client: FakeOrchestratorClient
    ) -> None:
        fake_client.should_fail = True
        respond = AsyncMock()
        command = {"text": "do something", "channel_id": "C001"}
        await bot._handle_team_command(command, respond)

        response = respond.call_args.args[0]
        assert "something went wrong" in response.lower()


# ---------------------------------------------------------------------------
# Test: Bot stop/cleanup
# ---------------------------------------------------------------------------


class TestBotLifecycle:
    async def test_stop_without_start(self, bot: SlackBot) -> None:
        """Calling stop before start should not raise."""
        await bot.stop()
        assert not bot.is_running

    async def test_stop_clears_state(self, bot: SlackBot) -> None:
        bot._running = True
        bot._handler = AsyncMock()
        bot._handler.close_async = AsyncMock()
        bot._app = MagicMock()

        await bot.stop()

        assert not bot.is_running
        assert bot._handler is None
        assert bot._app is None

    def test_ensure_app_import_error(self, bot: SlackBot) -> None:
        """If slack-bolt is not installed, _ensure_app raises ImportError."""
        with patch.dict("sys.modules", {"slack_bolt.async_app": None, "slack_bolt": None}):
            with patch("builtins.__import__", side_effect=ImportError("no slack_bolt")):
                with pytest.raises(ImportError, match="slack-bolt is required"):
                    bot._ensure_app()

    def test_is_running_default_false(self, bot: SlackBot) -> None:
        assert not bot.is_running
