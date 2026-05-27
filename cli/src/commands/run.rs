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
    let (task, report) = expand_refs(&raw_task, &cwd, &ContextConfig::default())?;
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

    let client = rt.api_client()?;
    let req = AgentRunRequest {
        agent: &agent_string,
        task: &task,
        model: &model_string,
        provider: &provider_string,
        max_steps,
        conversation_id: None,
    };

    if args.stream {
        run_streaming(&client, &req, args.json).await
    } else {
        run_blocking(&client, &req, args.json).await
    }
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
) -> Result<()> {
    let resp = client.agent_run(req).await?;
    if json {
        let stdout = std::io::stdout();
        serde_json::to_writer(&stdout, &resp.raw).map_err(|e| AgoError::Other(e.to_string()))?;
        println!();
    } else {
        render_human(&resp);
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
        render_human(&resp);
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

fn render_human(resp: &AgentRunResponse) {
    if let Some(output) = resp.output() {
        println!("{output}");
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
            },
        )
        .await
        .unwrap_err();
        assert!(matches!(err, AgoError::AuthRejected));
    }
}
