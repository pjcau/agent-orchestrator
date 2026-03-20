# 15 - Plugin System

## Overview

Paperclip has a comprehensive plugin system with ~20 dedicated service modules. Plugins run as isolated worker processes, communicate via RPC, and can subscribe to events, schedule jobs, register tools, serve UI panels, and handle webhooks.

## Plugin Lifecycle State Machine

```
installed ──→ ready ──→ disabled
    │            │         │
    │            ├──→ error │
    │            ↓         │
    │     upgrade_pending  │
    │            │         │
    ↓            ↓         ↓
          uninstalled
```

Transitions are validated. Moving to `ready` starts the worker. Moving out of `ready` stops it gracefully.

## Plugin SDK

```typescript
// Plugin authors write this:
import { definePlugin } from "@paperclipai/plugin-sdk";

export default definePlugin({
  async setup(ctx) {
    ctx.events.on("issue.created", async (event) => {
      // React to events
    });

    ctx.jobs.register("full-sync", async (job) => {
      // Scheduled work
    });

    ctx.data.register("health", async ({ companyId }) => {
      // Serve data to UI
    });
  },

  async onHealth() {
    return { status: "ok" };
  },

  async onWebhook(input) {
    // Handle inbound webhooks
  },
});
```

## PluginContext

Plugins receive a rich context:

| API | Purpose |
|-----|---------|
| `ctx.events` | Subscribe to domain events |
| `ctx.jobs` | Register/schedule job handlers |
| `ctx.data` | Register data handlers for UI |
| `ctx.state` | Per-scope key-value state store |
| `ctx.config` | Plugin configuration |
| `ctx.secrets` | Resolve secrets by reference |
| `ctx.http` | HTTP client |
| `ctx.logger` | Structured logging |

## Server-Side Architecture

The plugin system spans ~20 service modules:

| Module | Responsibility |
|--------|---------------|
| `plugin-loader` | Scan dirs, load manifests, initialize |
| `plugin-registry` | DB-backed plugin metadata |
| `plugin-lifecycle` | State machine transitions |
| `plugin-worker-manager` | Child process management |
| `plugin-event-bus` | Route domain events to plugins |
| `plugin-job-scheduler` | Cron-like job scheduling |
| `plugin-job-store` | Persist job runs |
| `plugin-job-coordinator` | Coordinate scheduler + lifecycle |
| `plugin-tool-dispatcher` | Plugin-registered tool execution |
| `plugin-tool-registry` | Tool registration |
| `plugin-host-services` | DB/secret/config access for plugins |
| `plugin-host-service-cleanup` | Resource cleanup on unload |
| `plugin-state-store` | Scoped key-value persistence |
| `plugin-stream-bus` | Stream data from plugins |
| `plugin-secrets-handler` | Secret resolution for plugins |
| `plugin-config-validator` | Config validation |
| `plugin-capability-validator` | Capability checking |
| `plugin-manifest-validator` | Manifest schema validation |
| `plugin-runtime-sandbox` | Isolation enforcement |
| `plugin-log-retention` | Log cleanup/rotation |
| `plugin-dev-watcher` | Hot-reload during development |

## Worker Process Model

Each plugin runs in a separate Node.js worker process. Communication is via JSON-RPC over stdio:

```
Host Process ←── JSON-RPC (stdio) ──→ Worker Process
     │                                      │
     ├─ lifecycle commands                  ├─ setup()
     ├─ event delivery                      ├─ event handlers
     ├─ job dispatch                        ├─ job handlers
     └─ tool invocation                     └─ tool handlers
```

## Plugin UI

Plugins can serve custom UI panels. Static assets are served via `plugin-ui-static.ts` at `/api/plugins/:id/ui/`.

## Plugin Examples

| Example | What it demonstrates |
|---------|---------------------|
| hello-world | Minimal plugin with setup |
| file-browser | UI panel, data handlers |
| kitchen-sink | All capabilities |
| authoring-smoke | Testing patterns |

## Key Patterns
- Full process isolation per plugin (security + crash containment)
- Rich SDK with events, jobs, state, tools, UI
- State machine for lifecycle (no undefined states)
- ~20 modules show thorough decomposition
- Dev watcher for hot-reload during development

## Relevance to Our Project
Our `PluginLoader` is minimal — it reads manifests and registers skills/providers. Paperclip's plugin system is a full extension platform with its own event bus, job scheduler, tool registry, and UI panels. The process isolation model is important for security. We could adopt the state machine pattern for our plugin lifecycle and the event-based integration (plugins reacting to domain events) instead of our current direct registration approach.
