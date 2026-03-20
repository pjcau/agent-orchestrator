# 06 - Adapter System

## Overview

Adapters are Paperclip's abstraction for agent runtimes. Each adapter knows how to execute a task using a specific AI tool (Claude Code, Codex, Cursor, etc.). The adapter registry provides a uniform interface regardless of the underlying runtime.

## Adapter Registry

```typescript
// server/src/adapters/registry.ts
const adaptersByType = new Map<string, ServerAdapterModule>([
  claudeLocalAdapter,    // claude_local
  codexLocalAdapter,     // codex_local
  openCodeLocalAdapter,  // opencode_local
  piLocalAdapter,        // pi_local
  cursorLocalAdapter,    // cursor
  geminiLocalAdapter,    // gemini_local
  openclawGatewayAdapter, // openclaw_gateway
  hermesLocalAdapter,    // hermes_local
  processAdapter,        // process (generic CLI)
  httpAdapter,           // http (generic HTTP)
].map(a => [a.type, a]));
```

Fallback: unknown adapter types fall back to the `processAdapter` (generic CLI execution).

## Adapter Interface

```typescript
interface ServerAdapterModule {
  type: string;
  execute: (context: AdapterExecutionContext) => AdapterExecutionResult;
  testEnvironment: (context) => AdapterEnvironmentTestResult;
  listSkills?: () => AdapterSkillEntry[];
  syncSkills?: (skills: AdapterSkillSnapshot[]) => void;
  sessionCodec?: AdapterSessionCodec;
  sessionManagement?: AdapterSessionManagement;
  models: AdapterModel[];
  listModels?: () => Promise<AdapterModel[]>;
  supportsLocalAgentJwt: boolean;
  agentConfigurationDoc?: string;
  getQuotaWindows?: () => QuotaWindow[];
}
```

Each adapter provides:
- **execute** — Run a task with the agent runtime
- **testEnvironment** — Verify the runtime is installed and configured
- **listSkills / syncSkills** — Skill discovery and synchronization
- **sessionCodec** — Encode/decode session state for persistence
- **models** — Available models for the adapter

## Adapter Types

### Local Adapters (process-based)
- `claude_local` — Spawns Claude Code CLI
- `codex_local` — Spawns OpenAI Codex CLI
- `cursor_local` — Spawns Cursor agent
- `gemini_local` — Spawns Gemini CLI
- `opencode_local` — Spawns OpenCode CLI
- `pi_local` — Spawns Pi CLI

### Gateway Adapters
- `openclaw_gateway` — Connects to OpenClaw server via API
- `hermes_local` — Hermes adapter

### Generic Adapters
- `process` — Arbitrary CLI process execution
- `http` — HTTP API-based agent execution

## Execution Context

```typescript
interface AdapterExecutionContext {
  agent: AdapterAgent;
  runtime: AdapterRuntime;
  invocationMeta: AdapterInvocationMeta;
  // env vars, working directory, secrets, skills, JWT...
}
```

## Skill Synchronization

Adapters can sync skills from the Paperclip company skill registry:
```
Company Skills DB → syncSkills() → Adapter-specific skill format
```

For Claude Code, this means writing SKILL.md files to the agent's `.claude/skills/` directory. Each adapter translates skills to its native format.

## Key Patterns
- Adapter as a bridge between Paperclip's company model and specific agent runtimes
- Uniform execute/test/skills interface across all adapters
- Session codec for transparent state persistence across heartbeats
- Fallback to generic process adapter for unknown types
- Separate workspace packages per adapter (good isolation)

## Relevance to Our Project
Our `Provider` abstraction handles LLM API calls. Paperclip's adapter is broader — it manages the entire agent runtime lifecycle (spawn process, inject context, stream output, parse results). Our providers are stateless API clients; their adapters are stateful process managers. The skill synchronization pattern (central registry → per-adapter format) is worth adopting — our skills are currently only in Claude Code format.
