use crate::cli::RunArgs;
use crate::client::{AgentRunRequest, AgentRunResponse};
use crate::error::{AgoError, Result};
use crate::runtime::Runtime;
use std::io::{IsTerminal, Read};

pub async fn run(rt: &Runtime, args: RunArgs) -> Result<()> {
    let task = resolve_task(args.task.as_deref())?;
    let agent = args
        .agent
        .as_deref()
        .or(rt.config.default_agent.as_deref())
        .ok_or_else(|| {
            AgoError::Config(
                "no agent specified — pass --agent NAME or set default_agent in config".into(),
            )
        })?;
    let model = args.model.as_deref().ok_or_else(|| {
        AgoError::Config("no model specified — pass --model ID (e.g. claude-sonnet-4-6)".into())
    })?;

    let client = rt.api_client()?;
    let resp = client
        .agent_run(&AgentRunRequest {
            agent,
            task: &task,
            model,
            provider: &args.provider,
            max_steps: args.max_steps,
        })
        .await?;

    if args.json {
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
            },
        )
        .await
        .unwrap_err();
        assert!(matches!(err, AgoError::Other(_)));
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
            },
        )
        .await
        .unwrap_err();
        assert!(matches!(err, AgoError::AuthRejected));
    }
}
