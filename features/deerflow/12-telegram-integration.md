# Feature: Telegram Bot Integration

## Context

From DeerFlow analysis (analysis/deepflow/18-im-channels.md, 29-learnings.md L11).
Telegram Bot API with long-polling is the simplest IM integration — no public IP, minimal setup via BotFather. Enables mobile access to agents.

## What to Build

### 1. Telegram Bot

```python
# src/agent_orchestrator/integrations/telegram_bot.py

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

class TelegramBot:
    """Telegram bot using long-polling (no public IP required)."""

    def __init__(self, orchestrator_client: OrchestratorClient, bot_token: str,
                 allowed_user_ids: list[int] | None = None):
        self._client = orchestrator_client
        self._allowed_users = set(allowed_user_ids or [])
        self._app = Application.builder().token(bot_token).build()
        self._register_handlers()

    def _register_handlers(self):
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("new", self._cmd_new))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("agents", self._cmd_agents))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

    async def _check_auth(self, update: Update) -> bool:
        """Check if user is allowed. Empty allowed_users = allow all."""
        if not self._allowed_users:
            return True
        return update.effective_user.id in self._allowed_users

    async def _cmd_start(self, update: Update, context):
        await update.message.reply_text(
            "Agent Orchestrator Bot\n\n"
            "Send any message to get help from AI agents.\n"
            "Commands:\n"
            "/new — Start new conversation\n"
            "/status — Current session status\n"
            "/agents — List available agents\n"
            "/help — Show this help"
        )

    async def _cmd_new(self, update: Update, context):
        """Start a new conversation (clear context)."""
        ...

    async def _cmd_status(self, update: Update, context):
        """Show current session: active agent, tokens used, cost."""
        ...

    async def _cmd_agents(self, update: Update, context):
        """List available agents with descriptions."""
        agents = self._client.list_agents()
        lines = [f"• *{a.name}* ({a.category}): {a.description}" for a in agents]
        await update.message.reply_markdown_v2("\n".join(lines))

    async def _handle_message(self, update: Update, context):
        """Handle free-text messages — route to appropriate agent."""
        if not await self._check_auth(update):
            await update.message.reply_text("Unauthorized.")
            return

        task = update.message.text
        chat_id = update.effective_chat.id
        conversation_id = f"telegram-{chat_id}"

        # Send "typing" indicator
        await update.effective_chat.send_action("typing")

        result = await self._client.run_agent(
            agent="backend",  # or auto-detect
            task=task,
            conversation_id=conversation_id,
        )

        # Split long responses into chunks (Telegram 4096 char limit)
        await self._send_chunked(update, result.output)

    async def _send_chunked(self, update: Update, text: str, chunk_size: int = 4000):
        """Send long messages in chunks respecting Telegram's 4096 char limit."""
        for i in range(0, len(text), chunk_size):
            await update.message.reply_text(text[i:i+chunk_size])

    async def start(self):
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()

    async def stop(self):
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()
```

### 2. Configuration

```yaml
# orchestrator.yaml
integrations:
  telegram:
    enabled: true
    bot_token: "${TELEGRAM_BOT_TOKEN}"
    allowed_user_ids: [123456789]   # Empty list = allow all
    default_agent: "backend"
```

### 3. Dependencies

```toml
# pyproject.toml
[project.optional-dependencies]
telegram = [
    "python-telegram-bot>=21.0",
]
```

## Files to Modify

- **Create**: `src/agent_orchestrator/integrations/telegram_bot.py`
- **Modify**: `src/agent_orchestrator/integrations/__init__.py` (export TelegramBot)
- **Modify**: `src/agent_orchestrator/dashboard/app.py` (start Telegram bot on server startup if configured)
- **Modify**: `pyproject.toml` (add telegram optional dependencies)
- **Modify**: `src/agent_orchestrator/core/yaml_config.py` (add telegram config)

## Tests

- Test bot initialization
- Test /start, /new, /status, /agents, /help commands
- Test free-text message routes to agent
- Test auth check blocks unauthorized users
- Test auth check allows empty allowed list
- Test response chunking at 4096 chars
- Test conversation_id mapping from chat_id
- Test category auto-detection
- Test bot start/stop lifecycle

## Acceptance Criteria

- [ ] TelegramBot class with long-polling (no public IP)
- [ ] 5 commands: /start, /new, /status, /agents, /help
- [ ] Free-text message handling with agent routing
- [ ] User authorization (whitelist by Telegram user ID)
- [ ] Response chunking for long messages
- [ ] Multi-turn conversations per chat
- [ ] All tests pass
- [ ] Existing tests still pass
