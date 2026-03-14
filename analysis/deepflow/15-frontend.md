# 15 - Frontend

## Tech Stack

- **Next.js 16** (App Router, Turbo)
- **React 19**
- **TypeScript**
- **Tailwind CSS 4**
- **Radix UI** for accessible components
- **pnpm** package manager
- **better-auth** for authentication

## Architecture

```
frontend/src/
├── app/                    # Next.js App Router pages
│   ├── workspace/          # Main workspace
│   │   ├── chats/          # Chat threads
│   │   ├── agents/         # Agent gallery/creation
│   │   └── layout.tsx
│   └── mock/api/           # Mock API routes for demo
├── core/                   # Business logic (hooks + API)
│   ├── agents/             # Agent CRUD + hooks
│   ├── threads/            # Thread management
│   ├── memory/             # Memory read/write
│   ├── skills/             # Skills management
│   ├── mcp/                # MCP config
│   ├── models/             # Model selection
│   ├── uploads/            # File uploads
│   ├── artifacts/          # Output files
│   ├── tasks/              # Sub-task tracking
│   ├── todos/              # Plan mode todos
│   ├── api/                # API client + streaming
│   ├── i18n/               # Internationalization (en-US, zh-CN)
│   ├── settings/           # User preferences
│   └── streamdown/         # Markdown streaming
├── components/
│   ├── ui/                 # ~40 base components (shadcn/radix)
│   ├── workspace/          # Workspace-specific components
│   ├── ai-elements/        # AI-specific UI components
│   └── landing/            # Landing page sections
└── server/
    └── better-auth/        # Auth configuration
```

## Key UI Components

### AI Elements (`components/ai-elements/`)
- `conversation.tsx` — Full conversation view
- `message.tsx` — Message rendering
- `plan.tsx` — Plan/todo visualization
- `reasoning.tsx` — Thinking/reasoning display
- `sources.tsx` — Citation display
- `code-block.tsx` — Code with syntax highlighting
- `web-preview.tsx` — Inline web preview
- `canvas.tsx` — Graph/flow visualization
- `model-selector.tsx` — Model picker

### Workspace
- `chat-box.tsx` — Main chat interface
- `input-box.tsx` — Message input
- `artifacts/` — File viewer/download
- `messages/` — Message list with markdown
- `settings/` — Settings dialog
- `workspace-sidebar.tsx` — Navigation

## Streaming Architecture

Uses `@langchain/langgraph-sdk` for SSE streaming:
```typescript
// core/api/stream-mode.ts
// Handles LangGraph SSE protocol: values, messages-tuple, end
```

Custom `streamdown` library for progressive markdown rendering.

## Internationalization

Full i18n support:
- `en-US` (English)
- `zh-CN` (Chinese)
- Cookie-based locale persistence
- Server-side locale detection

## Demo Mode

Mock API routes serve pre-recorded conversations:
```
frontend/public/demo/threads/{thread_id}/thread.json
```

Allows showcasing DeerFlow without a running backend.

## Comparison with Our Dashboard

| Aspect | DeerFlow Frontend | Our Dashboard |
|--------|------------------|---------------|
| Framework | Next.js 16 (SSR) | Vanilla HTML/CSS/JS |
| Complexity | ~200 components | ~10 HTML files |
| Streaming | LangGraph SDK SSE | WebSocket + fetch |
| Auth | better-auth (OAuth) | OAuth2 + API keys |
| i18n | Yes (en/zh) | No |
| Demo mode | Yes (mock API) | No |
| Code editor | CodeMirror | highlight.js |
| Graph viz | XYFlow | None |

DeerFlow's frontend is significantly more sophisticated — a full SaaS-quality UI vs our utilitarian dashboard.
