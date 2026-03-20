# 18 - Error Handling

## Overview

Paperclip uses custom error factories, Express 5 async error handling, structured error responses, and graceful degradation throughout the codebase.

## Custom Error Factories

```typescript
// server/src/errors.ts
export function notFound(message: string) {
  const error = new Error(message);
  (error as any).status = 404;
  return error;
}

export function unprocessable(message: string) {
  const error = new Error(message);
  (error as any).status = 422;
  return error;
}

export function conflict(message: string) {
  const error = new Error(message);
  (error as any).status = 409;
  return error;
}

export function badRequest(message: string) {
  const error = new Error(message);
  (error as any).status = 400;
  return error;
}
```

## Error Handler Middleware

```typescript
// server/src/middleware/error-handler.ts
export const errorHandler: ErrorRequestHandler = (err, req, res, next) => {
  const status = err.status ?? 500;
  res.status(status).json({ error: err.message });
  if (status >= 500) logger.error({ err }, "Unhandled error");
};
```

Express 5's native async support means no `asyncHandler()` wrapper needed — thrown errors in async routes are caught automatically.

## Usage Pattern

```typescript
// Typical service method
async function getById(id: string) {
  const row = await db.select()...;
  if (!row) throw notFound("Agent not found");
  if (row.companyId !== companyId) throw unprocessable("Agent does not belong to company");
  return row;
}
```

## Heartbeat Error Recovery

The heartbeat service handles adapter process failures:
- Process crash → log error, update run status
- Timeout → terminate process, update run status
- Detached process → special error code `process_detached`
- Budget breach mid-run → graceful shutdown

## Plugin Error Handling

Plugin worker crashes are handled by the lifecycle manager:
- Worker exit → transition to `error` state
- Restart policy → configurable retry
- Health check failures → surface in dashboard

## Key Patterns
- Simple error factories (not a class hierarchy)
- Express 5 async error handling (no wrappers)
- Structured JSON error responses
- Graceful degradation in heartbeat execution
- Plugin crash containment via process isolation

## Relevance to Our Project
Our error handling uses Python exceptions with FastAPI's exception handlers. Paperclip's approach is simpler — custom error factories with status codes. The heartbeat error recovery (process crash handling, detached process detection) is more robust than our agent execution error handling. The plugin crash containment via process isolation is a pattern we should adopt.
