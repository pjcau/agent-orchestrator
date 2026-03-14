# 18 - IM Channels

## Overview

DeerFlow supports receiving tasks from messaging apps. All channels use outbound connections — no public IP required.

## Supported Channels

| Channel | Transport | Difficulty |
|---------|-----------|------------|
| Telegram | Bot API (long-polling) | Easy |
| Slack | Socket Mode | Moderate |
| Feishu/Lark | WebSocket | Moderate |

## Architecture

```
External Platform → Channel impl → MessageBus.publish_inbound()
                                        │
                          ChannelManager._dispatch_loop()
                                        │
                         ┌───────────────┤
                         │               │
                    Command          Chat Message
                    (handle locally)  (create/lookup thread)
                                        │
                              LangGraph Server
                              (runs.stream/wait)
                                        │
                              Outbound → Channel → Platform
```

## Components

| File | Purpose |
|------|---------|
| `message_bus.py` | Async pub/sub hub |
| `store.py` | JSON persistence (channel:chat → thread_id) |
| `manager.py` | Core dispatcher |
| `base.py` | Abstract Channel base class |
| `service.py` | Lifecycle management |
| `slack.py` | Slack implementation |
| `feishu.py` | Feishu/Lark implementation |
| `telegram.py` | Telegram implementation |

## Commands

| Command | Description |
|---------|-------------|
| `/new` | Start new conversation |
| `/status` | Show thread info |
| `/models` | List available models |
| `/memory` | View memory |
| `/help` | Show help |

## Per-User Session Configuration

```yaml
channels:
  telegram:
    session:
      assistant_id: mobile_agent
      users:
        "123456789":
          assistant_id: vip_agent
          config:
            recursion_limit: 150
          context:
            thinking_enabled: true
            subagent_enabled: true
```

## File Attachments

Channels support file uploads:
- Files downloaded from platform API
- Uploaded to DeerFlow via Gateway
- Available to agent via UploadsMiddleware

## Key Insight for Our Project

We don't have IM channel integration at all. DeerFlow's approach is clean:
- Channels auto-start when configured
- No public IP needed (all use outbound connections)
- Unified message bus pattern
- Per-user/per-channel session config

This could be valuable for our orchestrator — receiving tasks from Slack/Telegram/Teams without needing a web UI.
