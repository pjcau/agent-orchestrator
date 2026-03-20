# 14 - Frontend Architecture

## Overview

Paperclip's frontend is a React 19 SPA built with Vite 6 and Tailwind CSS 4. It uses TanStack Query for data fetching and Radix UI for accessible components.

## Tech Stack

| Library | Purpose |
|---------|---------|
| React 19 | UI framework |
| Vite 6 | Build + HMR |
| Tailwind CSS 4 | Utility-first styling |
| TanStack Query 5 | Server state management |
| React Router 7 | Client-side routing |
| Radix UI | Accessible UI primitives |
| cmdk | Command palette |
| Lucide | Icon library |
| @mdxeditor/editor | Rich text editing |
| mermaid | Diagram rendering |
| @dnd-kit | Drag and drop |

## Page Structure

```
ui/src/pages/
├── Dashboard.tsx          # Overview with metrics
├── Companies.tsx          # Company list/switcher
├── CompanySettings.tsx    # Company configuration
├── Agents.tsx             # Agent list
├── AgentDetail.tsx        # Agent config, runs, costs
├── Projects.tsx           # Project list
├── ProjectDetail.tsx      # Project issues, workspaces
├── Issues.tsx             # Issue board/list
├── IssueDetail.tsx        # Issue detail with comments
├── Goals.tsx              # Goal tree
├── GoalDetail.tsx         # Goal detail
├── Approvals.tsx          # Approval queue
├── ApprovalDetail.tsx     # Approval review
├── Costs.tsx              # Cost analytics
├── Activity.tsx           # Activity feed
├── CompanySkills.tsx      # Skill management
├── PluginManager.tsx      # Plugin management
├── InstanceSettings.tsx   # Global settings
├── Auth.tsx               # Login/signup
├── Inbox.tsx              # Notification inbox
├── OrgChart.tsx           # Org chart visualization
└── ... (30+ pages)
```

## Adapter System (UI)

The UI has its own adapter registry for agent configuration:

```
ui/src/adapters/
├── registry.ts             # Adapter registration
├── types.ts                # Adapter interface
├── claude-local/           # Claude Code config fields
├── codex-local/            # Codex config fields
├── cursor/                 # Cursor config fields
├── gemini-local/           # Gemini config fields
├── opencode-local/         # OpenCode config fields
├── openclaw-gateway/       # OpenClaw config fields
├── pi-local/               # Pi config fields
├── http/                   # HTTP adapter config
└── process/                # Process adapter config
```

Each adapter provides React components for its configuration UI.

## Context Providers

```
ui/src/context/
├── CompanyContext.tsx       # Current company
├── ThemeContext.tsx         # Dark/light theme
├── DialogContext.tsx        # Modal management
├── ToastContext.tsx         # Toast notifications
├── LiveUpdatesProvider.tsx  # WebSocket connection
├── SidebarContext.tsx       # Sidebar state
├── PanelContext.tsx         # Panel state
└── BreadcrumbContext.tsx    # Breadcrumb state
```

## Key UI Components

- **KanbanBoard** — Issue board (backlog → done)
- **GoalTree** — Hierarchical goal visualization
- **CompanyRail** — Left sidebar with company list
- **CommandPalette** — Quick navigation (cmd+k)
- **LiveRunWidget** — Real-time agent execution view
- **RunTranscriptView** — Agent run log viewer with streaming
- **OrgChart** — Mermaid-based org chart
- **BudgetPolicyCard** / **BudgetIncidentCard** — Budget UI

## Key Patterns
- TanStack Query for all server state (no manual fetching)
- Adapter registry pattern mirrored on frontend (UI config per runtime)
- Context providers for app-wide state
- 30+ pages showing full-featured business application
- Live updates via WebSocket context provider

## Relevance to Our Project
Our dashboard is a single-page HTML/CSS/JS app without a framework. Paperclip's React + TanStack Query approach is more maintainable. The UI adapter registry (each agent runtime has its own config components) is a pattern we could use — currently our dashboard has no per-provider configuration UI. The KanbanBoard and GoalTree components show the "business layer" UI we'd need to add organizational features.
