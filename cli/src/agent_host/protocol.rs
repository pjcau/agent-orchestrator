//! Wire protocol for the agent-host channel.
//!
//! Byte-compatible with the Python implementation in
//! `src/agent_orchestrator/agent_host/protocol.py`. Each frame is a
//! JSON object with a `kind` discriminator and an immutable `frame_id`
//! (hex-encoded UUID). Subclass payloads are additive so the schema can
//! grow without breaking older peers; an unknown `kind` is rejected by
//! [`parse_frame`] so peers fail loudly rather than silently dropping
//! frames.
//!
//! The frame catalogue mirrors the agent-loop state machine:
//!
//! | Direction        | Kind            | Purpose                                  |
//! |------------------|-----------------|------------------------------------------|
//! | client → server  | `hello`         | Open the session, declare cwd + manifest |
//! | server → client  | `ack`           | Confirm pairing, assign `run_id`         |
//! | client → server  | `prompt`        | User turn input                          |
//! | server → client  | `tool_call`     | Server asks the client to run a tool     |
//! | client → server  | `tool_result`   | Client returns the tool outcome          |
//! | client → server  | `tool_chunk`    | Streamed tool output                     |
//! | either           | `cancel`        | Abort an in-flight tool call             |
//! | server → client  | `assistant_text` | Streamed LLM tokens                     |
//! | server → client  | `turn_end`      | Server-side turn complete                |
//! | either           | `error`         | Hard failure with a typed `code`         |

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use thiserror::Error;

pub const PROTOCOL_VERSION: u32 = 1;

pub const KIND_HELLO: &str = "hello";
pub const KIND_ACK: &str = "ack";
pub const KIND_PROMPT: &str = "prompt";
pub const KIND_TOOL_CALL: &str = "tool_call";
pub const KIND_TOOL_RESULT: &str = "tool_result";
pub const KIND_TOOL_CHUNK: &str = "tool_chunk";
pub const KIND_CANCEL: &str = "cancel";
pub const KIND_ASSISTANT_TEXT: &str = "assistant_text";
pub const KIND_TURN_END: &str = "turn_end";
pub const KIND_ERROR: &str = "error";
pub const KIND_STEP: &str = "step";

/// Fail-loud error from [`parse_frame`].
///
/// Silently dropping unknown frames would hide protocol drift between
/// client and server and turn into impossible-to-debug freezes during
/// a long chat session.
#[derive(Debug, Error)]
pub enum FrameError {
    #[error("missing or unknown frame kind: {0:?}")]
    UnknownKind(Option<String>),
    #[error("malformed JSON: {0}")]
    Json(#[from] serde_json::Error),
}

fn new_frame_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}

fn now_ts() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// Common header for every agent-host frame.
///
/// Variants carry typed payloads; round-trip parsing uses serde tags so
/// the wire schema matches the Python `@dataclass(frozen=True)` exactly
/// (see `protocol.py`).
#[derive(Debug, Clone, PartialEq)]
pub enum Frame {
    Hello(Hello),
    Ack(Ack),
    Prompt(Prompt),
    ToolCall(ToolCall),
    ToolResult(ToolResult),
    ToolChunk(ToolChunk),
    Cancel(Cancel),
    AssistantText(AssistantText),
    TurnEnd(TurnEnd),
    Error(ErrorFrame),
    Step(Step),
}

impl Frame {
    /// Serialise to the same dict shape Python emits.
    ///
    /// The `kind` field always comes first by convention; otherwise the
    /// ordering does not matter because both peers parse on the kind
    /// discriminator.
    pub fn to_value(&self) -> Value {
        match self {
            Frame::Hello(f) => serde_json::to_value(f).expect("Hello serializes"),
            Frame::Ack(f) => serde_json::to_value(f).expect("Ack serializes"),
            Frame::Prompt(f) => serde_json::to_value(f).expect("Prompt serializes"),
            Frame::ToolCall(f) => serde_json::to_value(f).expect("ToolCall serializes"),
            Frame::ToolResult(f) => serde_json::to_value(f).expect("ToolResult serializes"),
            Frame::ToolChunk(f) => serde_json::to_value(f).expect("ToolChunk serializes"),
            Frame::Cancel(f) => serde_json::to_value(f).expect("Cancel serializes"),
            Frame::AssistantText(f) => serde_json::to_value(f).expect("AssistantText serializes"),
            Frame::TurnEnd(f) => serde_json::to_value(f).expect("TurnEnd serializes"),
            Frame::Error(f) => serde_json::to_value(f).expect("Error serializes"),
            Frame::Step(f) => serde_json::to_value(f).expect("Step serializes"),
        }
    }

    /// JSON string representation — what the WebSocket transport sends.
    pub fn to_json(&self) -> String {
        self.to_value().to_string()
    }
}

/// Dispatch a raw JSON value on its `kind` discriminator.
///
/// Returns `FrameError::UnknownKind` if `kind` is missing or not in the
/// catalogue. Unknown payload fields are silently dropped by serde
/// (forward-compat); the kind discriminator is the only hard boundary.
pub fn parse_frame(v: &Value) -> Result<Frame, FrameError> {
    let kind = v.get("kind").and_then(Value::as_str);
    match kind {
        Some(KIND_HELLO) => Ok(Frame::Hello(serde_json::from_value(v.clone())?)),
        Some(KIND_ACK) => Ok(Frame::Ack(serde_json::from_value(v.clone())?)),
        Some(KIND_PROMPT) => Ok(Frame::Prompt(serde_json::from_value(v.clone())?)),
        Some(KIND_TOOL_CALL) => Ok(Frame::ToolCall(serde_json::from_value(v.clone())?)),
        Some(KIND_TOOL_RESULT) => Ok(Frame::ToolResult(serde_json::from_value(v.clone())?)),
        Some(KIND_TOOL_CHUNK) => Ok(Frame::ToolChunk(serde_json::from_value(v.clone())?)),
        Some(KIND_CANCEL) => Ok(Frame::Cancel(serde_json::from_value(v.clone())?)),
        Some(KIND_ASSISTANT_TEXT) => Ok(Frame::AssistantText(serde_json::from_value(v.clone())?)),
        Some(KIND_TURN_END) => Ok(Frame::TurnEnd(serde_json::from_value(v.clone())?)),
        Some(KIND_ERROR) => Ok(Frame::Error(serde_json::from_value(v.clone())?)),
        Some(KIND_STEP) => Ok(Frame::Step(serde_json::from_value(v.clone())?)),
        other => Err(FrameError::UnknownKind(other.map(str::to_string))),
    }
}

/// Convenience: parse a JSON string directly.
pub fn parse_frame_str(raw: &str) -> Result<Frame, FrameError> {
    let v: Value = serde_json::from_str(raw)?;
    parse_frame(&v)
}

// ---------------------------------------------------------------------------
// Frame payloads — one struct per kind.
// ---------------------------------------------------------------------------
//
// Every struct carries the shared header (`kind`, `frame_id`, `timestamp`)
// inline. Defaults are provided so callers can build a frame with a
// single named field and let the rest be filled in.

macro_rules! ts_default {
    () => {
        now_ts()
    };
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Hello {
    #[serde(default = "default_kind_hello")]
    pub kind: String,
    #[serde(default = "new_frame_id")]
    pub frame_id: String,
    #[serde(default = "default_ts")]
    pub timestamp: f64,
    #[serde(default = "default_version")]
    pub version: u32,
    #[serde(default)]
    pub cwd: String,
    #[serde(default)]
    pub tool_manifest: Vec<String>,
    #[serde(default)]
    pub stream_caps: Vec<String>,
    #[serde(default)]
    pub agent: String,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub provider: String,
    /// Max agent steps for a turn (the `--max-steps` flag). 0 = unset →
    /// the server applies its own default and clamps to a ceiling.
    #[serde(default)]
    pub max_steps: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Ack {
    #[serde(default = "default_kind_ack")]
    pub kind: String,
    #[serde(default = "new_frame_id")]
    pub frame_id: String,
    #[serde(default = "default_ts")]
    pub timestamp: f64,
    #[serde(default)]
    pub run_id: String,
    #[serde(default)]
    pub agent: String,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub provider: String,
    #[serde(default)]
    pub capabilities: Vec<String>,
    /// Per-session HMAC key (hex, 64 chars / 32 bytes). Server-minted,
    /// shipped here on every accept. The dashboard's stable
    /// `JWT_SECRET_KEY` is never sent to the client.
    #[serde(default)]
    pub signing_key: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Prompt {
    #[serde(default = "default_kind_prompt")]
    pub kind: String,
    #[serde(default = "new_frame_id")]
    pub frame_id: String,
    #[serde(default = "default_ts")]
    pub timestamp: f64,
    #[serde(default)]
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolCall {
    #[serde(default = "default_kind_tool_call")]
    pub kind: String,
    #[serde(default = "new_frame_id")]
    pub frame_id: String,
    #[serde(default = "default_ts")]
    pub timestamp: f64,
    #[serde(default)]
    pub tool_call_id: String,
    #[serde(default)]
    pub name: String,
    /// Free-form JSON object — the canonical schemas live in
    /// `src/agent_orchestrator/agent_host/server.py`
    /// (`_DEFAULT_TOOL_SCHEMAS`); the Rust runner accepts aliases for
    /// the common parameter names so a sloppy LLM still works.
    #[serde(default)]
    pub args: HashMap<String, Value>,
    #[serde(default)]
    pub nonce: String,
    #[serde(default)]
    pub signature: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolResult {
    #[serde(default = "default_kind_tool_result")]
    pub kind: String,
    #[serde(default = "new_frame_id")]
    pub frame_id: String,
    #[serde(default = "default_ts")]
    pub timestamp: f64,
    #[serde(default)]
    pub tool_call_id: String,
    #[serde(default = "default_status_ok")]
    pub status: String,
    /// Tool-specific output (string for file content, object for shell).
    #[serde(default)]
    pub output: Value,
    #[serde(default)]
    pub error_code: String,
    #[serde(default)]
    pub nonce: String,
    #[serde(default)]
    pub signature: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolChunk {
    #[serde(default = "default_kind_tool_chunk")]
    pub kind: String,
    #[serde(default = "new_frame_id")]
    pub frame_id: String,
    #[serde(default = "default_ts")]
    pub timestamp: f64,
    #[serde(default)]
    pub tool_call_id: String,
    #[serde(default)]
    pub seq: u64,
    #[serde(default)]
    pub chunk: String,
    #[serde(default)]
    pub eof: bool,
    #[serde(default)]
    pub nonce: String,
    #[serde(default)]
    pub signature: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Cancel {
    #[serde(default = "default_kind_cancel")]
    pub kind: String,
    #[serde(default = "new_frame_id")]
    pub frame_id: String,
    #[serde(default = "default_ts")]
    pub timestamp: f64,
    #[serde(default)]
    pub tool_call_id: String,
    #[serde(default)]
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AssistantText {
    #[serde(default = "default_kind_assistant_text")]
    pub kind: String,
    #[serde(default = "new_frame_id")]
    pub frame_id: String,
    #[serde(default = "default_ts")]
    pub timestamp: f64,
    #[serde(default)]
    pub chunk: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TurnEnd {
    #[serde(default = "default_kind_turn_end")]
    pub kind: String,
    #[serde(default = "new_frame_id")]
    pub frame_id: String,
    #[serde(default = "default_ts")]
    pub timestamp: f64,
    #[serde(default = "default_status_ok")]
    pub status: String,
    #[serde(default)]
    pub step_count: u64,
    /// Turn totals — upstream prompt tokens, downstream completion
    /// tokens, accumulated USD cost. Additive: a server that does not
    /// send them leaves these 0 (`#[serde(default)]`).
    #[serde(default)]
    pub input_tokens: u64,
    #[serde(default)]
    pub output_tokens: u64,
    #[serde(default)]
    pub cost_usd: f64,
    /// Short human-readable reason when `status != "ok"` (e.g.
    /// "Max steps (10) reached"). Empty on success. Additive field.
    #[serde(default)]
    pub error: String,
}

/// Server → client. Progress indicator inside a turn — see
/// `agent_orchestrator.agent_host.protocol.Step` for the contract.
/// Multi-agent runs emit one frame per orchestrator step so the user
/// sees `[3/15] backend: writing api/main.py` instead of dead air.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Step {
    #[serde(default = "default_kind_step")]
    pub kind: String,
    #[serde(default = "new_frame_id")]
    pub frame_id: String,
    #[serde(default = "default_ts")]
    pub timestamp: f64,
    #[serde(default)]
    pub index: u64,
    #[serde(default)]
    pub total: u64,
    #[serde(default)]
    pub label: String,
    #[serde(default)]
    pub agent: String,
    /// Cumulative turn totals known when this step was emitted — turns
    /// the progress line into a live token meter. Additive: a server
    /// that does not send them leaves these 0 (`#[serde(default)]`).
    #[serde(default)]
    pub input_tokens: u64,
    #[serde(default)]
    pub output_tokens: u64,
    #[serde(default)]
    pub cost_usd: f64,
    /// Cross-turn workspace-digest decision for THIS turn, stamped by the
    /// orchestrator on the team-lead's first step (e.g. "injected (…, keep)",
    /// "reset (pivot)", "empty"). Additive: empty when the server omits it.
    #[serde(default)]
    pub digest: String,
}

/// The wire kind is `error` but Rust reserves `Error` so we suffix.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ErrorFrame {
    #[serde(default = "default_kind_error")]
    pub kind: String,
    #[serde(default = "new_frame_id")]
    pub frame_id: String,
    #[serde(default = "default_ts")]
    pub timestamp: f64,
    #[serde(default)]
    pub code: String,
    #[serde(default)]
    pub message: String,
}

// ---------------------------------------------------------------------------
// Defaults — keep one per kind so serde can fill in missing fields on
// forward-compat parses.
// ---------------------------------------------------------------------------

fn default_kind_hello() -> String {
    KIND_HELLO.to_string()
}
fn default_kind_ack() -> String {
    KIND_ACK.to_string()
}
fn default_kind_prompt() -> String {
    KIND_PROMPT.to_string()
}
fn default_kind_tool_call() -> String {
    KIND_TOOL_CALL.to_string()
}
fn default_kind_tool_result() -> String {
    KIND_TOOL_RESULT.to_string()
}
fn default_kind_tool_chunk() -> String {
    KIND_TOOL_CHUNK.to_string()
}
fn default_kind_cancel() -> String {
    KIND_CANCEL.to_string()
}
fn default_kind_assistant_text() -> String {
    KIND_ASSISTANT_TEXT.to_string()
}
fn default_kind_turn_end() -> String {
    KIND_TURN_END.to_string()
}
fn default_kind_error() -> String {
    KIND_ERROR.to_string()
}
fn default_kind_step() -> String {
    KIND_STEP.to_string()
}

fn default_version() -> u32 {
    PROTOCOL_VERSION
}
fn default_status_ok() -> String {
    "ok".to_string()
}
fn default_ts() -> f64 {
    ts_default!()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn round_trip(f: Frame) -> Frame {
        let v = f.to_value();
        parse_frame(&v).expect("round trip parse")
    }

    #[test]
    fn hello_round_trip() {
        let f = Frame::Hello(Hello {
            kind: KIND_HELLO.into(),
            frame_id: "abc".into(),
            timestamp: 1.0,
            version: PROTOCOL_VERSION,
            cwd: "/tmp/p".into(),
            tool_manifest: vec!["file_read".into(), "file_write".into()],
            stream_caps: vec!["tool_chunk".into()],
            agent: "team-lead".into(),
            model: "tencent/hy3-preview".into(),
            provider: "openrouter".into(),
            max_steps: 30,
        });
        assert_eq!(round_trip(f.clone()), f);
    }

    #[test]
    fn ack_carries_signing_key() {
        let f = Frame::Ack(Ack {
            kind: KIND_ACK.into(),
            frame_id: "x".into(),
            timestamp: 0.0,
            run_id: "r-1".into(),
            agent: "backend".into(),
            model: "m".into(),
            provider: "openrouter".into(),
            capabilities: vec!["file_read".into()],
            signing_key: "deadbeef".repeat(8),
        });
        let parsed = round_trip(f.clone());
        if let Frame::Ack(a) = parsed {
            assert_eq!(a.signing_key.len(), 64);
        } else {
            panic!("kind drift");
        }
    }

    #[test]
    fn tool_call_round_trip() {
        let mut args = HashMap::new();
        args.insert("file_path".into(), json!("note.md"));
        args.insert("content".into(), json!("hi"));
        let f = Frame::ToolCall(ToolCall {
            kind: KIND_TOOL_CALL.into(),
            frame_id: "f".into(),
            timestamp: 0.0,
            tool_call_id: "tc".into(),
            name: "file_write".into(),
            args,
            nonce: "n".into(),
            signature: "s".into(),
        });
        assert_eq!(round_trip(f.clone()), f);
    }

    #[test]
    fn tool_result_with_object_output() {
        let f = Frame::ToolResult(ToolResult {
            kind: KIND_TOOL_RESULT.into(),
            frame_id: "f".into(),
            timestamp: 0.0,
            tool_call_id: "tc".into(),
            status: "ok".into(),
            output: json!({"stdout": "hi", "returncode": 0}),
            error_code: "".into(),
            nonce: "n".into(),
            signature: "s".into(),
        });
        assert_eq!(round_trip(f.clone()), f);
    }

    #[test]
    fn parse_unknown_kind_rejected() {
        let v = json!({"kind": "totally-unknown", "frame_id": "x"});
        match parse_frame(&v) {
            Err(FrameError::UnknownKind(Some(k))) => assert_eq!(k, "totally-unknown"),
            other => panic!("expected UnknownKind, got {other:?}"),
        }
    }

    #[test]
    fn parse_missing_kind_rejected() {
        let v = json!({"frame_id": "x"});
        match parse_frame(&v) {
            Err(FrameError::UnknownKind(None)) => {}
            other => panic!("expected UnknownKind(None), got {other:?}"),
        }
    }

    #[test]
    fn forward_compat_unknown_field_dropped() {
        // v2 peer adds `priority` to ToolCall — v1 receiver should still
        // parse the frame, dropping the unknown field rather than raising.
        let v = json!({
            "kind": KIND_TOOL_CALL,
            "frame_id": "f",
            "timestamp": 0.0,
            "tool_call_id": "t",
            "name": "file_read",
            "args": {},
            "nonce": "",
            "signature": "",
            "priority": "high"
        });
        let parsed = parse_frame(&v).expect("forward-compat parse");
        if let Frame::ToolCall(t) = parsed {
            assert_eq!(t.name, "file_read");
        } else {
            panic!("kind drift");
        }
    }

    #[test]
    fn hello_version_carried_through() {
        let f = Frame::Hello(Hello {
            kind: KIND_HELLO.into(),
            frame_id: "f".into(),
            timestamp: 0.0,
            version: 99,
            cwd: "".into(),
            tool_manifest: vec!["x".into()],
            stream_caps: vec![],
            agent: "".into(),
            model: "".into(),
            provider: "".into(),
            max_steps: 0,
        });
        let v = f.to_value();
        assert_eq!(v["kind"], KIND_HELLO);
        assert_eq!(v["version"], 99);
        let parsed = parse_frame(&v).unwrap();
        if let Frame::Hello(h) = parsed {
            assert_eq!(h.version, 99);
        } else {
            panic!("kind drift");
        }
    }

    #[test]
    fn step_round_trip_with_usage() {
        let f = Frame::Step(Step {
            kind: KIND_STEP.into(),
            frame_id: "f".into(),
            timestamp: 0.0,
            index: 3,
            total: 0,
            label: "thinking".into(),
            agent: "team-lead".into(),
            input_tokens: 4096,
            output_tokens: 512,
            cost_usd: 0.004,
            digest: "injected (4 files, 1 ok-cmd, 2 bad-cmd, keep)".into(),
        });
        assert_eq!(round_trip(f.clone()), f);
    }

    #[test]
    fn turn_end_round_trip_with_usage() {
        let f = Frame::TurnEnd(TurnEnd {
            kind: KIND_TURN_END.into(),
            frame_id: "f".into(),
            timestamp: 0.0,
            status: "ok".into(),
            step_count: 7,
            input_tokens: 1234,
            output_tokens: 567,
            cost_usd: 0.0123,
            error: String::new(),
        });
        assert_eq!(round_trip(f.clone()), f);
    }

    #[test]
    fn turn_end_carries_error_reason() {
        let f = Frame::TurnEnd(TurnEnd {
            kind: KIND_TURN_END.into(),
            frame_id: "f".into(),
            timestamp: 0.0,
            status: "error".into(),
            step_count: 1,
            input_tokens: 510,
            output_tokens: 456,
            cost_usd: 0.0002,
            error: "Max steps (10) reached".into(),
        });
        let parsed = round_trip(f.clone());
        if let Frame::TurnEnd(t) = parsed {
            assert_eq!(t.error, "Max steps (10) reached");
            assert_eq!(t.status, "error");
        } else {
            panic!("kind drift");
        }
    }

    #[test]
    fn old_step_without_usage_defaults_to_zero() {
        // A v1 server emits Step without the token meter fields — a new
        // client must still parse it, defaulting them to 0.
        let v = json!({
            "kind": KIND_STEP,
            "frame_id": "f",
            "timestamp": 0.0,
            "index": 1,
            "total": 0,
            "label": "x",
            "agent": "",
        });
        let parsed = parse_frame(&v).expect("backward-compat parse");
        if let Frame::Step(s) = parsed {
            assert_eq!(s.input_tokens, 0);
            assert_eq!(s.output_tokens, 0);
            assert_eq!(s.cost_usd, 0.0);
            // A server that omits the digest note leaves it empty.
            assert_eq!(s.digest, "");
        } else {
            panic!("kind drift");
        }
    }

    #[test]
    fn parse_frame_str_helper() {
        let raw = json!({
            "kind": KIND_PROMPT,
            "frame_id": "f",
            "timestamp": 0.0,
            "text": "hi",
        })
        .to_string();
        let parsed = parse_frame_str(&raw).unwrap();
        if let Frame::Prompt(p) = parsed {
            assert_eq!(p.text, "hi");
        } else {
            panic!();
        }
    }
}
