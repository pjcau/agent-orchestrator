# 13 - Real-time System

## Overview

Paperclip uses WebSocket for real-time event delivery. Each company has an isolated event stream. The system is simple but effective.

## Architecture

```
Service Layer
    │
    ├─ publishLiveEvent({ companyId, type, payload })
    │        │
    │        ▼
    │  EventEmitter (in-process)
    │        │
    │        ▼
    │  subscribeCompanyLiveEvents(companyId, listener)
    │        │
    │        ▼
    └─ WebSocket Server → Browser Clients
```

## Live Events Service

```typescript
// server/src/services/live-events.ts
const emitter = new EventEmitter();
emitter.setMaxListeners(0); // unlimited listeners

export function publishLiveEvent(input) {
  const event = toLiveEvent(input);
  emitter.emit(input.companyId, event);
}

export function subscribeCompanyLiveEvents(companyId, listener) {
  emitter.on(companyId, listener);
  return () => emitter.off(companyId, listener);
}
```

This is intentionally simple: in-process EventEmitter, no Redis, no external pub/sub. Works because Paperclip is single-process.

## WebSocket Server

```typescript
// server/src/realtime/live-events-ws.ts
const wss = new WebSocketServer({ noServer: true });

// Auth on upgrade
server.on("upgrade", (req, socket, head) => {
  const companyId = parseCompanyId(url.pathname);
  authorizeUpgrade(db, req, companyId, url, opts)
    .then(context => {
      wss.handleUpgrade(req, socket, head, ws => {
        wss.emit("connection", ws, req);
      });
    });
});

// On connection, subscribe to company events
wss.on("connection", (socket, req) => {
  const unsubscribe = subscribeCompanyLiveEvents(companyId, event => {
    socket.send(JSON.stringify(event));
  });
  // Cleanup on close
});
```

## Authentication

WebSocket auth supports:
1. **Bearer token** (API key for agents)
2. **Query parameter token** (`?token=...` for browser workaround)
3. **Session cookie** (for board users via better-auth)
4. **Implicit** (local_trusted mode — no auth needed)

## Keepalive

```typescript
const pingInterval = setInterval(() => {
  for (const socket of wss.clients) {
    if (!aliveByClient.get(socket)) {
      socket.terminate(); // dead client
      continue;
    }
    aliveByClient.set(socket, false);
    socket.ping();
  }
}, 30000); // 30s ping cycle
```

## Event Types

Events include: agent status changes, heartbeat start/complete, cost events, issue updates, approval decisions, budget alerts, plugin events, etc.

## Key Patterns
- Simple in-process EventEmitter (no Redis needed for single-process)
- Company-isolated event streams (no cross-company leaks)
- WebSocket with proper auth on upgrade
- Ping/pong keepalive for dead client detection
- Monotonic event IDs for ordering

## Relevance to Our Project
Our dashboard uses WebSocket for streaming too, but with a different event bus (`EventBus` class). Paperclip's approach of using Node's native EventEmitter is simpler. The per-company isolation is cleaner than our per-session approach. The auth-on-upgrade pattern is important for security — our WebSocket doesn't authenticate.
