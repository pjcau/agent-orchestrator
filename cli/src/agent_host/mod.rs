//! Native agent-host client for `ago chat --client-tools` and `ago run
//! --client-tools`.
//!
//! Replaces the prior Python subprocess (`python -m
//! agent_orchestrator.agent_host`) — the binary now owns the WebSocket,
//! the HMAC sign/verify, the path sandbox, the shell allowlist, and
//! the local execution of `file_read` / `file_write` / `shell_exec`.
//! The server still runs the agent loop, the LLM call, the team-lead
//! routing, and the conversation memory; only the tools execute here.
//!
//! Layout mirrors the Python package the Rust port replaces so the two
//! can be cross-referenced 1:1 during the transition:
//!
//! * [`protocol`] — frame catalogue (Hello, Ack, Prompt, ToolCall,
//!   ToolResult, ToolChunk, Cancel, AssistantText, TurnEnd, Error).
//! * [`signing`] — HMAC-SHA-256 over `(run_id, tool_call_id, nonce,
//!   name)` with a per-session key minted by the server in the ACK.
//! * [`sandbox`] — strict `enforce_workspace` (A.2 commit).
//! * [`allowlist`] — persistent `argv[0]` allowlist (A.2 commit).
//! * [`runner`] — local file/shell execution (A.3 commit).
//! * [`client`] — WebSocket client + REPL (A.4 commit).
//!
//! The wire protocol is byte-compatible with `src/agent_orchestrator/
//! agent_host/` in Python — the two clients can connect to the same
//! `/api/cli/v1/agent-host` endpoint and behave identically.

pub mod allowlist;
pub mod protocol;
pub mod runner;
pub mod sandbox;
pub mod signing;
