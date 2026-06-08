//! WebSocket client + REPL for `ago chat --client-tools`.
//!
//! Owns the WS handshake, the per-session signing key, the dispatch of
//! incoming frames to the local [`runner`], and the user-facing REPL
//! (stdin read loop, stdout assistant streaming, stderr progress lines).
//! Replaces the Python `__main__.py` + `AgentHostClient` so the `ago`
//! binary is fully self-contained.
//!
//! Connection lifecycle:
//!
//! 1. `connect(url, token)` opens a TLS WebSocket via tokio-tungstenite,
//!    passing the JWT in the `X-API-Key` header.
//! 2. `handshake(workspace, agent, model, provider)` ships a HELLO,
//!    awaits ACK, decodes `signing_key` into raw bytes, stores
//!    everything in [`SessionInfo`].
//! 3. `run_repl(workspace, …)` enters the read/write loop until EOF
//!    (Ctrl-D), `:quit`, or a server `Error` frame.
//!
//! Concurrency:
//!
//! * One task for the WS read loop (`receive_loop`).
//! * One detached task per `ToolCall` so a long-running shell command
//!   does not block the receive loop from observing the matching
//!   `Cancel` frame.
//! * Stdin reads happen on a blocking thread (`spawn_blocking`) and
//!   feed prompts back through a channel.

use anyhow::{anyhow, Context, Result};
use futures_util::{SinkExt, StreamExt};
use serde_json::Value;
use std::collections::HashMap;
use std::io::{BufRead, IsTerminal, Write};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Instant;
use tokio::sync::{mpsc, Mutex, Notify};
use tokio_tungstenite::tungstenite::http::Request;
use tokio_tungstenite::tungstenite::{client::IntoClientRequest, Message};

use super::protocol::{
    parse_frame_str, Frame, Hello, Prompt, Step, ToolCall, ToolChunk, ToolResult, TurnEnd,
    PROTOCOL_VERSION,
};
use super::runner::{CancelSignal, ChunkEmitter, LocalToolRunner, ShellConfirmer, ToolOutcome};
use super::signing::{compute_signature, decode_hex_key};
use tracing::debug;

/// What the client learned during HELLO/ACK.
#[derive(Debug, Clone)]
pub struct SessionInfo {
    pub run_id: String,
    pub agent: String,
    pub model: String,
    pub provider: String,
    pub capabilities: Vec<String>,
    /// Raw bytes decoded from `Ack.signing_key`. Kept in memory only;
    /// dies with the connection — never persisted.
    pub signing_key: Vec<u8>,
}

/// Tunables surfaced for tests / future ops knobs.
pub struct ClientConfig {
    pub agent: String,
    pub model: String,
    pub provider: String,
    /// Max agent steps per turn (`--max-steps`). Sent in HELLO; 0 lets
    /// the server pick its default.
    pub max_steps: u64,
    /// When true (default), shell_exec streams stdout chunks as the
    /// process produces them (sent as signed `tool_chunk` frames).
    pub stream_shell: bool,
}

impl Default for ClientConfig {
    fn default() -> Self {
        Self {
            agent: String::new(),
            model: String::new(),
            provider: String::new(),
            max_steps: 0,
            stream_shell: true,
        }
    }
}

/// Connect to `<base_url>/api/cli/v1/agent-host` and return the open
/// WebSocket. `base_url` may use `http(s)://`; we rewrite the scheme
/// to `ws(s)://` before connecting.
pub async fn connect(base_url: &str, token: &str) -> Result<WsClient> {
    let ws_url = derive_ws_url(base_url);
    let mut req: Request<()> = ws_url
        .as_str()
        .into_client_request()
        .context("invalid agent-host URL")?;
    if !token.is_empty() {
        let val = tokio_tungstenite::tungstenite::http::HeaderValue::from_str(token)
            .map_err(|e| anyhow!("invalid API key for X-API-Key header: {e}"))?;
        req.headers_mut().insert("X-API-Key", val);
    }
    let (ws, _resp) = tokio_tungstenite::connect_async(req)
        .await
        .with_context(|| format!("agent-host connection to {ws_url} failed"))?;
    Ok(WsClient { stream: Some(ws) })
}

pub fn derive_ws_url(base_url: &str) -> String {
    let trimmed = base_url.trim_end_matches('/');
    let with_scheme = if let Some(rest) = trimmed.strip_prefix("https://") {
        format!("wss://{rest}")
    } else if let Some(rest) = trimmed.strip_prefix("http://") {
        format!("ws://{rest}")
    } else {
        trimmed.to_string()
    };
    format!("{with_scheme}/api/cli/v1/agent-host")
}

type WsStream =
    tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>>;

/// Thin wrapper so the rest of the file can talk in `send_frame` /
/// `receive_frame` terms instead of raw `Message`s.
pub struct WsClient {
    stream: Option<WsStream>,
}

impl WsClient {
    pub async fn handshake(
        &mut self,
        workspace: &std::path::Path,
        cfg: &ClientConfig,
    ) -> Result<SessionInfo> {
        let hello = Frame::Hello(Hello {
            kind: super::protocol::KIND_HELLO.into(),
            frame_id: uuid::Uuid::new_v4().simple().to_string(),
            timestamp: 0.0,
            version: PROTOCOL_VERSION,
            cwd: workspace.display().to_string(),
            tool_manifest: LocalToolRunner::manifest(),
            stream_caps: vec!["tool_chunk".into()],
            agent: cfg.agent.clone(),
            model: cfg.model.clone(),
            provider: cfg.provider.clone(),
            max_steps: cfg.max_steps,
        });
        self.send_frame(&hello).await?;

        let reply = self.receive_frame().await?;
        match reply {
            Frame::Ack(a) => {
                let signing_key = decode_hex_key(&a.signing_key)
                    .context("server returned invalid signing_key in ACK")?;
                Ok(SessionInfo {
                    run_id: a.run_id,
                    agent: a.agent,
                    model: a.model,
                    provider: a.provider,
                    capabilities: a.capabilities,
                    signing_key,
                })
            }
            Frame::Error(e) => Err(anyhow!("server rejected HELLO: {} — {}", e.code, e.message)),
            other => Err(anyhow!(
                "unexpected first frame from server: {:?}",
                std::mem::discriminant(&other)
            )),
        }
    }

    pub async fn send_frame(&mut self, frame: &Frame) -> Result<()> {
        let stream = self.stream.as_mut().ok_or_else(|| anyhow!("ws closed"))?;
        let payload = frame.to_json();
        stream
            .send(Message::Text(payload))
            .await
            .context("ws send failed")?;
        Ok(())
    }

    pub async fn receive_frame(&mut self) -> Result<Frame> {
        let stream = self.stream.as_mut().ok_or_else(|| anyhow!("ws closed"))?;
        loop {
            let msg = stream
                .next()
                .await
                .ok_or_else(|| anyhow!("ws closed by peer"))?
                .context("ws receive failed")?;
            match msg {
                Message::Text(t) => return Ok(parse_frame_str(&t)?),
                Message::Binary(_) => {
                    // Agent-host is text-only.
                    return Err(anyhow!("unexpected binary frame during handshake"));
                }
                Message::Ping(p) => {
                    let _ = stream.send(Message::Pong(p)).await;
                }
                Message::Pong(_) => continue,
                Message::Close(_) => return Err(anyhow!("ws closed by peer")),
                _ => continue,
            }
        }
    }

    pub async fn close(&mut self) -> Result<()> {
        if let Some(mut s) = self.stream.take() {
            let _ = s.close(None).await;
        }
        Ok(())
    }

    /// Take the stream so we can split it into sink/stream for the
    /// REPL phase where we need concurrent read + write.
    pub fn into_stream(mut self) -> Result<WsStream> {
        self.stream
            .take()
            .ok_or_else(|| anyhow!("ws already taken"))
    }
}

// ---------------------------------------------------------------------------
// REPL
// ---------------------------------------------------------------------------

/// Runs the chat REPL until EOF / `:quit` / server error.
///
/// `confirm` is the user-prompt callback for new shell binaries (the
/// `__main__.py` equivalent prompts on stderr); pass [`StdinShellConfirmer`]
/// for the default behaviour.
#[allow(clippy::too_many_arguments)]
pub async fn run_repl(
    ws: WsClient,
    session: SessionInfo,
    workspace: PathBuf,
    confirm: Option<Box<dyn ShellConfirmer>>,
    no_color: bool,
    shell_allow: &[String],
    shell_deny: &[String],
    shell_allow_all: bool,
) -> Result<()> {
    let runner = Arc::new(build_runner(
        &workspace,
        confirm,
        shell_allow,
        shell_deny,
        shell_allow_all,
    ));
    let session = Arc::new(session);
    let cancels: Arc<Mutex<HashMap<String, CancelSignal>>> = Arc::new(Mutex::new(HashMap::new()));

    // Split the WS into sink + stream so the receive loop can keep
    // reading while we send prompts and tool_results from another task.
    let ws_stream = ws.into_stream()?;
    let (sink, stream) = ws_stream.split();
    let sink = Arc::new(Mutex::new(sink));
    let stream = Arc::new(Mutex::new(stream));

    // (prompt) channel: stdin task → main loop → WS send.
    let (prompt_tx, mut prompt_rx) = mpsc::unbounded_channel::<ReplInput>();

    // Stdin reader (blocking) — kept in a dedicated OS thread because
    // tokio cannot poll stdin reliably across platforms.
    spawn_stdin_reader(prompt_tx);

    // Receive loop: dispatches frames concurrently. Lives in its own task
    // so the main loop can keep accepting stdin input.
    let recv_task = tokio::spawn(receive_loop(
        stream.clone(),
        sink.clone(),
        session.clone(),
        runner.clone(),
        cancels.clone(),
        no_color,
    ));

    // Main loop: only handles prompt input — everything else is event-driven.
    while let Some(input) = prompt_rx.recv().await {
        match input {
            ReplInput::Quit => break,
            ReplInput::Prompt(text) => {
                let frame = Frame::Prompt(Prompt {
                    kind: super::protocol::KIND_PROMPT.into(),
                    frame_id: uuid::Uuid::new_v4().simple().to_string(),
                    timestamp: 0.0,
                    text,
                });
                debug_frame("send", &frame);
                let mut sink_lock = sink.lock().await;
                if let Err(e) = sink_lock.send(Message::Text(frame.to_json())).await {
                    eprintln!("[agent-host] ws send failed: {e}");
                    break;
                }
            }
        }
    }

    // Signal the receive loop to wrap up by closing the sink.
    {
        let mut sink_lock = sink.lock().await;
        let _ = sink_lock.close().await;
    }
    let _ = recv_task.await;
    Ok(())
}

enum ReplInput {
    Prompt(String),
    Quit,
}

/// Coordinates the single stdin reader with interactive `[y/N]`
/// confirmations.
///
/// There is exactly one stdin per process, so two readers competing for
/// it is a bug: previously the REPL reader and the shell confirmer both
/// called `read_line`, so the user's `y` answering `allow git? [y/N]`
/// was stolen by the REPL and sent as a new chat prompt — the
/// confirmation then hung forever and the tool call timed out (the
/// session looked "stuck"). The router makes the REPL reader the sole
/// owner: when a confirmation is armed, the next line is delivered to
/// the confirmer instead of being treated as a prompt. A process global
/// is appropriate here because stdin itself is a process global.
#[derive(Clone, Default)]
struct StdinRouter {
    pending: Arc<std::sync::Mutex<Option<std::sync::mpsc::Sender<String>>>>,
}

impl StdinRouter {
    /// Arm routing and return a receiver for the next stdin line.
    /// Called by a confirmer right after it prints its `[y/N]` prompt.
    fn await_line(&self) -> std::sync::mpsc::Receiver<String> {
        let (tx, rx) = std::sync::mpsc::channel();
        *self.pending.lock().expect("stdin router poisoned") = Some(tx);
        rx
    }

    /// If a confirmation is armed, hand it this line and report it
    /// consumed (`true`); otherwise the caller treats the line as a
    /// normal prompt (`false`). Called by the stdin reader per line.
    fn try_deliver(&self, line: &str) -> bool {
        let mut guard = self.pending.lock().expect("stdin router poisoned");
        if let Some(tx) = guard.take() {
            let _ = tx.send(line.to_string());
            true
        } else {
            false
        }
    }
}

/// Process-global stdin router shared by the REPL reader and the
/// [`StdinShellConfirmer`].
fn stdin_router() -> &'static StdinRouter {
    static ROUTER: std::sync::OnceLock<StdinRouter> = std::sync::OnceLock::new();
    ROUTER.get_or_init(StdinRouter::default)
}

fn spawn_stdin_reader(tx: mpsc::UnboundedSender<ReplInput>) {
    std::thread::spawn(move || {
        let stdin = std::io::stdin();
        let mut line = String::new();
        loop {
            line.clear();
            eprint!("> ");
            let _ = std::io::stderr().flush();
            match stdin.lock().read_line(&mut line) {
                Ok(0) => {
                    let _ = tx.send(ReplInput::Quit);
                    break;
                }
                Ok(_) => {
                    let trimmed = line.trim_end_matches('\n').to_string();
                    // A pending confirmation claims this line first — the
                    // user's y/N must answer the prompt, not become a new
                    // chat message.
                    if stdin_router().try_deliver(&trimmed) {
                        continue;
                    }
                    match trimmed.as_str() {
                        ":quit" | ":q" | "exit" | "quit" => {
                            let _ = tx.send(ReplInput::Quit);
                            break;
                        }
                        "" => continue,
                        _ => {
                            if tx.send(ReplInput::Prompt(trimmed)).is_err() {
                                break;
                            }
                        }
                    }
                }
                Err(_) => {
                    let _ = tx.send(ReplInput::Quit);
                    break;
                }
            }
        }
    });
}

// Sink + stream type aliases keep the function signatures readable.
type WsSink = Arc<Mutex<futures_util::stream::SplitSink<WsStream, Message>>>;
type WsStreamShared = Arc<Mutex<futures_util::stream::SplitStream<WsStream>>>;

async fn receive_loop(
    stream: WsStreamShared,
    sink: WsSink,
    session: Arc<SessionInfo>,
    runner: Arc<LocalToolRunner>,
    cancels: Arc<Mutex<HashMap<String, CancelSignal>>>,
    no_color: bool,
) {
    let theme = AnsiTheme::detect(no_color);
    // Live token-meter state, reset per turn so tok/s reflects the
    // current turn rather than the whole session.
    let mut meter = Meter::new();
    // Buffer the assistant's reply for the turn so markdown (headings,
    // **bold**, `code`, lists, ``` fences) can be rendered as a coherent
    // block at TurnEnd. Streaming raw chunks can't colour markdown whose
    // markers straddle chunk boundaries. Progress stays live via Step lines.
    let mut assistant_buf = String::new();
    loop {
        let frame_res = {
            let mut s = stream.lock().await;
            match s.next().await {
                Some(Ok(Message::Text(t))) => Some(parse_frame_str(&t)),
                Some(Ok(Message::Binary(_))) => {
                    // Agent-host protocol is text-only. A binary frame
                    // means a misconfigured peer or a future extension —
                    // log + ignore rather than crash the loop.
                    eprintln!("[agent-host] unexpected binary frame ignored");
                    None
                }
                Some(Ok(Message::Ping(p))) => {
                    let mut snk = sink.lock().await;
                    let _ = snk.send(Message::Pong(p)).await;
                    None
                }
                Some(Ok(Message::Pong(_))) => None,
                Some(Ok(Message::Close(_))) | None => {
                    eprintln!("\n[agent-host] connection closed");
                    return;
                }
                Some(Ok(_)) => None,
                Some(Err(e)) => {
                    eprintln!("\n[agent-host] ws error: {e}");
                    return;
                }
            }
        };
        let frame = match frame_res {
            Some(Ok(f)) => f,
            Some(Err(e)) => {
                eprintln!("\n[agent-host] dropping unparseable frame: {e}");
                continue;
            }
            None => continue,
        };
        debug_frame("recv", &frame);
        match frame {
            Frame::AssistantText(t) => {
                // Accumulate; rendered as a markdown block at TurnEnd.
                assistant_buf.push_str(&t.chunk);
            }
            Frame::TurnEnd(t) => {
                // Render the buffered reply with markdown colouring, then a
                // usage summary on stderr so a piped stdout stays clean.
                // `highlight` returns the text byte-for-byte unchanged when
                // colour is off (no_color / NO_COLOR / non-TTY), so pipes are
                // unaffected.
                if !assistant_buf.is_empty() {
                    print!("{}", crate::render::highlight(&assistant_buf, no_color));
                    assistant_buf.clear();
                }
                println!();
                let _ = std::io::stdout().flush();
                print_turn_end(&theme, &t);
                meter.reset();
            }
            Frame::Error(e) => {
                // Flush whatever the assistant produced before the error so
                // partial output isn't lost.
                if !assistant_buf.is_empty() {
                    print!("{}", crate::render::highlight(&assistant_buf, no_color));
                    let _ = std::io::stdout().flush();
                    assistant_buf.clear();
                }
                eprintln!(
                    "\n{red}[agent-host] server error: {code} {msg}{reset}",
                    red = theme.red,
                    reset = theme.reset,
                    code = e.code,
                    msg = e.message
                );
                return;
            }
            Frame::ToolCall(tc) => {
                // Dispatch in a background task so the receive loop can
                // keep observing the matching CANCEL.
                let session = session.clone();
                let runner = runner.clone();
                let sink = sink.clone();
                let cancels = cancels.clone();
                let theme = theme.clone();
                tokio::spawn(async move {
                    handle_tool_call(tc, session, runner, sink, cancels, theme).await;
                });
            }
            Frame::Cancel(c) => {
                let map = cancels.lock().await;
                if let Some(notify) = map.get(&c.tool_call_id) {
                    notify.notify_one();
                }
            }
            Frame::Step(s) => {
                print_step(&theme, &s, &mut meter);
            }
            _ => {}
        }
    }
}

async fn handle_tool_call(
    frame: ToolCall,
    session: Arc<SessionInfo>,
    runner: Arc<LocalToolRunner>,
    sink: WsSink,
    cancels: Arc<Mutex<HashMap<String, CancelSignal>>>,
    theme: AnsiTheme,
) {
    let run_id = session.run_id.clone();
    let key = &session.signing_key;
    // Verify the inbound tool_call signature before doing anything local.
    if !super::signing::verify_signature(
        key,
        &run_id,
        &frame.tool_call_id,
        &frame.nonce,
        &frame.name,
        &frame.signature,
    ) {
        eprintln!(
            "\n[agent-host] dropping tool_call with bad signature id={}",
            frame.tool_call_id
        );
        send_tool_result(
            &sink,
            &run_id,
            &frame.tool_call_id,
            &frame.nonce,
            &frame.name,
            key,
            &ToolOutcome::err("signature_invalid"),
        )
        .await;
        return;
    }

    // Register a cancel signal for this tool_call so the receive loop
    // can wake the runner mid-call.
    let cancel: CancelSignal = Arc::new(Notify::new());
    {
        let mut map = cancels.lock().await;
        map.insert(frame.tool_call_id.clone(), cancel.clone());
    }

    print_progress_called(&theme, &frame);

    // Streaming for shell_exec: each chunk becomes a signed tool_chunk.
    let emitter: Option<Box<dyn ChunkEmitter>> = if frame.name == "shell_exec" {
        Some(Box::new(WsChunkEmitter {
            sink: sink.clone(),
            tool_call_id: frame.tool_call_id.clone(),
            nonce: frame.nonce.clone(),
            name: frame.name.clone(),
            run_id: run_id.clone(),
            key: key.clone(),
            seq: 0,
        }))
    } else {
        None
    };

    let started = Instant::now();
    let outcome = runner
        .run(
            &frame.name,
            frame.args.clone(),
            emitter,
            Some(cancel.clone()),
        )
        .await;
    let elapsed_ms = started.elapsed().as_millis() as u64;

    print_progress_finished(&theme, &frame, &outcome, elapsed_ms);

    {
        let mut map = cancels.lock().await;
        map.remove(&frame.tool_call_id);
    }

    send_tool_result(
        &sink,
        &run_id,
        &frame.tool_call_id,
        &frame.nonce,
        &frame.name,
        key,
        &outcome,
    )
    .await;
}

async fn send_tool_result(
    sink: &WsSink,
    run_id: &str,
    tool_call_id: &str,
    nonce: &str,
    name: &str,
    key: &[u8],
    outcome: &ToolOutcome,
) {
    let signature = compute_signature(key, run_id, tool_call_id, nonce, name);
    let frame = Frame::ToolResult(ToolResult {
        kind: super::protocol::KIND_TOOL_RESULT.into(),
        frame_id: uuid::Uuid::new_v4().simple().to_string(),
        timestamp: 0.0,
        tool_call_id: tool_call_id.into(),
        status: if outcome.success {
            "ok".into()
        } else {
            "error".into()
        },
        output: outcome.output.clone(),
        error_code: outcome.error_code.clone().unwrap_or_default(),
        nonce: nonce.into(),
        signature,
    });
    if outcome.success {
        debug_frame("send", &frame);
    } else {
        // P1: the runner stashes the human-readable cause (denied binary,
        // path outside workspace, io error, …) in `outcome.metadata`, which
        // is NOT carried on the wire. Log it here, where we still have it,
        // so the trace explains *why* a tool failed.
        debug!(
            "send tool_result id={} status=error reason=\"{}\"",
            tool_call_id,
            failure_reason(outcome)
        );
    }
    let mut snk = sink.lock().await;
    if let Err(e) = snk.send(Message::Text(frame.to_json())).await {
        eprintln!("[agent-host] failed to send tool_result: {e}");
    }
}

struct WsChunkEmitter {
    sink: WsSink,
    tool_call_id: String,
    nonce: String,
    name: String,
    run_id: String,
    key: Vec<u8>,
    seq: u64,
}

impl ChunkEmitter for WsChunkEmitter {
    fn emit<'a>(
        &'a mut self,
        chunk: &'a str,
    ) -> std::pin::Pin<Box<dyn std::future::Future<Output = ()> + Send + 'a>> {
        Box::pin(async move {
            let seq = self.seq;
            self.seq += 1;
            let signature = compute_signature(
                &self.key,
                &self.run_id,
                &self.tool_call_id,
                &self.nonce,
                &self.name,
            );
            let frame = Frame::ToolChunk(ToolChunk {
                kind: super::protocol::KIND_TOOL_CHUNK.into(),
                frame_id: uuid::Uuid::new_v4().simple().to_string(),
                timestamp: 0.0,
                tool_call_id: self.tool_call_id.clone(),
                seq,
                chunk: chunk.to_string(),
                eof: false,
                nonce: self.nonce.clone(),
                signature,
            });
            let mut snk = self.sink.lock().await;
            let _ = snk.send(Message::Text(frame.to_json())).await;
        })
    }
}

// ---------------------------------------------------------------------------
// ANSI rendering
// ---------------------------------------------------------------------------

/// Stable per-agent foreground palette. A multi-agent (`--agent team-lead`)
/// run interleaves Step frames from team-lead and several sub-agents on the
/// same stream; giving each agent its own colour makes the fan-out readable
/// at a glance. Red is deliberately excluded — it is reserved for failures.
const AGENT_PALETTE: &[&str] = &[
    "\x1b[36m", // cyan
    "\x1b[33m", // yellow
    "\x1b[35m", // magenta
    "\x1b[34m", // blue
    "\x1b[92m", // bright green
    "\x1b[96m", // bright cyan
    "\x1b[95m", // bright magenta
];

#[derive(Clone)]
struct AnsiTheme {
    dim: &'static str,
    green: &'static str,
    red: &'static str,
    bold: &'static str,
    reset: &'static str,
    colored: bool,
}

impl AnsiTheme {
    /// `force_no_color` is the resolved `--no-color` flag; it ORs with the
    /// `NO_COLOR` env var (https://no-color.org) and the non-TTY check so any
    /// one of them disables colour.
    fn detect(force_no_color: bool) -> Self {
        let no_color = force_no_color || std::env::var_os("NO_COLOR").is_some();
        let is_tty = std::io::stderr().is_terminal();
        if no_color || !is_tty {
            Self {
                dim: "",
                green: "",
                red: "",
                bold: "",
                reset: "",
                colored: false,
            }
        } else {
            Self {
                dim: "\x1b[2m",
                green: "\x1b[32m",
                red: "\x1b[31m",
                bold: "\x1b[1m",
                reset: "\x1b[0m",
                colored: true,
            }
        }
    }

    /// Deterministic colour for an agent name, stable across every Step so a
    /// given sub-agent keeps one colour for the whole run. Empty string when
    /// colour is disabled (the caller can wrap unconditionally).
    fn agent_color(&self, name: &str) -> &'static str {
        if !self.colored || name.is_empty() {
            return "";
        }
        let hash: usize = name.bytes().map(|b| b as usize).sum();
        AGENT_PALETTE[hash % AGENT_PALETTE.len()]
    }
}

/// One-line debug trace of a frame in either direction. Visible with
/// `ago -vv chat --client-tools` or `AGO_LOG=debug`. Lets the user share
/// exactly what crossed the wire (token fields, error reasons, timing)
/// when something looks stuck.
/// Render a JSON value to a compact single-line string for the debug log,
/// truncated so a large tool payload can't flood the trace. Plain strings
/// are shown unquoted; everything else is compact JSON.
fn compact_value(v: &Value) -> String {
    const MAX: usize = 200;
    if v.is_null() {
        return "-".to_string();
    }
    let s = match v.as_str() {
        Some(s) => s.to_string(),
        None => v.to_string(),
    };
    let s = s.replace('\n', " ");
    if s.chars().count() > MAX {
        let head: String = s.chars().take(MAX).collect();
        format!("{head}…[+{} chars]", s.chars().count() - MAX)
    } else {
        s
    }
}

/// Build a one-line failure reason from a failed [`ToolOutcome`]: the typed
/// `error_code` plus the most descriptive metadata field the runner left
/// behind. Returns an empty string for successful outcomes. This is the P1
/// fix — it turns a bare `status=error` into an actionable diagnosis.
fn failure_reason(outcome: &ToolOutcome) -> String {
    if outcome.success {
        return String::new();
    }
    let code = outcome.error_code.as_deref().unwrap_or("error");
    // Prefer the richest metadata field; the runner uses these keys.
    let detail = ["detail", "attempted", "path", "tool", "got_keys"]
        .iter()
        .find_map(|k| outcome.metadata.get(*k))
        .map(compact_value)
        .unwrap_or_default();
    if detail.is_empty() {
        code.to_string()
    } else {
        format!("{code}: {detail}")
    }
}

fn debug_frame(dir: &str, f: &Frame) {
    match f {
        Frame::Hello(h) => debug!("{dir} hello version={} agent={}", h.version, h.agent),
        Frame::Ack(a) => debug!("{dir} ack run_id={} model={}", a.run_id, a.model),
        Frame::Prompt(p) => debug!("{dir} prompt {}B", p.text.len()),
        Frame::ToolCall(tc) => debug!("{dir} tool_call id={} name={}", tc.tool_call_id, tc.name),
        Frame::ToolResult(tr) => {
            if tr.status == "ok" {
                debug!("{dir} tool_result id={} status=ok", tr.tool_call_id)
            } else {
                // P1: never log a bare `status=error`. Surface the typed
                // error_code and any payload so `--log-file` traces are
                // self-diagnosing (e.g. a denied `rm` shows the reason
                // instead of a blank failure).
                debug!(
                    "{dir} tool_result id={} status={} error_code={} output={}",
                    tr.tool_call_id,
                    tr.status,
                    if tr.error_code.is_empty() {
                        "-"
                    } else {
                        &tr.error_code
                    },
                    compact_value(&tr.output)
                )
            }
        }
        Frame::ToolChunk(c) => {
            debug!(
                "{dir} tool_chunk id={} seq={} eof={}",
                c.tool_call_id, c.seq, c.eof
            )
        }
        Frame::Cancel(c) => debug!("{dir} cancel id={} reason={}", c.tool_call_id, c.reason),
        Frame::AssistantText(a) => debug!("{dir} assistant_text {}B", a.chunk.len()),
        Frame::Step(s) => debug!(
            "{dir} step idx={} total={} agent={:?} label={:?} in={} out={} cost={}",
            s.index, s.total, s.agent, s.label, s.input_tokens, s.output_tokens, s.cost_usd
        ),
        Frame::TurnEnd(t) => debug!(
            "{dir} turn_end status={:?} steps={} in={} out={} cost={} error={:?}",
            t.status, t.step_count, t.input_tokens, t.output_tokens, t.cost_usd, t.error
        ),
        Frame::Error(e) => debug!("{dir} error code={} message={}", e.code, e.message),
    }
}

/// Live token-meter state for the current turn. `tok/s` is the
/// downstream (output) rate between consecutive Step frames, falling
/// back to the turn average on the first frame so the field is never
/// blank.
struct Meter {
    turn_start: Instant,
    last_t: Instant,
    last_out: u64,
}

impl Meter {
    fn new() -> Self {
        let now = Instant::now();
        Self {
            turn_start: now,
            last_t: now,
            last_out: 0,
        }
    }

    fn reset(&mut self) {
        let now = Instant::now();
        self.turn_start = now;
        self.last_t = now;
        self.last_out = 0;
    }

    /// Record the latest cumulative output-token count and return the
    /// instantaneous tok/s since the previous sample.
    fn tok_per_s(&mut self, output_tokens: u64) -> f64 {
        let now = Instant::now();
        let dt = now.duration_since(self.last_t).as_secs_f64();
        let rate = if dt > 0.05 && output_tokens >= self.last_out {
            (output_tokens - self.last_out) as f64 / dt
        } else {
            let elapsed = now.duration_since(self.turn_start).as_secs_f64();
            if elapsed > 0.0 {
                output_tokens as f64 / elapsed
            } else {
                0.0
            }
        };
        self.last_t = now;
        self.last_out = output_tokens;
        rate
    }
}

/// Compact token count: `950`, `12.3k`, `1.5M`.
fn fmt_tokens(n: u64) -> String {
    if n < 1000 {
        n.to_string()
    } else if n < 1_000_000 {
        format!("{:.1}k", n as f64 / 1000.0)
    } else {
        format!("{:.1}M", n as f64 / 1_000_000.0)
    }
}

fn print_step(theme: &AnsiTheme, step: &Step, meter: &mut Meter) {
    // Format examples:
    //   "  [3/15] backend: writing api/main.py   ↑12.3k ↓4.5k · $0.0123 · 78 tok/s"
    //   "  [2] team-lead: planning"        (total unknown, no usage yet)
    //   "  [-] team-lead: thinking"        (no index either — rare)
    let progress = if step.total > 0 {
        format!("[{}/{}]", step.index, step.total)
    } else if step.index > 0 {
        format!("[{}]", step.index)
    } else {
        "[-]".to_string()
    };
    let agent = if step.agent.is_empty() {
        String::new()
    } else {
        // Colour the agent name with its stable per-agent colour + bold so
        // each participant in a team run is instantly distinguishable. The
        // trailing reset returns to the surrounding dim style.
        format!(
            " {color}{bold}{name}{reset}{dim}:",
            color = theme.agent_color(&step.agent),
            bold = theme.bold,
            name = step.agent,
            reset = theme.reset,
            dim = theme.dim,
        )
    };
    let meter_str = if step.input_tokens > 0 || step.output_tokens > 0 {
        let tok_s = meter.tok_per_s(step.output_tokens);
        let cost = if step.cost_usd > 0.0 {
            format!(" · ${:.4}", step.cost_usd)
        } else {
            String::new()
        };
        format!(
            "  ↑{up} ↓{down}{cost} · {tps:.0} tok/s",
            up = fmt_tokens(step.input_tokens),
            down = fmt_tokens(step.output_tokens),
            cost = cost,
            tps = tok_s,
        )
    } else {
        String::new()
    };
    eprintln!(
        "{dim}  {progress}{agent} {label}{meter}{reset}",
        dim = theme.dim,
        reset = theme.reset,
        progress = progress,
        agent = agent,
        label = step.label,
        meter = meter_str,
    );
}

fn print_turn_end(theme: &AnsiTheme, turn: &TurnEnd) {
    let mark = if turn.status == "ok" {
        format!("{}✓{}", theme.green, theme.reset)
    } else {
        format!("{}✗{}", theme.red, theme.reset)
    };
    let mut bits = vec![format!("turn {}", turn.status)];
    if turn.step_count > 0 {
        bits.push(format!("{} steps", turn.step_count));
    }
    if turn.input_tokens > 0 || turn.output_tokens > 0 {
        bits.push(format!(
            "↑{} ↓{}",
            fmt_tokens(turn.input_tokens),
            fmt_tokens(turn.output_tokens)
        ));
    }
    if turn.cost_usd > 0.0 {
        bits.push(format!("${:.4}", turn.cost_usd));
    }
    // Surface the failure reason so the user is not left with a bare
    // "✗ turn error".
    let reason = if turn.status != "ok" && !turn.error.is_empty() {
        format!(
            " {dim}— {err}{reset}",
            dim = theme.dim,
            err = turn.error,
            reset = theme.reset
        )
    } else {
        String::new()
    };
    eprintln!(
        "  {mark} {dim}{body}{reset}{reason}",
        mark = mark,
        dim = theme.dim,
        reset = theme.reset,
        body = bits.join(" · "),
        reason = reason,
    );
}

fn print_progress_called(theme: &AnsiTheme, frame: &ToolCall) {
    let summary = summarise_args(&frame.args);
    eprintln!(
        "{dim}  ↳ {bold}{name}{reset}{dim}({summary}){reset}",
        dim = theme.dim,
        bold = theme.bold,
        reset = theme.reset,
        name = frame.name,
        summary = summary,
    );
}

fn print_progress_finished(
    theme: &AnsiTheme,
    frame: &ToolCall,
    outcome: &ToolOutcome,
    elapsed_ms: u64,
) {
    if outcome.success {
        let tail = summarise_output(&outcome.output);
        let tail = if tail.is_empty() {
            String::new()
        } else {
            format!(
                " {dim}— {tail}{reset}",
                dim = theme.dim,
                reset = theme.reset
            )
        };
        eprintln!(
            "{dim}  {green}✓{reset}{dim} {name} in {ms}ms{reset}{tail}",
            dim = theme.dim,
            green = theme.green,
            reset = theme.reset,
            name = frame.name,
            ms = elapsed_ms,
            tail = tail,
        );
    } else {
        let err = outcome.error_code.clone().unwrap_or_else(|| "error".into());
        eprintln!(
            "{dim}  {red}✗{reset}{dim} {name} in {ms}ms{reset} {dim}— {err}{reset}",
            dim = theme.dim,
            red = theme.red,
            reset = theme.reset,
            name = frame.name,
            ms = elapsed_ms,
            err = err,
        );
    }
}

fn summarise_args(args: &HashMap<String, Value>) -> String {
    let mut parts: Vec<String> = Vec::new();
    for (k, v) in args {
        let snippet = match v {
            Value::String(s) if k == "content" => format!("content=<{}B>", s.len()),
            Value::String(s) => {
                let mut sv = s.clone();
                if sv.len() > 40 {
                    sv.truncate(37);
                    sv.push('…');
                }
                format!("{k}={sv}")
            }
            Value::Array(arr) => {
                let preview = arr
                    .iter()
                    .take(3)
                    .map(|x| {
                        x.as_str()
                            .map(str::to_string)
                            .unwrap_or_else(|| x.to_string())
                    })
                    .collect::<Vec<_>>()
                    .join(", ");
                let more = if arr.len() > 3 { ", …" } else { "" };
                format!("{k}=[{preview}{more}]")
            }
            other => format!("{k}={other}"),
        };
        parts.push(snippet);
    }
    let joined = parts.join(", ");
    if joined.len() > 80 {
        format!("{}…", &joined[..79])
    } else {
        joined
    }
}

fn summarise_output(output: &Value) -> String {
    if output.is_null() {
        return String::new();
    }
    if let Value::Object(map) = output {
        if map.contains_key("returncode") {
            let rc = map.get("returncode").cloned().unwrap_or(Value::Null);
            let stdout_len = map
                .get("stdout")
                .and_then(Value::as_str)
                .map(str::len)
                .unwrap_or(0);
            let stderr_len = map
                .get("stderr")
                .and_then(Value::as_str)
                .map(str::len)
                .unwrap_or(0);
            let mut parts = vec![format!("rc={}", rc)];
            if stdout_len > 0 {
                parts.push(format!("{} stdout", human_bytes(stdout_len)));
            }
            if stderr_len > 0 {
                parts.push(format!("{} stderr", human_bytes(stderr_len)));
            }
            return parts.join(", ");
        }
        if let Some(n) = map.get("bytes_written").and_then(Value::as_u64) {
            return human_bytes(n as usize);
        }
    }
    if let Value::String(s) = output {
        return human_bytes(s.len());
    }
    let s = output.to_string();
    if s.len() > 60 {
        format!("{}…", &s[..57])
    } else {
        s
    }
}

fn human_bytes(n: usize) -> String {
    if n < 1024 {
        format!("{n}B")
    } else if n < 1024 * 1024 {
        format!("{:.1}KB", n as f64 / 1024.0)
    } else {
        format!("{:.1}MB", n as f64 / (1024.0 * 1024.0))
    }
}

// ---------------------------------------------------------------------------
// Stdin shell confirmer — the default UX
// ---------------------------------------------------------------------------

/// Interactive `[y/N]` confirmation on stderr. The answer is read
/// through the shared [`StdinRouter`], NOT by calling `read_line`
/// directly — otherwise it would race the REPL's own stdin reader and
/// the user's `y` could be consumed as a chat prompt (see `StdinRouter`).
pub struct StdinShellConfirmer;

impl ShellConfirmer for StdinShellConfirmer {
    fn confirm<'a>(
        &'a self,
        binary: &'a str,
        high_risk: bool,
    ) -> std::pin::Pin<Box<dyn std::future::Future<Output = bool> + Send + 'a>> {
        Box::pin(async move {
            let high_risk_label = if high_risk {
                " (HIGH RISK: full shell access)"
            } else {
                ""
            };
            eprint!("\n[agent-host] allow `{binary}` for this session?{high_risk_label} [y/N] ");
            let _ = std::io::stderr().flush();
            // Arm the router, then wait (off the async executor) for the
            // single stdin reader to hand us the user's line.
            let rx = stdin_router().await_line();
            let answer = tokio::task::spawn_blocking(move || rx.recv().ok())
                .await
                .ok()
                .flatten();
            match answer {
                Some(line) => matches!(line.trim().to_ascii_lowercase().as_str(), "y" | "yes"),
                None => false,
            }
        })
    }
}

fn build_runner(
    workspace: &std::path::Path,
    confirm: Option<Box<dyn ShellConfirmer>>,
    shell_allow: &[String],
    shell_deny: &[String],
    shell_allow_all: bool,
) -> LocalToolRunner {
    let mut runner = LocalToolRunner::new(workspace.to_path_buf()).with_shell_policy(
        shell_allow,
        shell_deny,
        shell_allow_all,
    );
    if let Some(c) = confirm {
        runner = runner.with_confirmer(c);
    }
    runner
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn derive_ws_url_https() {
        assert_eq!(
            derive_ws_url("https://agents-orchestrator.com"),
            "wss://agents-orchestrator.com/api/cli/v1/agent-host"
        );
    }

    #[test]
    fn derive_ws_url_http() {
        assert_eq!(
            derive_ws_url("http://localhost:5005/"),
            "ws://localhost:5005/api/cli/v1/agent-host"
        );
    }

    #[test]
    fn derive_ws_url_passthrough_when_already_ws() {
        // Future-proof: if the user already supplies a ws:// URL we
        // do not double-rewrite. The path is still appended once.
        assert_eq!(
            derive_ws_url("ws://localhost:5005"),
            "ws://localhost:5005/api/cli/v1/agent-host"
        );
    }

    #[test]
    fn summarise_args_collapses_content() {
        let mut m = HashMap::new();
        m.insert("file_path".into(), json!("note.md"));
        m.insert("content".into(), json!("x".repeat(2048)));
        let s = summarise_args(&m);
        assert!(s.contains("file_path=note.md"));
        assert!(s.contains("content=<2048B>"));
    }

    // --- P1: tool-error reason logging -------------------------------------

    #[test]
    fn failure_reason_empty_for_success() {
        let ok = ToolOutcome::ok(json!("hello"));
        assert_eq!(failure_reason(&ok), "");
    }

    #[test]
    fn failure_reason_includes_code_and_detail() {
        // Mirrors the real denied-`rm` case: error_code + a `detail` meta.
        let denied = ToolOutcome::err("shell_denied_by_policy")
            .with_meta("detail", json!("rm not allowed by project policy"));
        let r = failure_reason(&denied);
        assert!(r.starts_with("shell_denied_by_policy:"), "got {r}");
        assert!(r.contains("rm not allowed by project policy"), "got {r}");
    }

    #[test]
    fn failure_reason_falls_back_to_code_only() {
        let bare = ToolOutcome::err("shell_empty_argv");
        assert_eq!(failure_reason(&bare), "shell_empty_argv");
    }

    #[test]
    fn failure_reason_uses_attempted_when_no_detail() {
        let outside = ToolOutcome::err("path_outside_workspace")
            .with_meta("tool", json!("file_read"))
            .with_meta("attempted", json!("/etc/passwd"));
        let r = failure_reason(&outside);
        assert!(r.contains("path_outside_workspace"), "got {r}");
        // `detail` is absent, so the next preferred key (`attempted`) wins.
        assert!(r.contains("/etc/passwd"), "got {r}");
    }

    #[test]
    fn compact_value_truncates_and_flattens() {
        assert_eq!(compact_value(&Value::Null), "-");
        assert_eq!(compact_value(&json!("a\nb")), "a b");
        let long = compact_value(&json!("x".repeat(500)));
        assert!(long.contains("…[+300 chars]"), "got {long}");
    }

    #[test]
    fn stdin_router_routes_only_when_armed() {
        let r = StdinRouter::default();
        // Not armed → the reader keeps the line (treats it as a prompt).
        assert!(!r.try_deliver("hello"));
        // Armed → the next line is delivered to the confirmer waiter.
        let rx = r.await_line();
        assert!(r.try_deliver("y"));
        assert_eq!(rx.recv().unwrap(), "y");
        // Single-shot: after one delivery it disarms automatically.
        assert!(!r.try_deliver("again"));
    }

    #[test]
    fn fmt_tokens_compacts() {
        assert_eq!(fmt_tokens(0), "0");
        assert_eq!(fmt_tokens(950), "950");
        assert_eq!(fmt_tokens(12_345), "12.3k");
        assert_eq!(fmt_tokens(1_500_000), "1.5M");
    }

    #[test]
    fn meter_tok_per_s_is_non_negative_and_resets() {
        let mut m = Meter::new();
        // First sample falls back to the turn average — never negative.
        let r1 = m.tok_per_s(100);
        assert!(r1 >= 0.0);
        // A non-increasing count must not produce a negative rate.
        let r2 = m.tok_per_s(100);
        assert!(r2 >= 0.0);
        m.reset();
        assert_eq!(m.last_out, 0);
    }

    #[test]
    fn summarise_args_argv_array_first_three() {
        let mut m = HashMap::new();
        m.insert("argv".into(), json!(["pytest", "-q", "tests/", "extra"]));
        let s = summarise_args(&m);
        assert!(s.contains("argv=[pytest, -q, tests/, …]"));
    }

    #[test]
    fn summarise_output_shell() {
        let v = json!({"stdout": "abc", "stderr": "", "returncode": 0});
        assert_eq!(summarise_output(&v), "rc=0, 3B stdout");
    }

    #[test]
    fn summarise_output_file_write_bytes() {
        let v = json!({"bytes_written": 1024, "path": "x.md"});
        assert_eq!(summarise_output(&v), "1.0KB");
    }

    #[test]
    fn summarise_output_file_read_string() {
        let v = json!("hello");
        assert_eq!(summarise_output(&v), "5B");
    }

    #[test]
    fn human_bytes_units() {
        assert_eq!(human_bytes(500), "500B");
        assert_eq!(human_bytes(1024), "1.0KB");
        assert_eq!(human_bytes(5 * 1024 * 1024), "5.0MB");
    }

    /// A coloured theme, constructed directly so the test does not depend on
    /// the ambient TTY/NO_COLOR state of the CI runner.
    fn colored_theme() -> AnsiTheme {
        AnsiTheme {
            dim: "\x1b[2m",
            green: "\x1b[32m",
            red: "\x1b[31m",
            bold: "\x1b[1m",
            reset: "\x1b[0m",
            colored: true,
        }
    }

    #[test]
    fn force_no_color_disables_theme() {
        // --no-color must win even on a TTY with no NO_COLOR env var.
        let theme = AnsiTheme::detect(true);
        assert!(!theme.colored);
        assert_eq!(theme.dim, "");
        assert_eq!(theme.bold, "");
        assert_eq!(theme.agent_color("backend"), "");
    }

    #[test]
    fn agent_color_is_stable_per_name() {
        let theme = colored_theme();
        // Same name → same colour every call (so a sub-agent keeps one colour).
        assert_eq!(theme.agent_color("backend"), theme.agent_color("backend"));
        // A returned colour is always a member of the palette.
        assert!(AGENT_PALETTE.contains(&theme.agent_color("frontend")));
    }

    #[test]
    fn agent_color_distinguishes_common_team_agents() {
        let theme = colored_theme();
        // The three agents a typical team run interleaves should not all
        // collapse to a single colour — at least two distinct colours.
        let colors = [
            theme.agent_color("team-lead"),
            theme.agent_color("backend"),
            theme.agent_color("frontend"),
        ];
        let distinct: std::collections::HashSet<_> = colors.iter().collect();
        assert!(
            distinct.len() >= 2,
            "expected varied colours, got {colors:?}"
        );
    }

    #[test]
    fn agent_color_empty_name_is_blank() {
        assert_eq!(colored_theme().agent_color(""), "");
    }
}
