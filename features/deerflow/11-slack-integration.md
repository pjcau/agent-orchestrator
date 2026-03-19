# Feature: Slack Integration (Socket Mode)

## Context

From DeerFlow analysis (analysis/deepflow/18-im-channels.md, 29-learnings.md L11).
Slack integration via Socket Mode requires no public IP — uses outbound WebSocket connection. Enables team workflows directly from Slack.

## What to Build

### 1. Slack Bot

```python
# src/agent_orchestrator/integrations/slack_bot.py

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

class SlackBot:
    """Slack bot using Socket Mode (no public IP required)."""

    def __init__(self, orchestrator_client: OrchestratorClient,
                 bot_token: str, app_token: str):
        self._client = orchestrator_client
        self._app = AsyncApp(token=bot_token)
        self._handler = AsyncSocketModeHandler(self._app, app_token)
        self._register_handlers()

    def _register_handlers(self):
        @self._app.event("app_mention")
        async def handle_mention(event, say):
            """Respond when @bot is mentioned."""
            task = event["text"].split(">", 1)[-1].strip()
            thread_ts = event.get("thread_ts", event["ts"])

            await say(f"Working on it...", thread_ts=thread_ts)
            result = await self._client.run_agent(
                agent="backend",  # or auto-detect category
                task=task,
            )
            await say(f"```\n{result.output[:3000]}\n```", thread_ts=thread_ts)

        @self._app.command("/agent")
        async def handle_command(ack, body, say):
            """Handle /agent slash command."""
            await ack()
            task = body["text"]
            result = await self._client.run_agent(agent="backend", task=task)
            await say(result.output[:3000])

        @self._app.command("/team")
        async def handle_team(ack, body, say):
            """Handle /team slash command for multi-agent tasks."""
            await ack()
            task = body["text"]
            await say("Team working on it...")
            result = await self._client.run_team(task=task)
            await say(f"*Team Result*\n```\n{result.summary[:3000]}\n```")

    async def start(self):
        await self._handler.start_async()

    async def stop(self):
        await self._handler.close_async()
```

### 2. Thread-Based Conversations

Map Slack threads to orchestrator conversation threads:

```python
# Thread mapping: Slack thread_ts → orchestrator conversation_id
# This enables multi-turn conversations in Slack threads

async def handle_message(event, say):
    thread_ts = event.get("thread_ts", event["ts"])
    conversation_id = f"slack-{event['channel']}-{thread_ts}"

    result = await self._client.run_agent(
        agent="backend",
        task=event["text"],
        conversation_id=conversation_id,  # Multi-turn context
    )
    await say(result.output[:3000], thread_ts=thread_ts)
```

### 3. Category Auto-Detection

Reuse existing `_detect_category()` to route Slack messages to the right agent:

```python
category = detect_category(task)
agents = get_category_agents(category)
result = await self._client.run_agent(agent=agents[0], task=task)
```

### 4. Configuration

```yaml
# orchestrator.yaml
integrations:
  slack:
    enabled: true
    bot_token: "${SLACK_BOT_TOKEN}"
    app_token: "${SLACK_APP_TOKEN}"
    default_agent: "backend"
    max_response_length: 3000
```

### 5. Dependencies

```toml
# pyproject.toml
[project.optional-dependencies]
slack = [
    "slack-bolt>=1.20",
    "slack-sdk>=3.33",
]
```

### 6. Dashboard Connection

- Show Slack-originated tasks in the dashboard activity feed
- Slack thread_ts stored in session metadata for traceability

## Files to Modify

- **Create**: `src/agent_orchestrator/integrations/slack_bot.py`
- **Create**: `src/agent_orchestrator/integrations/__init__.py`
- **Modify**: `src/agent_orchestrator/dashboard/app.py` (start Slack bot on server startup if configured)
- **Modify**: `pyproject.toml` (add slack optional dependencies)
- **Modify**: `src/agent_orchestrator/core/yaml_config.py` (add integrations section)

## Tests

- Test bot initialization with tokens
- Test app_mention handler extracts task from message
- Test /agent command handler
- Test /team command handler
- Test thread_ts → conversation_id mapping
- Test category auto-detection from message
- Test response truncation at max_response_length
- Test graceful handling when orchestrator returns error
- Test bot stop/cleanup

## Acceptance Criteria

- [ ] SlackBot class with Socket Mode (no public IP)
- [ ] @mention, /agent, /team handlers
- [ ] Thread-based multi-turn conversations
- [ ] Category auto-detection for agent routing
- [ ] Configuration via YAML/env vars
- [ ] Dashboard shows Slack-originated tasks
- [ ] All tests pass
- [ ] Existing tests still pass
