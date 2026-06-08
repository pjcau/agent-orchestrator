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
use rustyline::completion::{Completer, Pair};
use rustyline::error::ReadlineError;
use rustyline::highlight::Highlighter;
use rustyline::hint::Hinter;
use rustyline::history::FileHistory;
use rustyline::validate::Validator;
use rustyline::{Config as RlConfig, CompletionType, Context as RlContext, EditMode, Editor, Helper};
use std::borrow::Cow;
use std::io::IsTerminal;
use std::time::Duration;

const DEFAULT_PROVIDER: &str = "ollama";
const HISTORY_DIRNAME: &str = "ago";
const HISTORY_FILENAME: &str = "chat-history";

// ---------------------------------------------------------------------------
// slash-command catalog (single source of truth)
// ---------------------------------------------------------------------------

/// One row of the slash-command palette. The same list drives `:help`, the
/// Tab-completion dropdown, and the inline hinter — add a command here once
/// and it shows up everywhere.
struct SlashCmd {
    /// Canonical name, typed as `:<name>` (e.g. `model` → `:model`).
    name: &'static str,
    /// Argument placeholder shown in help / completion (empty if none).
    args: &'static str,
    /// One-line explanation.
    help: &'static str,
}

const SLASH_COMMANDS: &[SlashCmd] = &[
    SlashCmd { name: "mode", args: "<agent|prompt>", help: "switch routing (agent loop vs direct LLM)" },
    SlashCmd { name: "agent", args: "<name>", help: "switch agent (agent mode only)" },
    SlashCmd { name: "model", args: "<id>", help: "switch model" },
    SlashCmd { name: "provider", args: "<type>", help: "switch provider (anthropic / openai / openrouter / ollama / ...)" },
    SlashCmd { name: "max-steps", args: "<N>", help: "cap agent steps per turn (agent mode only)" },
    SlashCmd { name: "context", args: "", help: "show @file / @dir context limits" },
    SlashCmd { name: "cache", args: "<on|off|purge|status>", help: "manage prompt caching" },
    SlashCmd { name: "cost", args: "", help: "show accumulated tokens + cost since start / last :reset" },
    SlashCmd { name: "reset", args: "", help: "new conversation thread + zero the cost counter" },
    SlashCmd { name: "clear", args: "", help: "alias of :reset" },
    SlashCmd { name: "info", args: "", help: "show current settings" },
    SlashCmd { name: "help", args: "", help: "list slash commands" },
    SlashCmd { name: "quit", args: "", help: "leave the session" },
    SlashCmd { name: "exit", args: "", help: "alias of :quit" },
];

/// Render the `:help` block from [`SLASH_COMMANDS`] so help text can never
/// drift from the dispatch table or the completer.
fn render_help() -> String {
    // Width of the widest ":name <args>" label, for column alignment.
    let label = |c: &SlashCmd| {
        if c.args.is_empty() {
            format!(":{}", c.name)
        } else {
            format!(":{} {}", c.name, c.args)
        }
    };
    let width = SLASH_COMMANDS.iter().map(|c| label(c).len()).max().unwrap_or(0);
    let mut out = String::from("Slash commands (type ':' then Tab for a dropdown):\n");
    for c in SLASH_COMMANDS {
        out.push_str(&format!("  {:<width$}  {}\n", label(c), c.help, width = width));
    }
    // Trim the trailing newline so callers can `println!` cleanly.
    out.pop();
    out
}

// ---------------------------------------------------------------------------
// session cost accounting (cumulative from start → :reset)
// ---------------------------------------------------------------------------

/// Running totals for the whole chat session. Accumulated on every
/// successful turn and zeroed by `:reset` / `:clear`, so the figure always
/// reflects spend since the start of the session or the last reset.
#[derive(Default, Clone)]
struct SessionTotals {
    turns: u64,
    input_tokens: u64,
    output_tokens: u64,
    cost_usd: f64,
}

impl SessionTotals {
    fn add(&mut self, stats: &TurnStats) {
        self.turns += 1;
        self.input_tokens += stats.input_tokens;
        self.output_tokens += stats.output_tokens;
        self.cost_usd += stats.cost_usd;
    }

    /// Human-readable one-liner used by both the per-turn footer and `:cost`.
    fn summary(&self) -> String {
        if self.turns == 0 {
            return "session: no turns yet — 0↑/0↓ tokens · $0.0000".to_string();
        }
        format!(
            "session: {} turn(s) · {}↑/{}↓ tokens · ${:.4}",
            self.turns, self.input_tokens, self.output_tokens, self.cost_usd
        )
    }
}

// ---------------------------------------------------------------------------
// rustyline helper: Tab-completion dropdown for ':' commands + inline hint
// ---------------------------------------------------------------------------

/// Editor helper that turns the leading `:` into a discoverable command
/// palette: pressing Tab after `:` lists every slash command next to its
/// explanation, and an empty `:` shows a one-line "Tab to list" hint.
struct ChatHelper {
    no_color: bool,
}

impl Completer for ChatHelper {
    type Candidate = Pair;

    fn complete(
        &self,
        line: &str,
        pos: usize,
        _ctx: &RlContext<'_>,
    ) -> rustyline::Result<(usize, Vec<Pair>)> {
        // Only the command word (between ':' and the first space) completes.
        if !line.starts_with(':') || pos > line.len() {
            return Ok((pos, Vec::new()));
        }
        let after = &line[1..];
        if after.contains(char::is_whitespace) {
            // Command already chosen; we don't complete its arguments.
            return Ok((pos, Vec::new()));
        }
        let partial = &line[1..pos];
        let candidates = SLASH_COMMANDS
            .iter()
            .filter(|c| c.name.starts_with(partial))
            .map(|c| {
                let label = if c.args.is_empty() {
                    format!(":{}", c.name)
                } else {
                    format!(":{} {}", c.name, c.args)
                };
                Pair {
                    display: format!("{label:<30}{}", c.help),
                    // Replace from index 1 (just after the ':') with the bare
                    // command name and a trailing space, ready for an argument.
                    replacement: format!("{} ", c.name),
                }
            })
            .collect();
        Ok((1, candidates))
    }
}

impl Hinter for ChatHelper {
    type Hint = String;

    fn hint(&self, line: &str, pos: usize, _ctx: &RlContext<'_>) -> Option<String> {
        if line == ":" && pos == 1 {
            Some("  ⇥ Tab to list commands".to_string())
        } else {
            None
        }
    }
}

impl Highlighter for ChatHelper {
    fn highlight_hint<'h>(&self, hint: &'h str) -> Cow<'h, str> {
        if self.no_color {
            Cow::Borrowed(hint)
        } else {
            Cow::Owned(format!("\x1b[2m{hint}\x1b[0m"))
        }
    }
}

impl Validator for ChatHelper {}
impl Helper for ChatHelper {}

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
    let mut totals = SessionTotals::default();

    print_banner(rt, &settings, &conversation_id)?;

    let mut rl = build_editor(rt.no_color)?;
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
            // `:cost` is handled here rather than in `handle_slash` because it
            // reads the running totals that the REPL loop owns.
            if rest.split_whitespace().next() == Some("cost") {
                print_session_totals(&totals, rt.no_color, true);
                continue;
            }
            match handle_slash(rest, &mut settings, &mut conversation_id, rt) {
                SlashOutcome::Continue => continue,
                SlashOutcome::Quit => break,
                SlashOutcome::Reset => {
                    totals = SessionTotals::default();
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
                totals.add(&stats);
                // Cumulative spend since start / last :reset, dimmed under the
                // per-turn footer.
                print_session_totals(&totals, rt.no_color, false);
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
    }

    Ok(())
}

/// Print the cumulative session spend. `explicit` (`:cost`) goes to stdout in
/// plain text; the per-turn auto-report goes dimmed to stderr next to the
/// turn footer.
fn print_session_totals(totals: &SessionTotals, no_color: bool, explicit: bool) {
    let line = totals.summary();
    if explicit {
        println!("{line}");
        println!("(use :reset to zero the counter)");
    } else if no_color {
        eprintln!("Σ {line}");
    } else {
        eprintln!("\x1b[2mΣ {line}  (:reset to zero)\x1b[0m");
    }
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
    shell_allow: Vec<String>,
    shell_deny: Vec<String>,
    shell_allow_all: bool,
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
        let (shell_allow, shell_deny, shell_allow_all) = preset
            .and_then(|p| p.shell.as_ref())
            .map(|s| (s.allow.clone(), s.deny.clone(), s.allow_all))
            .unwrap_or_default();
        Ok(Self {
            mode: args.mode,
            agent,
            model,
            provider,
            max_steps,
            no_color: rt.no_color,
            shell_allow,
            shell_deny,
            shell_allow_all,
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
            println!("{}", render_help());
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
            println!("✓ new conversation_id = {conversation_id} · cost counter zeroed");
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

fn build_editor(no_color: bool) -> Result<Editor<ChatHelper, FileHistory>> {
    // `CompletionType::List` renders the matched commands as a multi-line
    // dropdown (name + explanation per row) instead of inline cycling.
    let cfg = RlConfig::builder()
        .edit_mode(EditMode::Emacs)
        .auto_add_history(false)
        .completion_type(CompletionType::List)
        .build();
    let mut editor: Editor<ChatHelper, FileHistory> =
        Editor::with_config(cfg).map_err(|e| AgoError::Other(format!("rustyline init: {e}")))?;
    editor.set_helper(Some(ChatHelper { no_color }));
    Ok(editor)
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
        &settings.shell_allow,
        &settings.shell_deny,
        settings.shell_allow_all,
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
            shell_allow: Vec::new(),
            shell_deny: Vec::new(),
            shell_allow_all: false,
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

    // --- session cost accounting -------------------------------------------

    #[test]
    fn session_totals_accumulate() {
        let mut t = SessionTotals::default();
        t.add(&TurnStats { input_tokens: 10, output_tokens: 5, cost_usd: 0.01 });
        t.add(&TurnStats { input_tokens: 20, output_tokens: 7, cost_usd: 0.02 });
        assert_eq!(t.turns, 2);
        assert_eq!(t.input_tokens, 30);
        assert_eq!(t.output_tokens, 12);
        assert!((t.cost_usd - 0.03).abs() < 1e-9);
    }

    #[test]
    fn session_summary_zero_and_nonzero() {
        let empty = SessionTotals::default();
        assert!(empty.summary().contains("no turns yet"));
        let mut t = SessionTotals::default();
        t.add(&TurnStats { input_tokens: 100, output_tokens: 40, cost_usd: 0.1234 });
        let s = t.summary();
        assert!(s.contains("1 turn(s)"));
        assert!(s.contains("100↑/40↓"));
        assert!(s.contains("$0.1234"));
    }

    #[test]
    fn reset_zeroes_totals() {
        // Mirrors the loop: SlashOutcome::Reset replaces totals with default.
        let mut t = SessionTotals::default();
        t.add(&TurnStats { input_tokens: 5, output_tokens: 5, cost_usd: 0.5 });
        t = SessionTotals::default();
        assert_eq!(t.turns, 0);
        assert!((t.cost_usd).abs() < 1e-9);
        assert!(t.summary().contains("no turns yet"));
    }

    // --- slash-command palette ---------------------------------------------

    #[test]
    fn help_lists_every_command() {
        let help = render_help();
        for c in SLASH_COMMANDS {
            assert!(help.contains(&format!(":{}", c.name)), "missing :{}", c.name);
            assert!(help.contains(c.help), "missing help for :{}", c.name);
        }
        assert!(help.contains("Tab"));
    }

    fn complete(line: &str, pos: usize) -> (usize, Vec<Pair>) {
        let helper = ChatHelper { no_color: true };
        let hist = FileHistory::new();
        let ctx = RlContext::new(&hist);
        helper.complete(line, pos, &ctx).unwrap()
    }

    #[test]
    fn completer_lists_all_on_bare_colon() {
        let (start, pairs) = complete(":", 1);
        assert_eq!(start, 1);
        assert_eq!(pairs.len(), SLASH_COMMANDS.len());
        // Display carries the explanation, replacement carries a bare name.
        assert!(pairs.iter().any(|p| p.display.contains("switch model")));
        assert!(pairs.iter().any(|p| p.replacement == "model "));
    }

    #[test]
    fn completer_filters_by_prefix() {
        let (start, pairs) = complete(":mo", 3);
        assert_eq!(start, 1);
        let names: Vec<&str> = pairs.iter().map(|p| p.replacement.trim()).collect();
        assert!(names.contains(&"mode"));
        assert!(names.contains(&"model"));
        assert!(!names.contains(&"reset"));
    }

    #[test]
    fn completer_stops_after_argument_space() {
        // Once a command word is chosen we no longer offer command names.
        let (_start, pairs) = complete(":model ", 7);
        assert!(pairs.is_empty());
    }

    #[test]
    fn completer_ignores_non_slash_input() {
        let (_start, pairs) = complete("hello", 5);
        assert!(pairs.is_empty());
    }

    #[test]
    fn hinter_prompts_tab_on_bare_colon() {
        let helper = ChatHelper { no_color: true };
        let hist = FileHistory::new();
        let ctx = RlContext::new(&hist);
        assert!(helper.hint(":", 1, &ctx).is_some());
        assert!(helper.hint(":m", 2, &ctx).is_none());
        assert!(helper.hint("hello", 5, &ctx).is_none());
    }
}
