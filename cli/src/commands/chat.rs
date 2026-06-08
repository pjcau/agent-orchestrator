//! `ago chat` — interactive REPL.
//!
//! Boots a rustyline editor, opens a fresh conversation_id, and loops:
//! read input → dispatch slash command OR forward to `/api/cli/v1/run`
//! (streaming) → render. Settings (agent / model / provider / max_steps)
//! are resolved once at startup from CLI flags > `.ago.yaml` > config and
//! can be flipped on the fly with `:agent`, `:model`, `:provider`,
//! `:max-steps`. `:reset` mints a new conversation_id, `:clear` is an
//! alias. `:info`/`:help`/`:quit` round out the v0.2 surface.

use crate::cli::{ChatArgs, ChatMode};
use crate::client::{AgentRunRequest, AgentRunResponse, ApiClient, PromptRequest, RunEvent};
use crate::context::{expand_refs, ContextConfig, ExpandReport, SkipReason};
use crate::error::{AgoError, Result};
use crate::runtime::Runtime;
use futures_util::StreamExt;
use indicatif::{ProgressBar, ProgressStyle};
use rustyline::error::ReadlineError;
use rustyline::{Config as RlConfig, DefaultEditor, EditMode};
use std::io::IsTerminal;
use std::time::Duration;

const DEFAULT_PROVIDER: &str = "ollama";
const HISTORY_DIRNAME: &str = "ago";
const HISTORY_FILENAME: &str = "chat-history";

pub async fn run(rt: &Runtime, args: ChatArgs) -> Result<()> {
    if args.client_tools || args.client_tools_py {
        // Agent-host mode. Same auth gate as the regular REST path so
        // failure messages match.
        let settings = ChatSettings::resolve(rt, &args)?;
        let server_url = rt.server_url()?.to_string();
        let _ = rt.api_client()?;
        let token_secret = rt
            .storage
            .load(&server_url)?
            .ok_or(AgoError::NotAuthenticated)?;
        use secrecy::ExposeSecret;
        let token = token_secret.expose_secret();

        if args.client_tools_py {
            // Transitional fallback — spawn `python -m
            // agent_orchestrator.agent_host`. Documented as hidden
            // because users should default to the native client.
            return spawn_agent_host(&server_url, token, &settings).await;
        }
        // Native Rust client — no Python dependency.
        return run_native_agent_host(&server_url, token, &settings).await;
    }
    let mut settings = ChatSettings::resolve(rt, &args)?;
    let client = rt.api_client()?;
    let server_url = rt.server_url()?.to_string();
    // --resume: pick up the most recent conversation_id we saw on this
    // exact server URL. If nothing is stored yet we silently fall through
    // to a fresh UUID so first-time `--resume` is not an error.
    let mut conversation_id = if args.resume {
        match rt.state.last_conversation_for(&server_url) {
            Some(id) => {
                eprintln!("\x1b[2m· resuming conversation {id}\x1b[0m");
                id.to_string()
            }
            None => {
                eprintln!(
                    "\x1b[33m· --resume: no prior conversation for {server_url}, starting fresh\x1b[0m"
                );
                uuid::Uuid::new_v4().to_string()
            }
        }
    } else {
        uuid::Uuid::new_v4().to_string()
    };
    let mut turn_count: u64 = 0;
    let mut total_input_tokens: u64 = 0;
    let mut total_output_tokens: u64 = 0;
    let mut total_cost_usd: f64 = 0.0;

    print_banner(rt, &settings, &conversation_id)?;

    let mut rl = build_editor()?;
    let history_path = ensure_history_path();
    if let Some(p) = &history_path {
        let _ = rl.load_history(p);
    }

    loop {
        let line = match rl.readline("> ") {
            Ok(l) => l,
            Err(ReadlineError::Eof) => {
                eprintln!("\n(bye)");
                break;
            }
            Err(ReadlineError::Interrupted) => {
                // Empty line on Ctrl-C; second Ctrl-C exits if buffer empty.
                continue;
            }
            Err(e) => return Err(AgoError::Other(format!("readline: {e}"))),
        };
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        if let Some(p) = &history_path {
            let _ = rl.add_history_entry(trimmed);
            let _ = rl.save_history(p);
        }

        // ---- slash commands ----
        if let Some(rest) = trimmed.strip_prefix(':') {
            match handle_slash(rest, &mut settings, &mut conversation_id, rt) {
                SlashOutcome::Continue => continue,
                SlashOutcome::Quit => break,
                SlashOutcome::Reset => {
                    turn_count = 0;
                    total_input_tokens = 0;
                    total_output_tokens = 0;
                    total_cost_usd = 0.0;
                    continue;
                }
            }
        }

        // ---- @file / @dir expansion (client-side, before sending) ----
        let cwd = std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from("."));
        let ctx_cfg = ContextConfig::from_runtime(rt);
        let (user_prompt, refs_cache, report) = match expand_refs(trimmed, &cwd, &ctx_cfg) {
            Ok(triple) => triple,
            Err(e) => {
                eprintln!("\x1b[31merror:\x1b[0m @ref expansion failed: {e}");
                continue;
            }
        };
        if !report.resolved.is_empty() || !report.skipped.is_empty() {
            print_expand_report(&report);
        }
        // Merge AGO.md (slow-changing project instructions, if any) with
        // the per-turn @ref content. AGO.md goes first so the byte prefix
        // stays stable across turns — the OpenRouter prompt cache only
        // discounts a *common* prefix.
        let cache_context = crate::instructions::Instructions::merge_with_refs(
            rt.instructions.as_ref(),
            &refs_cache,
        );
        // Only send cache_context to the server when caching is enabled.
        // Otherwise concat into the prompt as v0.3.x did — server has no
        // way to discount the tokens but everything still works.
        let (final_prompt, server_cache) =
            if rt.config.cache_is_enabled() && !cache_context.is_empty() {
                (user_prompt, Some(cache_context))
            } else if !cache_context.is_empty() {
                (format!("{user_prompt}\n\n---\n{cache_context}"), None)
            } else {
                (user_prompt, None)
            };

        // ---- forward to server ----
        match send_turn(
            &client,
            &settings,
            &conversation_id,
            &final_prompt,
            server_cache.as_deref(),
            args.no_progress,
            rt.no_color,
        )
        .await
        {
            Ok(stats) => {
                turn_count += 1;
                total_input_tokens += stats.input_tokens;
                total_output_tokens += stats.output_tokens;
                total_cost_usd += stats.cost_usd;
                // Persist the conversation id so the next `--resume` picks
                // it up. Best-effort: a state.toml write failure (e.g. RO
                // home directory in CI) must not kill the REPL.
                if let Err(e) = crate::state::persist_conversation(
                    &rt.state_path,
                    &server_url,
                    &conversation_id,
                ) {
                    tracing::debug!("state.toml save failed: {e}");
                }
            }
            Err(AgoError::AuthRejected) => {
                eprintln!(
                    "\x1b[31merror:\x1b[0m server rejected the stored token. Run `ago login` and try again."
                );
                break;
            }
            Err(e) => {
                eprintln!("\x1b[31merror:\x1b[0m {e}");
                // Stay in the REPL — transient errors should not kill the session.
            }
        }

        let _ = turn_count;
        let _ = total_input_tokens;
        let _ = total_output_tokens;
        let _ = total_cost_usd;
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// settings + slash command state
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
struct ChatSettings {
    mode: ChatMode,
    agent: String,
    model: String,
    provider: String,
    max_steps: u32,
    no_color: bool,
}

impl ChatSettings {
    fn resolve(rt: &Runtime, args: &ChatArgs) -> Result<Self> {
        let preset = rt.project.as_ref();
        // Agent is mandatory in `agent` mode, optional in `prompt` mode.
        // We still resolve a default so `:mode agent` later works without
        // forcing the user to restart.
        let agent = args
            .agent
            .clone()
            .or_else(|| preset.and_then(|p| p.agent.clone()))
            .or_else(|| rt.config.default_agent.clone())
            .unwrap_or_else(|| "backend".to_string());
        let model = args
            .model
            .clone()
            .or_else(|| preset.and_then(|p| p.model.clone()))
            .ok_or_else(|| {
                AgoError::Config("no model — pass --model or set model in .ago.yaml".into())
            })?;
        let provider = args
            .provider
            .clone()
            .or_else(|| preset.and_then(|p| p.provider.clone()))
            .unwrap_or_else(|| DEFAULT_PROVIDER.to_string());
        let max_steps = preset.and_then(|p| p.max_steps).unwrap_or(args.max_steps);
        Ok(Self {
            mode: args.mode,
            agent,
            model,
            provider,
            max_steps,
            no_color: rt.no_color,
        })
    }
}

enum SlashOutcome {
    Continue,
    Quit,
    Reset,
}

fn handle_slash(
    body: &str,
    settings: &mut ChatSettings,
    conversation_id: &mut String,
    rt: &Runtime,
) -> SlashOutcome {
    let mut parts = body.splitn(2, char::is_whitespace);
    let cmd = parts.next().unwrap_or("").trim();
    let arg = parts.next().unwrap_or("").trim();

    match cmd {
        "" | "help" => {
            println!(
                "Slash commands:\n\
                 \x20 :mode <agent|prompt>  switch routing (agent loop vs direct LLM)\n\
                 \x20 :agent <name>         switch agent (agent mode only)\n\
                 \x20 :model <id>           switch model\n\
                 \x20 :provider <type>      switch provider (anthropic / openai / openrouter / ollama / ...)\n\
                 \x20 :max-steps <N>        cap agent steps per turn (agent mode only)\n\
                 \x20 :context              show @file / @dir context limits\n\
                 \x20 :cache <on|off|purge|status>  manage prompt caching (v0.4.1 wires server)\n\
                 \x20 :reset                start a new conversation thread\n\
                 \x20 :clear                alias of :reset\n\
                 \x20 :info                 show current settings\n\
                 \x20 :help                 this message\n\
                 \x20 :quit / :exit         leave the session"
            );
            SlashOutcome::Continue
        }
        "quit" | "exit" => SlashOutcome::Quit,
        "info" => {
            println!(
                "mode: {}\nagent: {}\nmodel: {}\nprovider: {}\nmax_steps: {}\nconversation_id: {}",
                settings.mode,
                settings.agent,
                settings.model,
                settings.provider,
                settings.max_steps,
                conversation_id
            );
            SlashOutcome::Continue
        }
        "cache" => {
            // Live-toggle the cache hint without leaving chat. Persisted to
            // the user config so it survives the session.
            let cur = rt.config.cache_is_enabled();
            match arg {
                "on" | "enable" | "true" => {
                    let mut cfg = rt.config.clone();
                    if cfg.set("cache_enabled", "true").is_ok() {
                        let _ = cfg.save(&rt.config_path);
                    }
                    println!("✓ cache = on");
                }
                "off" | "disable" | "false" => {
                    let mut cfg = rt.config.clone();
                    if cfg.set("cache_enabled", "false").is_ok() {
                        let _ = cfg.save(&rt.config_path);
                    }
                    println!("✓ cache = off");
                }
                "purge" => {
                    // Equivalent to :reset for the purposes of cache TTL —
                    // the provider sees a fresh prefix on the next turn.
                    *conversation_id = uuid::Uuid::new_v4().to_string();
                    println!("✓ cache purged — new conversation_id = {conversation_id}");
                    return SlashOutcome::Reset;
                }
                "status" | "" => {
                    println!("cache: {} (config: {})", cur, rt.config_path.display());
                    println!("note: server-side cache_control insertion lands in v0.4.1");
                }
                other => {
                    eprintln!("usage: :cache <on|off|purge|status>  (got {other})");
                }
            }
            SlashOutcome::Continue
        }
        "context" | "ctx" => {
            let cfg = ContextConfig::from_runtime(rt);
            println!(
                "max_file_bytes: {}\nmax_total_bytes: {}\nmax_refs: {}",
                cfg.max_file_bytes, cfg.max_total_bytes, cfg.max_refs
            );
            if let Some(path) = rt.project_path.as_ref() {
                println!("from: {} (.ago.yaml context: block)", path.display());
            } else {
                println!("from: built-in defaults (no .ago.yaml found)");
            }
            SlashOutcome::Continue
        }
        "mode" => {
            match arg {
                "agent" => {
                    settings.mode = ChatMode::Agent;
                    println!("✓ mode = agent (tool-using agent loop)");
                }
                "prompt" => {
                    settings.mode = ChatMode::Prompt;
                    println!("✓ mode = prompt (direct LLM, no tools)");
                }
                "" => {
                    eprintln!("usage: :mode <agent|prompt>");
                }
                other => {
                    eprintln!("unknown mode: {other} (try agent or prompt)");
                }
            }
            SlashOutcome::Continue
        }
        "agent" => {
            if arg.is_empty() {
                eprintln!("usage: :agent <name>");
            } else {
                settings.agent = arg.to_string();
                println!("✓ agent = {}", settings.agent);
            }
            SlashOutcome::Continue
        }
        "model" => {
            if arg.is_empty() {
                eprintln!("usage: :model <id>");
            } else {
                settings.model = arg.to_string();
                println!("✓ model = {}", settings.model);
            }
            SlashOutcome::Continue
        }
        "provider" => {
            if arg.is_empty() {
                eprintln!("usage: :provider <type>");
            } else {
                settings.provider = arg.to_string();
                println!("✓ provider = {}", settings.provider);
            }
            SlashOutcome::Continue
        }
        "max-steps" | "maxsteps" => match arg.parse::<u32>() {
            Ok(n) if (1..=200).contains(&n) => {
                settings.max_steps = n;
                println!("✓ max_steps = {n}");
                SlashOutcome::Continue
            }
            _ => {
                eprintln!("usage: :max-steps <N>  (1..=200)");
                SlashOutcome::Continue
            }
        },
        "reset" | "clear" => {
            *conversation_id = uuid::Uuid::new_v4().to_string();
            println!("✓ new conversation_id = {}", conversation_id);
            SlashOutcome::Reset
        }
        other => {
            eprintln!("unknown slash command: :{other} (try :help)");
            SlashOutcome::Continue
        }
    }
}

// ---------------------------------------------------------------------------
// per-turn networking
// ---------------------------------------------------------------------------

#[derive(Default)]
struct TurnStats {
    input_tokens: u64,
    output_tokens: u64,
    cost_usd: f64,
}

async fn send_turn(
    client: &ApiClient,
    settings: &ChatSettings,
    conversation_id: &str,
    task: &str,
    cache_context: Option<&str>,
    no_progress: bool,
    no_color: bool,
) -> Result<TurnStats> {
    match settings.mode {
        ChatMode::Agent => {
            send_turn_agent(
                client,
                settings,
                conversation_id,
                task,
                cache_context,
                no_progress,
                no_color,
            )
            .await
        }
        ChatMode::Prompt => {
            send_turn_prompt(
                client,
                settings,
                conversation_id,
                task,
                cache_context,
                no_progress,
                no_color,
            )
            .await
        }
    }
}

async fn send_turn_prompt(
    client: &ApiClient,
    settings: &ChatSettings,
    conversation_id: &str,
    prompt: &str,
    cache_context: Option<&str>,
    no_progress: bool,
    no_color: bool,
) -> Result<TurnStats> {
    let show_progress = !no_progress && std::io::stderr().is_terminal();
    let spinner = show_progress.then(|| {
        let pb = make_spinner();
        pb.set_message(format!(
            "[prompt] {} via {}",
            settings.model, settings.provider
        ));
        pb
    });
    let req = PromptRequest {
        prompt,
        model: &settings.model,
        provider: &settings.provider,
        conversation_id: Some(conversation_id),
        cache_context,
    };
    let resp = client.prompt(&req).await;
    if let Some(pb) = spinner {
        pb.finish_and_clear();
    }
    let resp = resp?;
    if let Some(output) = resp.output() {
        println!("{}", crate::render::highlight(output, no_color));
    }
    if let Some(err) = resp.error() {
        eprintln!("error: {err}");
    }
    let stats = TurnStats {
        input_tokens: resp.input_tokens().unwrap_or(0),
        output_tokens: resp.output_tokens().unwrap_or(0),
        cost_usd: resp.cost_usd().unwrap_or(0.0),
    };
    if let Some(elapsed) = resp.elapsed_s() {
        let mut footer = format!("— {elapsed:.2}s");
        if stats.input_tokens + stats.output_tokens > 0 {
            footer.push_str(&format!(
                "  {}↑/{}↓ tokens",
                stats.input_tokens, stats.output_tokens
            ));
        }
        if stats.cost_usd > 0.0 {
            footer.push_str(&format!("  ${:.4}", stats.cost_usd));
        }
        eprintln!("{footer}");
    }
    Ok(stats)
}

async fn send_turn_agent(
    client: &ApiClient,
    settings: &ChatSettings,
    conversation_id: &str,
    task: &str,
    cache_context: Option<&str>,
    no_progress: bool,
    no_color: bool,
) -> Result<TurnStats> {
    let req = AgentRunRequest {
        agent: &settings.agent,
        task,
        model: &settings.model,
        provider: &settings.provider,
        max_steps: settings.max_steps,
        conversation_id: Some(conversation_id),
        cache_context,
    };
    let show_progress = !no_progress && std::io::stderr().is_terminal();
    let spinner = show_progress.then(make_spinner);
    let mut step: u64 = 0;
    let mut final_payload: Option<serde_json::Value> = None;

    let mut stream = client.agent_run_stream(&req).await?;
    while let Some(item) = stream.next().await {
        let event = item?;
        if event.event == "complete" {
            final_payload = Some(event.data);
            break;
        }
        step += 1;
        if let Some(pb) = &spinner {
            update_spinner(pb, &event.event, &event, step);
        }
    }
    if let Some(pb) = spinner {
        pb.finish_and_clear();
    }
    let payload = final_payload
        .ok_or_else(|| AgoError::Other("server closed stream before completion".into()))?;
    let resp = AgentRunResponse { raw: payload };

    if let Some(output) = resp.output() {
        println!("{}", crate::render::highlight(output, no_color));
    }
    if let Some(err) = resp.error() {
        eprintln!("error: {err}");
    }
    let stats = TurnStats {
        input_tokens: resp.total_input_tokens().unwrap_or(0),
        output_tokens: resp.total_output_tokens().unwrap_or(0),
        cost_usd: resp.total_cost_usd().unwrap_or(0.0),
    };
    if let Some(elapsed) = resp.elapsed_s() {
        let mut footer = format!("— {elapsed:.2}s");
        if stats.input_tokens + stats.output_tokens > 0 {
            footer.push_str(&format!(
                "  {}↑/{}↓ tokens",
                stats.input_tokens, stats.output_tokens
            ));
        }
        if stats.cost_usd > 0.0 {
            footer.push_str(&format!("  ${:.4}", stats.cost_usd));
        }
        eprintln!("{footer}");
    }
    Ok(stats)
}

fn print_expand_report(report: &ExpandReport) {
    for r in &report.resolved {
        let kind = match r.kind {
            crate::context::RefKind::File => "file",
            crate::context::RefKind::Directory => "dir",
            crate::context::RefKind::DirectoryRecursive => "dir/**",
        };
        let trunc = if r.truncated { " [truncated]" } else { "" };
        if let Some(stats) = r.recursive.as_ref() {
            let mut extras = Vec::new();
            if stats.excluded > 0 {
                extras.push(format!("{} excluded", stats.excluded));
            }
            if stats.truncated_files > 0 {
                extras.push(format!("{} file(s) truncated", stats.truncated_files));
            }
            if stats.stopped_files {
                extras.push("hit max_dir_files".into());
            }
            if stats.stopped_bytes {
                extras.push("hit max_total_bytes".into());
            }
            let extras_s = if extras.is_empty() {
                String::new()
            } else {
                format!(" — {}", extras.join(", "))
            };
            eprintln!(
                "\x1b[2m· included {kind} {} ({} files, {} B{trunc}{extras_s})\x1b[0m",
                r.token, stats.files_inlined, r.bytes
            );
        } else {
            eprintln!(
                "\x1b[2m· included {kind} {} ({} B{trunc})\x1b[0m",
                r.token, r.bytes
            );
        }
    }
    for s in &report.skipped {
        let reason = match &s.reason {
            SkipReason::NotFound => "not found".to_string(),
            SkipReason::Excluded => "excluded by safety pattern".to_string(),
            SkipReason::TooManyRefs => "max @refs per turn reached".to_string(),
            SkipReason::TotalSizeExceeded => "total context size cap reached".to_string(),
            SkipReason::ReadError(e) => format!("read error: {e}"),
        };
        eprintln!("\x1b[33m· skipped {} — {reason}\x1b[0m", s.token);
    }
}

fn make_spinner() -> ProgressBar {
    let pb = ProgressBar::new_spinner();
    pb.set_style(
        ProgressStyle::with_template("{spinner:.cyan} {elapsed_precise} {prefix:.dim} {msg}")
            .unwrap()
            .tick_chars("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏ "),
    );
    pb.enable_steady_tick(Duration::from_millis(100));
    pb.set_prefix("thinking");
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

// ---------------------------------------------------------------------------
// banner + readline boilerplate
// ---------------------------------------------------------------------------

fn print_banner(rt: &Runtime, s: &ChatSettings, conv: &str) -> Result<()> {
    let server = rt.server_url()?;
    println!("ago {} — chat mode", env!("CARGO_PKG_VERSION"));
    println!("connected to {server}");
    println!(
        "mode: {} · agent: {} · model: {} · provider: {} · max_steps: {}",
        s.mode, s.agent, s.model, s.provider, s.max_steps
    );
    if let Some(doc) = rt.instructions.as_ref() {
        println!(
            "instructions: {} ({} B{})",
            doc.path.display(),
            doc.content.len(),
            if doc.truncated { ", truncated" } else { "" }
        );
    }
    println!("conversation: {} · type :help for slash commands", conv);
    println!();
    Ok(())
}

fn build_editor() -> Result<DefaultEditor> {
    let cfg = RlConfig::builder()
        .edit_mode(EditMode::Emacs)
        .auto_add_history(false)
        .build();
    DefaultEditor::with_config(cfg).map_err(|e| AgoError::Other(format!("rustyline init: {e}")))
}

fn ensure_history_path() -> Option<std::path::PathBuf> {
    let dirs = directories::ProjectDirs::from("io", "agent-orchestrator", HISTORY_DIRNAME)?;
    let dir = dirs.data_local_dir();
    if std::fs::create_dir_all(dir).is_err() {
        return None;
    }
    Some(dir.join(HISTORY_FILENAME))
}

// ---------------------------------------------------------------------------
// Agent-host subprocess dispatch (see docs/agent-host.md).
// ---------------------------------------------------------------------------

/// Spawn `python -m agent_orchestrator.agent_host` with the resolved
/// settings, inherit stdio, and wait for it to finish. The Python
/// subprocess owns the WebSocket, the local tool execution, and the
/// REPL — this Rust function is just a thin launcher.
///
/// Why not embed the WS client in Rust? See `docs/agent-host.md`
/// "Roll-back plan": the skill/sandbox/allowlist logic lives in
/// `src/agent_orchestrator/skills/` and `src/agent_orchestrator/agent_host/`
/// in Python; re-implementing it in Rust would duplicate the trust
/// boundary code. The subprocess approach keeps a single source of truth
/// — same pattern `ago run --local` already uses.
async fn spawn_agent_host(server_url: &str, token: &str, settings: &ChatSettings) -> Result<()> {
    use tokio::process::Command;
    let python = pick_python_for_agent_host();
    let cwd = std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from("."));

    eprintln!("\x1b[2m· spawning {python} -m agent_orchestrator.agent_host (client-tools)\x1b[0m");
    let status = Command::new(&python)
        .args([
            "-m",
            "agent_orchestrator.agent_host",
            "--server",
            server_url,
            "--cwd",
            cwd.to_string_lossy().as_ref(),
            "--agent",
            &settings.agent,
            "--model",
            &settings.model,
            "--provider",
            &settings.provider,
        ])
        // Token via env so it never appears in `ps`/`/proc/<pid>/cmdline`.
        .env("AGO_API_KEY", token)
        .stdin(std::process::Stdio::inherit())
        .stdout(std::process::Stdio::inherit())
        .stderr(std::process::Stdio::inherit())
        .status()
        .await
        .map_err(|e| {
            AgoError::Other(format!(
                "could not spawn {python}: {e}. Install with `pip install agent-orchestrator`."
            ))
        })?;

    if !status.success() {
        return Err(AgoError::Other(format!(
            "agent-host subprocess exited with status {status}"
        )));
    }
    Ok(())
}

fn pick_python_for_agent_host() -> String {
    std::env::var("AGO_PYTHON").unwrap_or_else(|_| "python3".to_string())
}

/// Native agent-host client — no Python subprocess.
///
/// Opens the WebSocket from inside the Rust binary, runs the handshake,
/// then transfers control to the REPL loop in
/// [`crate::agent_host::client::run_repl`]. Everything that used to
/// happen inside `python -m agent_orchestrator.agent_host` now lives
/// in `cli/src/agent_host/`.
async fn run_native_agent_host(
    server_url: &str,
    token: &str,
    settings: &ChatSettings,
) -> Result<()> {
    use crate::agent_host::client::{connect, run_repl, ClientConfig, StdinShellConfirmer};
    eprintln!("\x1b[2m· agent-host (native) connecting to {server_url}\x1b[0m");
    let mut ws = connect(server_url, token)
        .await
        .map_err(|e| AgoError::Other(format!("agent-host connect failed: {e:#}")))?;
    let cwd = std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from("."));
    let cfg = ClientConfig {
        agent: settings.agent.clone(),
        model: settings.model.clone(),
        provider: settings.provider.clone(),
        max_steps: settings.max_steps as u64,
        stream_shell: true,
    };
    let session = ws
        .handshake(&cwd, &cfg)
        .await
        .map_err(|e| AgoError::Other(format!("agent-host handshake failed: {e:#}")))?;
    eprintln!(
        "\x1b[2m· connected run_id={} agent={} model={}\x1b[0m",
        session.run_id,
        if session.agent.is_empty() {
            "-"
        } else {
            &session.agent
        },
        if session.model.is_empty() {
            "-"
        } else {
            &session.model
        }
    );
    run_repl(
        ws,
        session,
        cwd,
        Some(Box::new(StdinShellConfirmer)),
        settings.no_color,
    )
    .await
    .map_err(|e| AgoError::Other(format!("agent-host repl error: {e:#}")))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::auth::MemoryStorage;
    use crate::config::Config;
    use std::sync::Arc;
    use tempfile::tempdir;

    fn cs() -> ChatSettings {
        ChatSettings {
            mode: ChatMode::Agent,
            agent: "a".into(),
            model: "m".into(),
            provider: "ollama".into(),
            max_steps: 10,
            no_color: false,
        }
    }

    fn dummy_rt() -> (Runtime, tempfile::TempDir) {
        let dir = tempdir().unwrap();
        let cfg_path = dir.path().join("cfg.toml");
        let storage = Arc::new(MemoryStorage::new());
        (
            Runtime::with_components(Config::default(), cfg_path, storage),
            dir,
        )
    }

    #[test]
    fn slash_mode_switch() {
        let mut s = cs();
        let (rt, _d) = dummy_rt();
        let mut c = "abc".to_string();
        handle_slash("mode prompt", &mut s, &mut c, &rt);
        assert_eq!(s.mode, ChatMode::Prompt);
        handle_slash("mode agent", &mut s, &mut c, &rt);
        assert_eq!(s.mode, ChatMode::Agent);
    }

    #[test]
    fn slash_mode_unknown_keeps_current() {
        let mut s = cs();
        let (rt, _d) = dummy_rt();
        let mut c = "abc".to_string();
        handle_slash("mode bogus", &mut s, &mut c, &rt);
        assert_eq!(s.mode, ChatMode::Agent);
        handle_slash("mode", &mut s, &mut c, &rt);
        assert_eq!(s.mode, ChatMode::Agent);
    }

    #[test]
    fn slash_quit_exits() {
        let mut s = cs();
        let (rt, _d) = dummy_rt();
        let mut c = "abc".to_string();
        assert!(matches!(
            handle_slash("quit", &mut s, &mut c, &rt),
            SlashOutcome::Quit
        ));
        assert!(matches!(
            handle_slash("exit", &mut s, &mut c, &rt),
            SlashOutcome::Quit
        ));
    }

    #[test]
    fn slash_model_updates_settings() {
        let mut s = cs();
        let (rt, _d) = dummy_rt();
        let mut c = "abc".to_string();
        handle_slash("model qwen2.5:3b", &mut s, &mut c, &rt);
        assert_eq!(s.model, "qwen2.5:3b");
    }

    #[test]
    fn slash_model_no_arg_is_noop() {
        let mut s = cs();
        let (rt, _d) = dummy_rt();
        let mut c = "abc".to_string();
        handle_slash("model", &mut s, &mut c, &rt);
        assert_eq!(s.model, "m");
    }

    #[test]
    fn slash_reset_mints_new_conversation_id() {
        let mut s = cs();
        let (rt, _d) = dummy_rt();
        let mut c = "abc".to_string();
        let outcome = handle_slash("reset", &mut s, &mut c, &rt);
        assert!(matches!(outcome, SlashOutcome::Reset));
        assert_ne!(c, "abc");
    }

    #[test]
    fn slash_max_steps_range_check() {
        let mut s = cs();
        let (rt, _d) = dummy_rt();
        let mut c = "abc".to_string();
        handle_slash("max-steps 0", &mut s, &mut c, &rt);
        assert_eq!(s.max_steps, 10);
        handle_slash("max-steps 5", &mut s, &mut c, &rt);
        assert_eq!(s.max_steps, 5);
        handle_slash("max-steps 9999", &mut s, &mut c, &rt);
        assert_eq!(s.max_steps, 5);
    }

    #[test]
    fn slash_unknown_does_not_crash() {
        let mut s = cs();
        let (rt, _d) = dummy_rt();
        let mut c = "abc".to_string();
        let outcome = handle_slash("nonsense", &mut s, &mut c, &rt);
        assert!(matches!(outcome, SlashOutcome::Continue));
    }
}
