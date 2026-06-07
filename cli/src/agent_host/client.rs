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
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{IsTerminal, Write};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::{mpsc, Mutex, Notify};
use tokio_tungstenite::tungstenite::http::Request;
use tokio_tungstenite::tungstenite::{client::IntoClientRequest, Message};

use super::protocol::{
    parse_frame_str, AssistantText, Cancel, ErrorFrame, Frame, Hello, Prompt,
    ToolCall, ToolChunk, ToolResult, TurnEnd, PROTOCOL_VERSION,
};
use super::runner::{
    CancelSignal, ChunkEmitter, LocalToolRunner, ShellConfirmer, ToolOutcome,
};
use super::signing::{compute_signature, decode_hex_key};

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

type WsStream = tokio_tungstenite::WebSocketStream<
    tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>,
>;

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
            Frame::Error(e) => Err(anyhow!(
                "server rejected HELLO: {} — {}",
                e.code,
                e.message
            )),
            other => Err(anyhow!(
                "unexpected first frame from server: {:?}",
                std::mem::discriminant(&other)
            )),
        }
    }

    pub async fn send_frame(&mut self, frame: &Frame) -> Result<()> {
        let stream = self
            .stream
            .as_mut()
            .ok_or_else(|| anyhow!("ws closed"))?;
        let payload = frame.to_json();
        stream
            .send(Message::Text(payload.into()))
            .await
            .context("ws send failed")?;
        Ok(())
    }

    pub async fn receive_frame(&mut self) -> Result<Frame> {
        let stream = self
            .stream
            .as_mut()
            .ok_or_else(|| anyhow!("ws closed"))?;
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
        self.stream.take().ok_or_else(|| anyhow!("ws already taken"))
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
pub async fn run_repl(
    ws: WsClient,
    session: SessionInfo,
    workspace: PathBuf,
    confirm: Option<Box<dyn ShellConfirmer>>,
) -> Result<()> {
    let runner = Arc::new(build_runner(&workspace, confirm));
    let session = Arc::new(session);
    let cancels: Arc<Mutex<HashMap<String, CancelSignal>>> =
        Arc::new(Mutex::new(HashMap::new()));

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
                let mut sink_lock = sink.lock().await;
                if let Err(e) = sink_lock.send(Message::Text(frame.to_json().into())).await {
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
type WsSink = Arc<
    Mutex<
        futures_util::stream::SplitSink<WsStream, Message>,
    >,
>;
type WsStreamShared = Arc<Mutex<futures_util::stream::SplitStream<WsStream>>>;

async fn receive_loop(
    stream: WsStreamShared,
    sink: WsSink,
    session: Arc<SessionInfo>,
    runner: Arc<LocalToolRunner>,
    cancels: Arc<Mutex<HashMap<String, CancelSignal>>>,
) {
    let theme = AnsiTheme::detect();
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
        match frame {
            Frame::AssistantText(t) => {
                print!("{}", t.chunk);
                let _ = std::io::stdout().flush();
            }
            Frame::TurnEnd(_) => {
                println!();
            }
            Frame::Error(e) => {
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
        .run(&frame.name, frame.args.clone(), emitter, Some(cancel.clone()))
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
        status: if outcome.success { "ok".into() } else { "error".into() },
        output: outcome.output.clone(),
        error_code: outcome.error_code.clone().unwrap_or_default(),
        nonce: nonce.into(),
        signature,
    });
    let mut snk = sink.lock().await;
    if let Err(e) = snk.send(Message::Text(frame.to_json().into())).await {
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
    ) -> std::pin::Pin<
        Box<dyn std::future::Future<Output = ()> + Send + 'a>,
    > {
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
            let _ = snk.send(Message::Text(frame.to_json().into())).await;
        })
    }
}

// ---------------------------------------------------------------------------
// ANSI rendering
// ---------------------------------------------------------------------------

#[derive(Clone)]
struct AnsiTheme {
    dim: &'static str,
    green: &'static str,
    red: &'static str,
    bold: &'static str,
    reset: &'static str,
}

impl AnsiTheme {
    fn detect() -> Self {
        let no_color = std::env::var_os("NO_COLOR").is_some();
        let is_tty = std::io::stderr().is_terminal();
        if no_color || !is_tty {
            Self {
                dim: "",
                green: "",
                red: "",
                bold: "",
                reset: "",
            }
        } else {
            Self {
                dim: "\x1b[2m",
                green: "\x1b[32m",
                red: "\x1b[31m",
                bold: "\x1b[1m",
                reset: "\x1b[0m",
            }
        }
    }
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
            format!(" {dim}— {tail}{reset}", dim = theme.dim, reset = theme.reset)
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
                    .map(|x| x.as_str().map(str::to_string).unwrap_or_else(|| x.to_string()))
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

/// Blocks stderr with an interactive `[y/N]` prompt. Matches the Python
/// `_confirm_shell` behaviour: single-user REPL, single-threaded, fine
/// to block.
pub struct StdinShellConfirmer;

impl ShellConfirmer for StdinShellConfirmer {
    fn confirm<'a>(
        &'a self,
        binary: &'a str,
        high_risk: bool,
    ) -> std::pin::Pin<
        Box<dyn std::future::Future<Output = bool> + Send + 'a>,
    > {
        Box::pin(async move {
            let high_risk_label = if high_risk {
                " (HIGH RISK: full shell access)"
            } else {
                ""
            };
            tokio::task::spawn_blocking({
                let binary = binary.to_string();
                let high_risk_label = high_risk_label.to_string();
                move || {
                    eprint!(
                        "\n[agent-host] allow `{binary}` for this session?{high_risk_label} [y/N] "
                    );
                    let _ = std::io::stderr().flush();
                    let mut buf = String::new();
                    if std::io::stdin().read_line(&mut buf).is_err() {
                        return false;
                    }
                    matches!(buf.trim().to_ascii_lowercase().as_str(), "y" | "yes")
                }
            })
            .await
            .unwrap_or(false)
        })
    }
}

fn build_runner(
    workspace: &std::path::Path,
    confirm: Option<Box<dyn ShellConfirmer>>,
) -> LocalToolRunner {
    let mut runner = LocalToolRunner::new(workspace.to_path_buf());
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
}
