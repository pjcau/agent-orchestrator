use crate::cli::RunArgs;
use crate::client::{AgentRunRequest, AgentRunResponse, RunEvent};
use crate::context::{expand_refs, ContextConfig};
use crate::error::{AgoError, Result};
use crate::runtime::Runtime;
use futures_util::StreamExt;
use indicatif::{ProgressBar, ProgressStyle};
use std::io::{IsTerminal, Read};
use std::time::Duration;

pub async fn run(rt: &Runtime, args: RunArgs) -> Result<()> {
    let raw_task = resolve_task(args.task.as_deref())?;
    // Expand @file / @dir references client-side. Local files are read on the
    // CLI's machine and inlined into the prompt before crossing the wire.
    let cwd = std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from("."));
    let (task_only, refs_cache, report) =
        expand_refs(&raw_task, &cwd, &ContextConfig::from_runtime(rt))?;
    // Prepend AGO.md (if any) so the cacheable prefix has the stable
    // project instructions first and the per-turn @ref content second —
    // best layout for prompt-cache hits.
    let cache_context =
        crate::instructions::Instructions::merge_with_refs(rt.instructions.as_ref(), &refs_cache);
    if let Some(doc) = rt.instructions.as_ref() {
        eprintln!(
            "\x1b[2m· loaded AGO.md ({} B{})\x1b[0m",
            doc.content.len(),
            if doc.truncated { " [truncated]" } else { "" }
        );
    }
    // When caching is opted out, fold the expansion back into the prompt so
    // the server still sees the @-ref content (just without the discount).
    let (task, cache_payload) = if rt.config.cache_is_enabled() && !cache_context.is_empty() {
        (task_only, Some(cache_context))
    } else if !cache_context.is_empty() {
        (format!("{task_only}\n\n---\n{cache_context}"), None)
    } else {
        (task_only, None)
    };
    for r in &report.resolved {
        eprintln!(
            "\x1b[2m· included {} ({} B{})\x1b[0m",
            r.token,
            r.bytes,
            if r.truncated { " [truncated]" } else { "" }
        );
    }
    for s in &report.skipped {
        eprintln!("\x1b[33m· skipped {} — {:?}\x1b[0m", s.token, s.reason);
    }
    let preset = rt.project.as_ref();

    // Resolution: CLI flag > .ago.yaml > global config / built-in default.
    let agent_string = args
        .agent
        .clone()
        .or_else(|| preset.and_then(|p| p.agent.clone()))
        .or_else(|| rt.config.default_agent.clone())
        .ok_or_else(|| {
            AgoError::Config(
                "no agent specified — pass --agent NAME, set agent in .ago.yaml, or default_agent in config"
                    .into(),
            )
        })?;
    let model_string = args
        .model
        .clone()
        .or_else(|| preset.and_then(|p| p.model.clone()))
        .ok_or_else(|| {
            AgoError::Config(
                "no model specified — pass --model ID or set model in .ago.yaml".into(),
            )
        })?;
    // For provider/max_steps the CLI uses clap defaults. We only fall back to
    // the preset when the user did NOT explicitly pass the flag — clap does
    // not natively tell us that, so we treat the clap default as a sentinel.
    let provider_string = if args.provider == default_provider() {
        preset
            .and_then(|p| p.provider.clone())
            .unwrap_or_else(default_provider)
    } else {
        args.provider.clone()
    };
    let max_steps = if args.max_steps == default_max_steps() {
        preset.and_then(|p| p.max_steps).unwrap_or(args.max_steps)
    } else {
        args.max_steps
    };

    // --local: skip the remote server entirely and shell out to the
    // embedded Python harness via the local_cli entrypoint. Mutually
    // exclusive with --stream / --resume because the subprocess has no
    // SSE channel and no shared conversation state.
    if args.local {
        if args.resume {
            eprintln!(
                "\x1b[33m· --resume has no effect in --local mode (no conversation persistence yet)\x1b[0m"
            );
        }
        if args.stream {
            eprintln!(
                "\x1b[33m· --stream has no effect in --local mode (one-shot blocking only)\x1b[0m"
            );
        }
        return run_local(
            &agent_string,
            &task,
            &model_string,
            &provider_string,
            max_steps,
            args.json,
            rt.no_color,
        )
        .await;
    }

    // --client-tools: agent loop stays on the server; tool execution is
    // delegated back to this cwd via the agent-host channel. Mutually
    // exclusive with --local (enforced by clap).
    if args.client_tools || args.client_tools_py {
        let _ = rt.api_client()?;
        let server_url = rt.server_url()?.to_string();
        let token_secret = rt
            .storage
            .load(&server_url)?
            .ok_or(AgoError::NotAuthenticated)?;
        use secrecy::ExposeSecret;
        let token = token_secret.expose_secret();
        if args.client_tools_py {
            return run_agent_host(
                &server_url,
                token,
                &agent_string,
                &task,
                &model_string,
                &provider_string,
            )
            .await;
        }
        return run_agent_host_native(
            &server_url,
            token,
            &agent_string,
            &task,
            &model_string,
            &provider_string,
            max_steps,
        )
        .await;
    }

    let client = rt.api_client()?;
    let server_url = rt.server_url()?.to_string();
    // --resume: send the most recent conversation_id we saw on this server.
    // If none stored yet we silently fall through so first-time --resume is
    // not an error.
    let resume_id = if args.resume {
        match rt.state.last_conversation_for(&server_url) {
            Some(id) => {
                eprintln!("\x1b[2m· resuming conversation {id}\x1b[0m");
                Some(id.to_string())
            }
            None => {
                eprintln!(
                    "\x1b[33m· --resume: no prior conversation for {server_url}, starting fresh\x1b[0m"
                );
                None
            }
        }
    } else {
        None
    };
    let req = AgentRunRequest {
        agent: &agent_string,
        task: &task,
        model: &model_string,
        provider: &provider_string,
        max_steps,
        conversation_id: resume_id.as_deref(),
        cache_context: cache_payload.as_deref(),
    };

    let outcome = if args.stream {
        run_streaming(&client, &req, args.json, rt.no_color).await
    } else {
        run_blocking(&client, &req, args.json, rt.no_color).await
    };

    // Persist whatever conversation_id we actually used so the next
    // --resume picks it up. Today the server reuses the id we send
    // (or mints one on its own when None) — we only know our own id
    // when --resume actively sent one, so we only update state in that
    // case. Storing the server-side new id is a v0.5.x follow-up that
    // needs the run/run_blocking helpers to return the resolved id.
    if outcome.is_ok() {
        if let Some(id) = resume_id.as_deref() {
            if let Err(e) = crate::state::persist_conversation(&rt.state_path, &server_url, id) {
                tracing::debug!("state.toml save failed: {e}");
            }
        }
    }
    outcome
}

fn default_provider() -> String {
    "ollama".to_string()
}

fn default_max_steps() -> u32 {
    10
}

async fn run_blocking(
    client: &crate::client::ApiClient,
    req: &AgentRunRequest<'_>,
    json: bool,
    no_color: bool,
) -> Result<()> {
    let resp = client.agent_run(req).await?;
    if json {
        let stdout = std::io::stdout();
        serde_json::to_writer(&stdout, &resp.raw).map_err(|e| AgoError::Other(e.to_string()))?;
        println!();
    } else {
        render_human(&resp, no_color);
    }
    if matches!(resp.success(), Some(false)) {
        return Err(AgoError::Other(
            resp.error()
                .map(|s| s.to_string())
                .unwrap_or_else(|| "agent run failed".into()),
        ));
    }
    Ok(())
}

async fn run_streaming(
    client: &crate::client::ApiClient,
    req: &AgentRunRequest<'_>,
    json: bool,
    no_color: bool,
) -> Result<()> {
    let mut stream = client.agent_run_stream(req).await?;
    let mut final_payload: Option<serde_json::Value> = None;
    let show_progress = !json && std::io::stderr().is_terminal();
    let spinner = show_progress.then(make_spinner);
    let mut step_count: u64 = 0;

    while let Some(item) = stream.next().await {
        let event = item?;
        if json {
            let line = serde_json::json!({"event": event.event, "data": event.data});
            println!(
                "{}",
                serde_json::to_string(&line).map_err(|e| AgoError::Other(e.to_string()))?
            );
        }
        match event.event.as_str() {
            "complete" => {
                final_payload = Some(event.data);
                break;
            }
            other => {
                step_count += 1;
                if let Some(pb) = &spinner {
                    update_spinner(pb, other, &event, step_count);
                }
            }
        }
    }

    if let Some(pb) = spinner {
        pb.finish_and_clear();
    }
    let payload = final_payload
        .ok_or_else(|| AgoError::Other("server closed stream before completion".into()))?;
    let resp = AgentRunResponse { raw: payload };

    if !json {
        render_human(&resp, no_color);
    }
    if matches!(resp.success(), Some(false)) {
        return Err(AgoError::Other(
            resp.error()
                .map(|s| s.to_string())
                .unwrap_or_else(|| "agent run failed".into()),
        ));
    }
    Ok(())
}

fn make_spinner() -> ProgressBar {
    let pb = ProgressBar::new_spinner();
    pb.set_style(
        ProgressStyle::with_template("{spinner:.cyan} {elapsed_precise} {prefix:.dim} {msg}")
            .unwrap()
            .tick_chars("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏ "),
    );
    pb.enable_steady_tick(Duration::from_millis(100));
    pb.set_prefix("running");
    pb
}

fn update_spinner(pb: &ProgressBar, kind: &str, event: &RunEvent, step: u64) {
    let agent = event
        .data
        .get("agent")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let label = if agent.is_empty() {
        format!("[{step:>3}] {kind}")
    } else {
        format!("[{step:>3}] {kind} ({agent})")
    };
    pb.set_message(label);
}

fn resolve_task(arg: Option<&str>) -> Result<String> {
    if let Some(s) = arg {
        if !s.trim().is_empty() {
            return Ok(s.to_string());
        }
    }
    let stdin = std::io::stdin();
    if stdin.is_terminal() {
        return Err(AgoError::Config(
            "no task — pass it as an argument or pipe via stdin".into(),
        ));
    }
    let mut buf = String::new();
    stdin.lock().read_to_string(&mut buf)?;
    let trimmed = buf.trim().to_string();
    if trimmed.is_empty() {
        return Err(AgoError::Config("empty task on stdin".into()));
    }
    Ok(trimmed)
}

/// Run the agent locally via `python3 -m agent_orchestrator.local_cli`.
///
/// We avoid pyo3 / heavy embedding — a one-shot subprocess that talks
/// JSON over stdin/stdout is enough for "I don't have a server, run it
/// here." Streaming and conversation persistence are deliberately not
/// supported in this mode (the v0.6.0 design effort decides whether
/// they're worth a length-prefixed JSON-RPC framing).
async fn run_local(
    agent: &str,
    task: &str,
    model: &str,
    provider: &str,
    max_steps: u32,
    json_out: bool,
    no_color: bool,
) -> Result<()> {
    use tokio::io::AsyncWriteExt;
    use tokio::process::Command;

    let python = pick_python();
    let req = serde_json::json!({
        "agent": agent,
        "task": task,
        "model": model,
        "provider": provider,
        "max_steps": max_steps,
    });
    let req_bytes = serde_json::to_vec(&req).map_err(|e| AgoError::Other(e.to_string()))?;

    eprintln!("\x1b[2m· spawning {python} -m agent_orchestrator.local_cli\x1b[0m");
    let mut child = Command::new(&python)
        .args(["-m", "agent_orchestrator.local_cli"])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::inherit())
        .spawn()
        .map_err(|e| {
            AgoError::Other(format!(
                "could not spawn {python}: {e}. Install with `pip install agent-orchestrator`."
            ))
        })?;

    if let Some(mut stdin) = child.stdin.take() {
        stdin.write_all(&req_bytes).await.map_err(AgoError::from)?;
        stdin.shutdown().await.map_err(AgoError::from)?;
    }

    let output = child.wait_with_output().await.map_err(AgoError::from)?;
    if !output.status.success() {
        return Err(AgoError::Other(format!(
            "local subprocess exited with status {}",
            output.status
        )));
    }
    // local_cli always writes a single JSON object even on failure, so
    // the parse step never fails on well-formed Python errors.
    let parsed: serde_json::Value = serde_json::from_slice(&output.stdout).map_err(|e| {
        AgoError::Other(format!(
            "could not parse local_cli output: {e} — raw: {}",
            String::from_utf8_lossy(&output.stdout)
        ))
    })?;
    if json_out {
        println!("{parsed}");
        return Ok(());
    }
    let resp = AgentRunResponse {
        raw: parsed.clone(),
    };
    render_human(&resp, no_color);
    if matches!(resp.success(), Some(false)) {
        return Err(AgoError::Other(
            resp.error()
                .map(|s| s.to_string())
                .unwrap_or_else(|| "local agent run failed".into()),
        ));
    }
    Ok(())
}

/// Pick the python interpreter to spawn. Honours `AGO_PYTHON` so a user
/// with a non-default virtualenv (poetry, conda, …) does not have to
/// activate it just for `ago run --local`. Falls back to `python3`.
fn pick_python() -> String {
    std::env::var("AGO_PYTHON").unwrap_or_else(|_| "python3".to_string())
}

/// Spawn the agent-host subprocess for `ago run --client-tools`.
///
/// The agent loop on the dashboard still drives the conversation; only
/// the tool calls (file_*, shell_exec) execute in the local cwd via the
/// WebSocket-bridged client. The task is fed as a single PROMPT frame
/// when the subprocess opens its session — for a multi-turn run use
/// `ago chat --client-tools` instead.
///
/// Token via env so it never appears in `ps`/`/proc/<pid>/cmdline`.
async fn run_agent_host(
    server_url: &str,
    token: &str,
    agent: &str,
    task: &str,
    model: &str,
    provider: &str,
) -> Result<()> {
    use tokio::io::AsyncWriteExt;
    use tokio::process::Command;
    let python = pick_python();
    let cwd = std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from("."));

    eprintln!("\x1b[2m· spawning {python} -m agent_orchestrator.agent_host (client-tools)\x1b[0m");
    let mut child = Command::new(&python)
        .args([
            "-m",
            "agent_orchestrator.agent_host",
            "--server",
            server_url,
            "--cwd",
            cwd.to_string_lossy().as_ref(),
            "--agent",
            agent,
            "--model",
            model,
            "--provider",
            provider,
        ])
        .env("AGO_API_KEY", token)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::inherit())
        .stderr(std::process::Stdio::inherit())
        .spawn()
        .map_err(|e| {
            AgoError::Other(format!(
                "could not spawn {python}: {e}. Install with `pip install agent-orchestrator`."
            ))
        })?;

    // Feed the one-shot task on stdin, then close. The subprocess will
    // emit the assistant reply to stdout (inherited) and exit on EOF.
    if let Some(mut stdin) = child.stdin.take() {
        stdin
            .write_all(task.as_bytes())
            .await
            .map_err(AgoError::from)?;
        stdin.write_all(b"\n").await.map_err(AgoError::from)?;
        stdin.shutdown().await.map_err(AgoError::from)?;
    }

    let status = child.wait().await.map_err(AgoError::from)?;
    if !status.success() {
        return Err(AgoError::Other(format!(
            "agent-host subprocess exited with status {status}"
        )));
    }
    Ok(())
}

fn render_human(resp: &AgentRunResponse, no_color: bool) {
    if let Some(output) = resp.output() {
        println!("{}", crate::render::highlight(output, no_color));
    }
    if let Some(err) = resp.error() {
        eprintln!("error: {err}");
    }
    let mut footer = Vec::new();
    if let Some(elapsed) = resp.elapsed_s() {
        footer.push(format!("{elapsed:.2}s"));
    }
    if let (Some(inp), Some(out)) = (resp.total_input_tokens(), resp.total_output_tokens()) {
        footer.push(format!("{inp}↑ / {out}↓ tokens"));
    }
    if let Some(cost) = resp.total_cost_usd() {
        footer.push(format!("${cost:.4}"));
    }
    if !footer.is_empty() {
        eprintln!("— {}", footer.join("  "));
    }
}

/// Native agent-host one-shot — opens the WS, sends one Prompt, prints
/// the assistant reply, and exits when the server emits TurnEnd. No
/// Python subprocess.
async fn run_agent_host_native(
    server_url: &str,
    token: &str,
    agent: &str,
    task: &str,
    model: &str,
    provider: &str,
    max_steps: u32,
) -> Result<()> {
    use crate::agent_host::client::{connect, ClientConfig};
    use crate::agent_host::protocol::{Frame, Prompt as PromptFrame, KIND_PROMPT};

    eprintln!("\x1b[2m· agent-host (native) one-shot to {server_url}\x1b[0m");
    let mut ws = connect(server_url, token)
        .await
        .map_err(|e| AgoError::Other(format!("agent-host connect failed: {e:#}")))?;
    let cwd = std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from("."));
    let cfg = ClientConfig {
        agent: agent.to_string(),
        model: model.to_string(),
        provider: provider.to_string(),
        max_steps: max_steps as u64,
        stream_shell: true,
    };
    let session = ws
        .handshake(&cwd, &cfg)
        .await
        .map_err(|e| AgoError::Other(format!("agent-host handshake failed: {e:#}")))?;
    eprintln!(
        "\x1b[2m· connected run_id={} agent={}\x1b[0m",
        session.run_id,
        if session.agent.is_empty() {
            "-"
        } else {
            &session.agent
        }
    );
    // One-shot: send the task as a Prompt, then read until TurnEnd / Error.
    ws.send_frame(&Frame::Prompt(PromptFrame {
        kind: KIND_PROMPT.into(),
        frame_id: uuid::Uuid::new_v4().simple().to_string(),
        timestamp: 0.0,
        text: task.to_string(),
    }))
    .await
    .map_err(|e| AgoError::Other(format!("agent-host prompt send failed: {e:#}")))?;
    loop {
        let frame = ws
            .receive_frame()
            .await
            .map_err(|e| AgoError::Other(format!("agent-host receive failed: {e:#}")))?;
        match frame {
            Frame::AssistantText(t) => {
                print!("{}", t.chunk);
                use std::io::Write;
                let _ = std::io::stdout().flush();
            }
            Frame::TurnEnd(_) => {
                println!();
                break;
            }
            Frame::Error(e) => {
                return Err(AgoError::Other(format!(
                    "server error: {} — {}",
                    e.code, e.message
                )));
            }
            // ToolCall/Cancel are not handled in one-shot mode — the
            // server's tool delegation only fires inside the REPL run
            // loop (the runner needs the per-call cancel registry).
            // Wire them up in a future commit if `ago run
            // --client-tools` becomes a real workflow.
            _ => {}
        }
    }
    let _ = ws.close().await;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::auth::MemoryStorage;
    use crate::config::Config;
    use crate::error::AgoError;
    use secrecy::SecretString;
    use std::sync::Arc;
    use tempfile::tempdir;
    use wiremock::matchers::{body_partial_json, header, method, path};
    use wiremock::{Mock, MockServer, ResponseTemplate};

    fn rt_with(server: &str, default_agent: Option<&str>) -> (Runtime, tempfile::TempDir) {
        let dir = tempdir().unwrap();
        let cfg_path = dir.path().join("config.toml");
        let mut cfg = Config::default();
        cfg.set("server", server).unwrap();
        if let Some(a) = default_agent {
            cfg.set("default_agent", a).unwrap();
        }
        let storage: Arc<dyn crate::auth::TokenStorage> = Arc::new(MemoryStorage::new());
        storage
            .save(server, &SecretString::from("k".to_string()))
            .unwrap();
        (Runtime::with_components(cfg, cfg_path, storage), dir)
    }

    #[tokio::test]
    async fn run_posts_request_and_renders() {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/api/agent/run"))
            .and(header("X-API-Key", "k"))
            .and(body_partial_json(serde_json::json!({
                "agent": "backend",
                "task": "do thing",
                "model": "claude-sonnet-4-6",
                "provider": "anthropic",
                "max_steps": 7
            })))
            .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
                "success": true,
                "output": "done",
                "elapsed_s": 1.25,
                "total_input_tokens": 100,
                "total_output_tokens": 50,
                "total_cost_usd": 0.0023
            })))
            .expect(1)
            .mount(&server)
            .await;

        let (rt, _d) = rt_with(&server.uri(), None);
        super::run(
            &rt,
            RunArgs {
                task: Some("do thing".into()),
                agent: Some("backend".into()),
                model: Some("claude-sonnet-4-6".into()),
                provider: "anthropic".into(),
                max_steps: 7,
                json: false,
                stream: false,
                resume: false,
                local: false,
                client_tools: false,
                client_tools_py: false,
            },
        )
        .await
        .unwrap();
    }

    #[tokio::test]
    async fn run_falls_back_to_default_agent() {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/api/agent/run"))
            .and(body_partial_json(serde_json::json!({ "agent": "backend" })))
            .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
                "success": true,
                "output": "ok"
            })))
            .mount(&server)
            .await;

        let (rt, _d) = rt_with(&server.uri(), Some("backend"));
        super::run(
            &rt,
            RunArgs {
                task: Some("x".into()),
                agent: None,
                model: Some("m".into()),
                provider: "ollama".into(),
                max_steps: 10,
                json: false,
                stream: false,
                resume: false,
                local: false,
                client_tools: false,
                client_tools_py: false,
            },
        )
        .await
        .unwrap();
    }

    #[tokio::test]
    async fn run_without_agent_anywhere_errors() {
        let server = MockServer::start().await;
        let (rt, _d) = rt_with(&server.uri(), None);
        let err = super::run(
            &rt,
            RunArgs {
                task: Some("x".into()),
                agent: None,
                model: Some("m".into()),
                provider: "ollama".into(),
                max_steps: 10,
                json: false,
                stream: false,
                resume: false,
                local: false,
                client_tools: false,
                client_tools_py: false,
            },
        )
        .await
        .unwrap_err();
        assert!(matches!(err, AgoError::Config(_)));
    }

    #[tokio::test]
    async fn run_propagates_failure_as_error() {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/api/agent/run"))
            .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
                "success": false,
                "error": "model unreachable"
            })))
            .mount(&server)
            .await;
        let (rt, _d) = rt_with(&server.uri(), None);
        let err = super::run(
            &rt,
            RunArgs {
                task: Some("x".into()),
                agent: Some("backend".into()),
                model: Some("m".into()),
                provider: "ollama".into(),
                max_steps: 10,
                json: false,
                stream: false,
                resume: false,
                local: false,
                client_tools: false,
                client_tools_py: false,
            },
        )
        .await
        .unwrap_err();
        assert!(matches!(err, AgoError::Other(_)));
    }

    #[tokio::test]
    async fn run_streaming_consumes_sse_events() {
        let server = MockServer::start().await;
        // wiremock supports raw string bodies — emit a complete SSE stream
        // with start + complete events.
        let body = concat!(
            "event: start\n",
            "data: {\"run_id\":\"abc\"}\n",
            "\n",
            "event: agent.spawn\n",
            "data: {\"agent\":\"backend\"}\n",
            "\n",
            "event: complete\n",
            "data: {\"success\":true,\"output\":\"done\"}\n",
            "\n",
        );
        Mock::given(method("POST"))
            .and(path("/api/cli/v1/run"))
            .and(header("X-API-Key", "k"))
            .respond_with(
                ResponseTemplate::new(200)
                    .insert_header("content-type", "text/event-stream")
                    .set_body_raw(body, "text/event-stream"),
            )
            .mount(&server)
            .await;

        let (rt, _d) = rt_with(&server.uri(), None);
        super::run(
            &rt,
            RunArgs {
                task: Some("x".into()),
                agent: Some("backend".into()),
                model: Some("m".into()),
                provider: "ollama".into(),
                max_steps: 10,
                json: false,
                stream: true,
                resume: false,
                local: false,
                client_tools: false,
                client_tools_py: false,
            },
        )
        .await
        .unwrap();
    }

    #[tokio::test]
    async fn run_streaming_without_complete_event_errors() {
        let server = MockServer::start().await;
        let body = concat!("event: start\n", "data: {\"run_id\":\"abc\"}\n", "\n",);
        Mock::given(method("POST"))
            .and(path("/api/cli/v1/run"))
            .respond_with(
                ResponseTemplate::new(200)
                    .insert_header("content-type", "text/event-stream")
                    .set_body_raw(body, "text/event-stream"),
            )
            .mount(&server)
            .await;

        let (rt, _d) = rt_with(&server.uri(), None);
        let err = super::run(
            &rt,
            RunArgs {
                task: Some("x".into()),
                agent: Some("backend".into()),
                model: Some("m".into()),
                provider: "ollama".into(),
                max_steps: 10,
                json: false,
                stream: true,
                resume: false,
                local: false,
                client_tools: false,
                client_tools_py: false,
            },
        )
        .await
        .unwrap_err();
        assert!(matches!(err, AgoError::Other(_)));
    }

    #[tokio::test]
    async fn run_uses_project_preset_when_flags_omitted() {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/api/agent/run"))
            .and(body_partial_json(serde_json::json!({
                "agent": "preset-agent",
                "model": "preset-model",
                "provider": "preset-provider",
                "max_steps": 42
            })))
            .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
                "success": true,
                "output": "ok"
            })))
            .mount(&server)
            .await;

        let (mut rt, _d) = rt_with(&server.uri(), None);
        rt = rt.with_project(
            crate::project::ProjectPreset {
                server: None,
                agent: Some("preset-agent".into()),
                model: Some("preset-model".into()),
                provider: Some("preset-provider".into()),
                max_steps: Some(42),
                context: None,
                shell: None,
                jail: None,
                jail_image: None,
                jail_docker: None,
                guard: None,
            },
            None,
        );

        super::run(
            &rt,
            RunArgs {
                task: Some("x".into()),
                agent: None,
                model: None,
                provider: "ollama".into(), // clap default — treated as sentinel
                max_steps: 10,             // clap default — treated as sentinel
                json: false,
                stream: false,
                resume: false,
                local: false,
                client_tools: false,
                client_tools_py: false,
            },
        )
        .await
        .unwrap();
    }

    #[tokio::test]
    async fn cli_flag_wins_over_preset() {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/api/agent/run"))
            .and(body_partial_json(
                serde_json::json!({ "agent": "explicit", "model": "explicit-model" }),
            ))
            .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
                "success": true,
                "output": "ok"
            })))
            .mount(&server)
            .await;

        let (mut rt, _d) = rt_with(&server.uri(), None);
        rt = rt.with_project(
            crate::project::ProjectPreset {
                server: None,
                agent: Some("preset".into()),
                model: Some("preset-m".into()),
                provider: None,
                max_steps: None,
                context: None,
                shell: None,
                jail: None,
                jail_image: None,
                jail_docker: None,
                guard: None,
            },
            None,
        );

        super::run(
            &rt,
            RunArgs {
                task: Some("x".into()),
                agent: Some("explicit".into()),
                model: Some("explicit-model".into()),
                provider: "ollama".into(),
                max_steps: 10,
                json: false,
                stream: false,
                resume: false,
                local: false,
                client_tools: false,
                client_tools_py: false,
            },
        )
        .await
        .unwrap();
    }

    #[tokio::test]
    async fn run_propagates_unauthorized() {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/api/agent/run"))
            .respond_with(ResponseTemplate::new(401))
            .mount(&server)
            .await;
        let (rt, _d) = rt_with(&server.uri(), None);
        let err = super::run(
            &rt,
            RunArgs {
                task: Some("x".into()),
                agent: Some("backend".into()),
                model: Some("m".into()),
                provider: "ollama".into(),
                max_steps: 10,
                json: false,
                stream: false,
                resume: false,
                local: false,
                client_tools: false,
                client_tools_py: false,
            },
        )
        .await
        .unwrap_err();
        assert!(matches!(err, AgoError::AuthRejected));
    }
}
