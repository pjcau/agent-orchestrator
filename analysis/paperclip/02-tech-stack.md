# 02 - Tech Stack

## Overview

Deep dive into Paperclip's technology choices, dependencies, and build tooling.

## Server Stack

| Technology | Version | Purpose |
|-----------|---------|---------|
| Node.js | 20+ | Runtime |
| TypeScript | 5.7+ | Language |
| Express | 5.1 | HTTP server |
| Drizzle ORM | 0.38 | Database access |
| PostgreSQL | embedded-postgres 18.1 | Database |
| ws | 8.19 | WebSocket |
| better-auth | 1.4 | Authentication |
| pino | 9.6 | Logging |
| zod | 3.24 | Validation |
| sharp | 0.34 | Image processing |
| @aws-sdk/client-s3 | 3.888 | S3 storage |
| multer | 2.0 | File upload |
| chokidar | 4.0 | File watching (plugins) |
| ajv | 8.18 | JSON schema validation |
| dompurify + jsdom | 3.3 / 28.1 | HTML sanitization |

## Frontend Stack

| Technology | Version | Purpose |
|-----------|---------|---------|
| React | 19 | UI framework |
| Vite | 6.1 | Build tool + dev server |
| Tailwind CSS | 4.0 | Styling |
| TanStack Query | 5.90 | Data fetching + cache |
| React Router | 7.1 | Routing |
| Radix UI | 1.4 | Headless components |
| cmdk | 1.1 | Command palette |
| Lucide | 0.574 | Icons |
| @mdxeditor/editor | 3.52 | Rich text editing |
| mermaid | 11.12 | Diagram rendering |
| react-markdown | 10.1 | Markdown rendering |
| @dnd-kit | 6.3 | Drag and drop |

## Build & Testing

| Tool | Purpose |
|------|---------|
| pnpm 9.15+ | Package manager |
| tsx | TypeScript execution (dev) |
| tsc | TypeScript compilation |
| vitest | Unit tests |
| Playwright | E2E tests |
| supertest | API integration tests |

## Notable Dependency Choices

### Embedded PostgreSQL
The `embedded-postgres` package bundles a PostgreSQL binary, eliminating the need for Docker or a separate database for local development. This is a "zero-config" approach — `npx paperclipai onboard --yes` starts a fully working system.

### better-auth
Modern auth library replacing Passport.js. Supports OAuth2 providers, session management, and multi-tenancy out of the box. Chosen over next-auth because the backend is Express, not Next.js.

### Drizzle ORM
Type-safe SQL query builder without code generation. Migrations are generated via `drizzle-kit generate`. The schema defines tables imperatively in TypeScript (not via decorator annotations).

### Express 5
The beta that's been stable enough for production. Key improvement over Express 4: native async error handling — throwing in an async route handler is caught automatically.

## Key Patterns
- Modern JS ecosystem (ESM modules, top-level await)
- Type-safe end-to-end (shared types between server and UI via workspace packages)
- Zero-config local dev via embedded database

## Relevance to Our Project
Our Python-based stack (FastAPI, SQLAlchemy) makes different trade-offs. The embedded PostgreSQL pattern is interesting — we require Docker for Postgres, adding friction. The shared types via monorepo packages is more robust than our approach of duplicating type definitions between Python and JS.
