//! Local tool runner for the native agent-host client.
//!
//! Mirrors the Python `LocalToolRunner` in
//! `src/agent_orchestrator/agent_host/client.py`. The runner owns:
//!
//! * `file_read` / `file_write` — strict path sandbox via
//!   [`super::sandbox::enforce_workspace`].
//! * `shell_exec` — `tokio::process::Command` with argv as a list
//!   (`shell=True` is refused at protocol layer), allowlist gated,
//!   per-call timeout, optional streaming via `emit_chunk`, optional
//!   cancellation via `cancel_event`.
//!
//! The runner produces structured [`ToolOutcome`] values that
//! [`super::client`] maps onto signed `tool_result` / `tool_chunk`
//! frames. Keeping the runner transport-agnostic makes unit testing
//! straightforward (no WS, no signature plumbing).

use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Arc;
use std::time::{Duration, Instant};
use thiserror::Error;
use tokio::fs;
use tokio::io::AsyncReadExt;
use tokio::process::Command;
use tokio::sync::Notify;
use tracing::debug;

use super::allowlist::{is_high_risk, AllowlistError, ShellAllowlist};
use super::sandbox::{enforce_workspace, SandboxError};

/// Default per-shell-call timeout matches the Python implementation.
pub const SHELL_DEFAULT_TIMEOUT: Duration = Duration::from_secs(60);

/// Grace period for a long-running server command (dev server, `compose up`,
/// `uvicorn`, …). Such commands never exit on their own, so blocking on them to
/// the full `shell_timeout` only ever yields a misleading `shell_timeout`
/// failure even though the server started fine. Instead we wait this short grace
/// for an early crash; if it is still alive afterwards we report success
/// ("started, still running"), leave it running in the background, and drain its
/// pipes so it never stalls. See `is_long_running_command`.
pub const LONG_RUNNING_GRACE: Duration = Duration::from_secs(8);

/// Cap on aggregate shell output (stdout + stderr) per call.
pub const SHELL_OUTPUT_CAP: usize = 10 * 1024 * 1024;

/// Chunk size for `emit_chunk` streaming.
pub const SHELL_CHUNK_BYTES: usize = 4 * 1024;

/// Default idle / no-output window for an UNKNOWN command: once a live process
/// has produced no new output for this long it is treated as settled (a server
/// or a watch-mode task) and detached. Overridable via `AGO_SHELL_IDLE`.
pub const SHELL_DEFAULT_IDLE: Duration = Duration::from_secs(10);

/// Extended idle window used while the recent output looks like an active build
/// (so a quiet-ish compile is not cut short). Overridable via
/// `AGO_SHELL_IDLE_BUILD`.
pub const SHELL_DEFAULT_IDLE_BUILD: Duration = Duration::from_secs(30);

/// Why a still-alive process was detached instead of blocked on. Drives the
/// reported `status` ("started" vs "running") and the advisory `detail`.
#[derive(Debug, Clone, Copy)]
enum DetachKind {
    /// Alive but produced no new output for the idle window — settled / waiting.
    Idle,
    /// Still producing output past the total ceiling — a long but live job.
    Ceiling,
}

/// Substrings that mark output as an in-progress build, so we grant a live
/// process a longer idle leash before treating silence as "settled".
const BUILD_OUTPUT_MARKERS: &[&str] = &[
    "compiling",
    "building",
    "bundling",
    "installing",
    "downloading",
    "fetching",
    "transforming",
];

/// Structured outcome of one tool call.
#[derive(Debug, Clone, PartialEq)]
pub struct ToolOutcome {
    pub success: bool,
    pub output: Value,
    pub error_code: Option<String>,
    pub metadata: HashMap<String, Value>,
}

impl ToolOutcome {
    pub fn ok(output: Value) -> Self {
        Self {
            success: true,
            output,
            error_code: None,
            metadata: HashMap::new(),
        }
    }

    pub fn err(code: &str) -> Self {
        Self {
            success: false,
            output: Value::Null,
            error_code: Some(code.into()),
            metadata: HashMap::new(),
        }
    }

    pub fn with_meta(mut self, key: &str, value: Value) -> Self {
        self.metadata.insert(key.into(), value);
        self
    }
}

#[derive(Debug, Error)]
pub enum ConfirmError {
    #[error("non-interactive client refused unknown binary: {0}")]
    NonInteractive(String),
    #[error("user declined the binary")]
    Declined,
    #[error("allowlist error: {0}")]
    Allowlist(#[from] AllowlistError),
}

/// Async confirmer: prompts the user the first time a binary appears.
///
/// Signature matches the Python `ConfirmCallback` so a future port that
/// drives multiple UIs (CLI, IDE plugin) only has to swap this trait
/// implementation.
pub trait ShellConfirmer: Send + Sync {
    fn confirm<'a>(
        &'a self,
        binary: &'a str,
        high_risk: bool,
    ) -> std::pin::Pin<Box<dyn std::future::Future<Output = bool> + Send + 'a>>;
}

/// Streaming emitter for shell stdout/stderr (4 KB chunks).
///
/// The receiver is responsible for assembling the chunks into the
/// `tool_result`'s aggregated output. The runner uses this only when
/// the caller wired a value — `None` falls back to buffered mode.
pub trait ChunkEmitter: Send {
    fn emit<'a>(
        &'a mut self,
        chunk: &'a str,
    ) -> std::pin::Pin<Box<dyn std::future::Future<Output = ()> + Send + 'a>>;
}

/// Cancellation signal raised by [`super::client`] when the server
/// sends a `cancel` frame for the current tool_call.
pub type CancelSignal = Arc<Notify>;

/// Runner state — workspace root + allowlist + shell timeout.
pub struct LocalToolRunner {
    workspace: PathBuf,
    allowlist: tokio::sync::Mutex<ShellAllowlist>,
    confirm: Option<Box<dyn ShellConfirmer>>,
    shell_timeout: Duration,
    /// Grace period before a long-running server command is reported as
    /// "started" and detached (instead of blocking to `shell_timeout`). Now used
    /// only as the (shorter) idle window for commands that match a known-server
    /// marker — a soft hint, no longer the gate.
    long_running_grace: Duration,
    /// Idle / no-output window for an UNKNOWN command before it is treated as a
    /// settled long-running process and detached. See [`SHELL_DEFAULT_IDLE`].
    shell_idle: Duration,
    /// Extended idle window used while recent output looks like an active build.
    /// See [`SHELL_DEFAULT_IDLE_BUILD`].
    shell_idle_build: Duration,
    /// Project-scoped shell policy (from `.ago.yaml`). `deny` hard-blocks,
    /// `project_allow` pre-approves without touching the global cache.
    /// Both are `argv[0]` basenames. `allow_all` flips the gate to
    /// default-allow (anything not in `deny` runs without a prompt).
    deny: HashSet<String>,
    project_allow: HashSet<String>,
    allow_all: bool,
    /// Persistent working directory across `shell_exec` calls (Phase 3 slice).
    /// A standalone `cd` updates it; every spawned command runs in it. Always
    /// kept inside `workspace`. Defaults to `workspace`.
    cwd: tokio::sync::Mutex<PathBuf>,
    /// Persistent extra environment across `shell_exec` calls. A standalone
    /// `export NAME=VALUE` adds to it; every spawned command inherits it.
    env: tokio::sync::Mutex<Vec<(String, String)>>,
    /// Per-session `file_read` dedup cache: resolved path → (mtime, size).
    /// On a repeat read of an unchanged file we return a compact "unchanged"
    /// marker instead of the full content, to save context tokens on the wire.
    /// Invalidated by `file_write`. Disable with `AGO_READ_CACHE=0`.
    read_cache: tokio::sync::Mutex<HashMap<String, (std::time::SystemTime, u64)>>,
}

/// A pure builtin that mutates persistent shell state instead of spawning a
/// process. Detected only when it is the WHOLE command (no chaining/operators).
#[derive(Debug, Clone, PartialEq)]
enum StateChange {
    /// `cd [dir]` — `None` means "home" (reset to workspace root).
    Cd(Option<String>),
    /// `export NAME=VALUE`.
    Export(String, String),
}

impl LocalToolRunner {
    pub fn new(workspace: PathBuf) -> Self {
        let cwd_init = workspace.clone();
        Self {
            workspace,
            allowlist: tokio::sync::Mutex::new(ShellAllowlist::at_default()),
            confirm: None,
            shell_timeout: shell_ceiling_default(),
            long_running_grace: LONG_RUNNING_GRACE,
            shell_idle: shell_idle_default(),
            shell_idle_build: shell_idle_build_default(),
            deny: HashSet::new(),
            project_allow: HashSet::new(),
            allow_all: false,
            cwd: tokio::sync::Mutex::new(cwd_init),
            env: tokio::sync::Mutex::new(Vec::new()),
            read_cache: tokio::sync::Mutex::new(HashMap::new()),
        }
    }

    pub fn with_allowlist(mut self, allowlist: ShellAllowlist) -> Self {
        self.allowlist = tokio::sync::Mutex::new(allowlist);
        self
    }

    /// Apply a project shell policy. Entries are normalised to basenames so
    /// they match the same way the allowlist cache does. `allow_all` flips
    /// the gate to default-allow (anything not in `deny` runs unprompted).
    pub fn with_shell_policy(mut self, allow: &[String], deny: &[String], allow_all: bool) -> Self {
        let base = |s: &String| {
            Path::new(s)
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or(s)
                .to_string()
        };
        self.project_allow = allow.iter().map(base).collect();
        self.deny = deny.iter().map(base).collect();
        self.allow_all = allow_all;
        self
    }

    pub fn with_confirmer(mut self, confirmer: Box<dyn ShellConfirmer>) -> Self {
        self.confirm = Some(confirmer);
        self
    }

    pub fn with_shell_timeout(mut self, timeout: Duration) -> Self {
        self.shell_timeout = timeout;
        self
    }

    /// Override the grace period for long-running server commands (default
    /// [`LONG_RUNNING_GRACE`]). Mainly for tests.
    pub fn with_long_running_grace(mut self, grace: Duration) -> Self {
        self.long_running_grace = grace;
        self
    }

    /// Override the idle / no-output window for unknown commands (default
    /// [`SHELL_DEFAULT_IDLE`]). Mainly for tests.
    pub fn with_shell_idle(mut self, idle: Duration) -> Self {
        self.shell_idle = idle;
        self
    }

    /// Override the extended (build) idle window (default
    /// [`SHELL_DEFAULT_IDLE_BUILD`]). Mainly for tests.
    pub fn with_shell_idle_build(mut self, idle: Duration) -> Self {
        self.shell_idle_build = idle;
        self
    }

    pub fn workspace(&self) -> &Path {
        &self.workspace
    }

    /// Tool names exposed in the HELLO manifest.
    pub fn manifest() -> Vec<String> {
        vec![
            "file_read".to_string(),
            "file_write".to_string(),
            "shell_exec".to_string(),
        ]
    }

    /// Execute one tool. `emit_chunk` and `cancel` are honoured by
    /// `shell_exec`; the file tools currently ignore them.
    pub async fn run(
        &self,
        name: &str,
        args: HashMap<String, Value>,
        emit_chunk: Option<Box<dyn ChunkEmitter>>,
        cancel: Option<CancelSignal>,
    ) -> ToolOutcome {
        match name {
            "file_read" => self.do_file_read(&args).await,
            "file_write" => self.do_file_write(&args).await,
            "shell_exec" => self.do_shell_exec(&args, emit_chunk, cancel).await,
            other => ToolOutcome::err("unknown_tool").with_meta("tool", json!(other)),
        }
    }

    // -----------------------------------------------------------------
    // file_read
    // -----------------------------------------------------------------

    async fn do_file_read(&self, args: &HashMap<String, Value>) -> ToolOutcome {
        let path = match first_present(args, &["file_path", "path", "filepath"]) {
            Some(p) => p,
            None => {
                return ToolOutcome::err("missing_file_path")
                    .with_meta("tool", json!("file_read"))
                    .with_meta("got_keys", json!(args.keys().collect::<Vec<_>>()));
            }
        };
        let resolved = match enforce_workspace(&self.workspace, &path, false) {
            Ok(p) => p,
            Err(SandboxError::PathEscape(_) | SandboxError::SymlinkOnPath(_)) => {
                return ToolOutcome::err("path_outside_workspace")
                    .with_meta("tool", json!("file_read"))
                    .with_meta("attempted", json!(path));
            }
            Err(e) => {
                return ToolOutcome::err("sandbox_error").with_meta("detail", json!(e.to_string()));
            }
        };
        // Pre-flight validation: a `file_read` on a directory otherwise fails
        // deep in `read_to_string` with a bare "Is a directory (os error 21)"
        // that the agent re-tried verbatim in real logs. Catch it here and hand
        // back an actionable suggestion instead of the raw errno.
        if let Ok(meta) = fs::metadata(&resolved).await {
            if meta.is_dir() {
                return ToolOutcome::err("is_a_directory")
                    .with_meta("path", json!(resolved.display().to_string()))
                    .with_meta(
                        "detail",
                        json!(format!(
                            "'{0}' is a directory, not a file. To see what is inside it, list \
                             it with shell_exec — e.g. argv = [\"ls\", \"-la\", \"{0}\"] — then \
                             file_read a specific file.",
                            resolved.display()
                        )),
                    );
            }
        }
        // Per-session read dedup: on a repeat read of an unchanged file, return
        // a compact "unchanged" marker instead of re-sending the whole content,
        // to save context tokens on the wire. Keyed by resolved path so `./x`
        // and `x` are the same entry; invalidated by file_write.
        let cache_key = resolved.display().to_string();
        let sig: Option<(std::time::SystemTime, u64)> = fs::metadata(&resolved)
            .await
            .ok()
            .and_then(|m| Some((m.modified().ok()?, m.len())));
        if read_cache_enabled() {
            if let Some(sig) = sig {
                if self.read_cache.lock().await.get(&cache_key) == Some(&sig) {
                    return ToolOutcome::ok(json!({
                        "status": "unchanged",
                        "path": cache_key.clone(),
                        "bytes": sig.1,
                    }))
                    .with_meta("cached", json!(true))
                    .with_meta(
                        "detail",
                        json!(
                            "You already read this file earlier this session and it \
                               has NOT changed since. Reuse your earlier read — the \
                               content was omitted to save context (set AGO_READ_CACHE=0 \
                               to disable)."
                        ),
                    );
                }
            }
        }
        match fs::read_to_string(&resolved).await {
            Ok(content) => {
                if let Some(sig) = sig {
                    self.read_cache.lock().await.insert(cache_key, sig);
                }
                ToolOutcome::ok(Value::String(content))
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                // "list before assume": guessed paths were the top source of
                // not_found errors in real logs (e.g. apps/frontend vs
                // apps/01/frontend). Point the agent at its parent directory so
                // it lists instead of guessing the next path.
                let parent_hint = resolved
                    .parent()
                    .map(|p| p.display().to_string())
                    .unwrap_or_else(|| ".".to_string());
                ToolOutcome::err("not_found")
                    .with_meta("path", json!(resolved.display().to_string()))
                    .with_meta(
                        "detail",
                        json!(format!(
                            "No such file: '{}'. Do NOT guess the next path — list the parent \
                             directory first with shell_exec, e.g. argv = [\"ls\", \"-la\", \"{}\"], \
                             then read an entry that actually exists.",
                            resolved.display(),
                            parent_hint
                        )),
                    )
            }
            Err(e) => ToolOutcome::err("io_error").with_meta("detail", json!(e.to_string())),
        }
    }

    // -----------------------------------------------------------------
    // file_write
    // -----------------------------------------------------------------

    async fn do_file_write(&self, args: &HashMap<String, Value>) -> ToolOutcome {
        let path = match first_present(args, &["file_path", "path", "filepath"]) {
            Some(p) => p,
            None => {
                return ToolOutcome::err("missing_file_path")
                    .with_meta("tool", json!("file_write"))
                    .with_meta("got_keys", json!(args.keys().collect::<Vec<_>>()));
            }
        };
        let content_value = first_present_value(args, &["content", "text", "body", "data"]);
        let content = match content_value {
            Some(Value::String(s)) => s,
            Some(Value::Null) => {
                return ToolOutcome::err("missing_content").with_meta("tool", json!("file_write"));
            }
            Some(other) => other.to_string(),
            None => {
                return ToolOutcome::err("missing_content")
                    .with_meta("tool", json!("file_write"))
                    .with_meta("got_keys", json!(args.keys().collect::<Vec<_>>()));
            }
        };
        let resolved = match enforce_workspace(&self.workspace, &path, false) {
            Ok(p) => p,
            Err(SandboxError::PathEscape(_) | SandboxError::SymlinkOnPath(_)) => {
                return ToolOutcome::err("path_outside_workspace")
                    .with_meta("tool", json!("file_write"))
                    .with_meta("attempted", json!(path));
            }
            Err(e) => {
                return ToolOutcome::err("sandbox_error").with_meta("detail", json!(e.to_string()));
            }
        };
        if let Some(parent) = resolved.parent() {
            if let Err(e) = fs::create_dir_all(parent).await {
                return ToolOutcome::err("io_error").with_meta("detail", json!(e.to_string()));
            }
        }
        match fs::write(&resolved, content.as_bytes()).await {
            Ok(()) => {
                // Invalidate the read cache for this path: the next file_read
                // must return the fresh content, not the "unchanged" marker.
                self.read_cache
                    .lock()
                    .await
                    .remove(&resolved.display().to_string());
                ToolOutcome::ok(json!({
                    "bytes_written": content.len(),
                    "path": resolved.display().to_string(),
                }))
            }
            Err(e) => ToolOutcome::err("io_error").with_meta("detail", json!(e.to_string())),
        }
    }

    // -----------------------------------------------------------------
    // shell_exec
    // -----------------------------------------------------------------

    /// Apply a pure `cd` / `export` to persistent session state without spawning
    /// a process. `cd` is resolved against the current persistent dir and must
    /// stay inside the workspace; `export` appends/overwrites a session var.
    async fn apply_state_change(&self, sc: StateChange) -> ToolOutcome {
        match sc {
            StateChange::Cd(target) => {
                let base = self.cwd.lock().await.clone();
                let candidate = match target.as_deref() {
                    // Bare `cd` / `cd ~` → workspace root (our notion of "home").
                    None | Some("~") | Some("") => self.workspace.clone(),
                    Some(t) if Path::new(t).is_absolute() => PathBuf::from(t),
                    Some(t) => base.join(t),
                };
                let resolved = match candidate.canonicalize() {
                    Ok(p) => p,
                    Err(_) => {
                        return ToolOutcome::err("shell_cd_failed").with_meta(
                            "detail",
                            json!(format!(
                                "cd: no such directory: {}",
                                target.as_deref().unwrap_or("~")
                            )),
                        );
                    }
                };
                // Containment: the resolved (symlink-followed) path must be the
                // workspace or live under it.
                let ws_canon = self
                    .workspace
                    .canonicalize()
                    .unwrap_or_else(|_| self.workspace.clone());
                if !resolved.starts_with(&ws_canon) {
                    return ToolOutcome::err("path_outside_workspace")
                        .with_meta("detail", json!("cd target resolves outside the workspace"));
                }
                if !resolved.is_dir() {
                    return ToolOutcome::err("shell_cd_failed")
                        .with_meta("detail", json!("cd: not a directory"));
                }
                *self.cwd.lock().await = resolved.clone();
                let rel = resolved
                    .strip_prefix(&ws_canon)
                    .unwrap_or(&resolved)
                    .to_string_lossy()
                    .to_string();
                let shown = if rel.is_empty() { ".".to_string() } else { rel };
                ToolOutcome::ok(json!({ "status": "ok", "cwd": shown.clone() }))
                    .with_meta("cwd", json!(shown))
                    .with_meta(
                        "detail",
                        json!(
                            "working directory updated; it persists across \
                               shell_exec calls, so you do NOT need to prefix `cd` \
                               on the next command"
                        ),
                    )
            }
            StateChange::Export(key, value) => {
                let mut env = self.env.lock().await;
                env.retain(|(k, _)| k != &key);
                env.push((key.clone(), value.clone()));
                ToolOutcome::ok(json!({ "status": "ok" }))
                    .with_meta("exported", json!(key))
                    .with_meta(
                        "detail",
                        json!(
                            "environment variable set; it persists across \
                               shell_exec calls"
                        ),
                    )
            }
        }
    }

    async fn do_shell_exec(
        &self,
        args: &HashMap<String, Value>,
        mut emit_chunk: Option<Box<dyn ChunkEmitter>>,
        cancel: Option<CancelSignal>,
    ) -> ToolOutcome {
        // The model very often sends a command STRING ("cd app && pytest")
        // instead of an argv list. How we handle it depends on the policy:
        //
        // * allow_all → the sandbox/container is the security boundary, NOT a
        //   per-binary allowlist. So run the string through a shell
        //   (`bash -lc`): the model's natural `cd x && cmd`, pipes, redirects,
        //   globs and builtins work, killing the cascading shell_nonzero_exit
        //   that a single stateless process produced. `bash` itself still
        //   passes the deny gate below, so a `deny: [bash]` is honored.
        // * otherwise → tokenize with shlex (shell-FREE, no metacharacters) so
        //   argv[0] still passes the per-binary deny/allow policy. Never bypass
        //   a real allowlist by silently invoking a shell.
        let argv_value = first_present_value(args, &["argv", "command", "cmd", "args"]);

        // Persistent-state slice (Phase 3): a command that is PURELY `cd <dir>`
        // or `export NAME=VALUE` — no chaining, pipes or redirection — mutates
        // session state instead of spawning. This makes the working directory
        // and exported vars persist across calls, so the agent no longer has to
        // prefix `cd apps/01/frontend &&` on every command. Chained forms
        // (`cd x && cmd`) are NOT intercepted and keep their existing one-shot
        // bash behaviour.
        if let Some(tokens) = state_change_tokens(&argv_value) {
            if let Some(sc) = detect_state_change(&tokens) {
                return self.apply_state_change(sc).await;
            }
        }

        let argv: Vec<String> = match argv_value {
            Some(Value::Array(arr)) => arr
                .iter()
                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                .collect(),
            Some(Value::String(s)) if self.allow_all => {
                vec!["bash".to_string(), "-lc".to_string(), s.clone()]
            }
            Some(Value::String(s)) => match shlex::split(s.as_str()) {
                Some(parts) if !parts.is_empty() => parts,
                // Unbalanced quotes / empty → can't tokenize safely.
                _ => {
                    return ToolOutcome::err("shell_unparseable_command").with_meta(
                        "detail",
                        json!(
                            "could not parse the command string into arguments; \
                             pass argv as a list, e.g. [\"npm\", \"install\"]. For a \
                             pipeline/redirection use [\"bash\", \"-lc\", \"<cmd>\"]"
                        ),
                    );
                }
            },
            _ => {
                return empty_argv_error();
            }
        };
        if argv.is_empty() {
            return empty_argv_error();
        }

        // Under allow_all the model also sends builtins / shell operators as an
        // argv LIST (e.g. ["cd","app"] or ["cd","app","&&","pytest"]) — which a
        // direct spawn rejects (`cd` is not a program). Reconstruct a shell line
        // and run it via `bash -lc`, same as the string path. Operators are kept
        // raw; every other token is shell-quoted so args with spaces survive.
        // Normal commands (no builtin, no operator) stay a direct spawn.
        let argv: Vec<String> = if self.allow_all && argv[0] != "bash" && list_needs_shell(&argv) {
            vec!["bash".to_string(), "-lc".to_string(), join_for_shell(&argv)]
        } else {
            argv
        };

        // Basename of argv[0] for policy checks — same normalisation as the
        // allowlist cache, so a path alias can't slip past deny/allow.
        let bin_base = Path::new(&argv[0])
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or(&argv[0])
            .to_string();

        // Project policy: a `deny` entry is a HARD block — it wins over the
        // global cache and over any confirm. Checked before everything else.
        if self.deny.contains(&bin_base) {
            return ToolOutcome::err("shell_denied_by_policy").with_meta(
                "detail",
                json!(format!("`{bin_base}` is denied by .ago.yaml shell.deny")),
            );
        }

        // Allowlist gate. First use of a new binary asks the confirmer;
        // without one, refuse (fail-closed). Two project-policy
        // short-circuits run without prompting and WITHOUT persisting to the
        // global cache: `allow_all` (anything not denied) and an explicit
        // project `allow` entry. `deny` was already enforced above and wins
        // over both.
        if !self.allow_all && !self.project_allow.contains(&bin_base) {
            let mut allow = self.allowlist.lock().await;
            match allow.contains(&argv) {
                Ok(true) => {}
                Ok(false) => match &self.confirm {
                    None => {
                        return ToolOutcome::err("shell_denied").with_meta(
                            "detail",
                            json!("non-interactive client; binary not pre-allowed"),
                        );
                    }
                    Some(c) => {
                        let bin = argv[0].clone();
                        let high_risk = is_high_risk(&bin);
                        let approved = c.confirm(&bin, high_risk).await;
                        if !approved {
                            return ToolOutcome::err("shell_denied");
                        }
                        if let Err(e) = allow.allow(&argv) {
                            return ToolOutcome::err("allowlist_save_failed")
                                .with_meta("detail", json!(e.to_string()));
                        }
                    }
                },
                Err(e) => {
                    return ToolOutcome::err("allowlist_check_failed")
                        .with_meta("detail", json!(e.to_string()));
                }
            }
        }

        // Spawn the process — argv list, never shell=True. Runs in the
        // persistent working directory (defaults to the workspace root) and
        // inherits any persisted `export`s. AGENT_HOST=1 lets called processes
        // detect they're under agent-host if they care.
        let run_dir = self.cwd.lock().await.clone();
        let extra_env = self.env.lock().await.clone();
        let mut cmd = Command::new(&argv[0]);
        cmd.args(&argv[1..])
            .current_dir(&run_dir)
            .env("AGENT_HOST", "1")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        for (k, v) in &extra_env {
            cmd.env(k, v);
        }

        let mut child = match cmd.spawn() {
            Ok(c) => c,
            Err(e) => {
                // Inside the jail the container ships a bare base image, so a
                // missing binary is most often "not installed in the image"
                // rather than a typo. Surface that cause so the agent adapts
                // and the operator knows the fix (see
                // docs/managing-local-projects.md § Jail-by-default).
                let in_jail = std::env::var("AGO_IN_JAIL").as_deref() == Ok("1");
                let not_found = e.kind() == std::io::ErrorKind::NotFound;
                let detail = spawn_failure_detail(&e.to_string(), &argv[0], not_found, in_jail);
                return ToolOutcome::err("shell_spawn_failed")
                    .with_meta("argv0", json!(argv[0]))
                    .with_meta("detail", json!(detail));
            }
        };

        let mut stdout = child.stdout.take().expect("stdout piped");
        let mut stderr = child.stderr.take().expect("stderr piped");

        let started = Instant::now();
        let mut out_buf = Vec::<u8>::new();
        let mut err_buf = Vec::<u8>::new();
        let mut cancelled = false;
        // `timed_out` is retained for `finalise_shell`'s signature but is never
        // set anymore: a still-alive process is detached (see `DetachKind`), not
        // killed-and-errored. Only a real non-zero EXIT is an error.
        let timed_out = false;
        // Behavior-based long-running handling (see
        // docs/shell-execution-redesign.md). We do NOT predict from the command
        // name. Two clocks run instead:
        //   * idle  — alive but no new output for `idle_window` ⇒ settled
        //             (server / watch) ⇒ detach, report "started".
        //   * total — still producing output past `shell_timeout` ⇒ a long but
        //             live job ⇒ detach, report "running" (never killed).
        // The marker list survives only as a soft HINT: a known server uses the
        // shorter `long_running_grace` idle window instead of `shell_idle`.
        let server_hint = is_long_running_command(&argv);
        let mut detach: Option<DetachKind> = None;

        // Co-operative reader loop: select between stdout / stderr / process exit
        // / cancel signal / idle clock / total ceiling. Cap output at
        // SHELL_OUTPUT_CAP combined; once hit, stop appending but keep reading so
        // the pipe never stalls.
        let cancel_fut = async {
            match cancel {
                Some(notify) => notify.notified().await,
                None => std::future::pending::<()>().await,
            }
        };
        tokio::pin!(cancel_fut);
        // Absolute ceiling on how long we are willing to BLOCK the turn.
        let total_fut = tokio::time::sleep(self.shell_timeout);
        tokio::pin!(total_fut);
        // Idle clock: reset to "now" on every output chunk.
        let mut last_output_at = tokio::time::Instant::now();

        let mut stdout_done = false;
        let mut stderr_done = false;

        loop {
            // Recompute the idle window each iteration: a known server detaches
            // fast; an unknown command that is actively building gets a longer
            // leash so a quiet-ish compile is not cut short.
            let idle_window = if server_hint {
                self.long_running_grace
            } else if output_looks_like_build(&out_buf, &err_buf) {
                self.shell_idle_build
            } else {
                self.shell_idle
            };
            let idle_fut = tokio::time::sleep_until(last_output_at + idle_window);
            tokio::pin!(idle_fut);

            tokio::select! {
                biased;
                _ = &mut cancel_fut => {
                    cancelled = true;
                    let _ = child.kill().await;
                    break;
                }
                // Total ceiling first so a firehose of output cannot starve it.
                _ = &mut total_fut => {
                    detach = Some(DetachKind::Ceiling);
                    break;
                }
                res = read_chunk(&mut stdout, SHELL_CHUNK_BYTES), if !stdout_done => {
                    match res {
                        Ok(Some(buf)) => {
                            last_output_at = tokio::time::Instant::now();
                            if out_buf.len() + err_buf.len() + buf.len() <= SHELL_OUTPUT_CAP {
                                out_buf.extend_from_slice(&buf);
                                if let Some(emit) = emit_chunk.as_deref_mut() {
                                    let s = String::from_utf8_lossy(&buf).to_string();
                                    emit.emit(&s).await;
                                }
                            }
                        }
                        Ok(None) => stdout_done = true,
                        Err(_) => stdout_done = true,
                    }
                }
                res = read_chunk(&mut stderr, SHELL_CHUNK_BYTES), if !stderr_done => {
                    match res {
                        Ok(Some(buf)) => {
                            last_output_at = tokio::time::Instant::now();
                            if out_buf.len() + err_buf.len() + buf.len() <= SHELL_OUTPUT_CAP {
                                err_buf.extend_from_slice(&buf);
                            }
                        }
                        Ok(None) => stderr_done = true,
                        Err(_) => stderr_done = true,
                    }
                }
                status = child.wait(), if stdout_done && stderr_done => {
                    return finalise_shell(argv, status.ok(), started.elapsed(), out_buf, err_buf, cancelled, timed_out);
                }
                // Idle last: only fires when nothing else is ready (no pending
                // output) and the window has elapsed since the last byte.
                _ = &mut idle_fut => {
                    detach = Some(DetachKind::Idle);
                    break;
                }
            }
        }

        // Still-alive process: report it (started / running) without killing,
        // then move the child + its pipes into a detached task that drains
        // stdout/stderr until the process exits. Draining matters — an unread
        // pipe buffer would eventually stall the process — and we never kill it,
        // so a server stays up for the agent's follow-up health checks.
        if let Some(kind) = detach {
            let argv0 = argv[0].clone();
            let snapshot = String::from_utf8_lossy(&out_buf).to_string();
            let elapsed_s = started.elapsed().as_secs();
            let (status_str, mut detail) = match kind {
                DetachKind::Idle => (
                    "started",
                    format!(
                        "'{argv0}' produced no new output for a while and is still \
                         running, so it was reported as started and left running in \
                         the background (typically a dev server or a watch-mode \
                         task). Its output so far is included above. Do NOT re-run \
                         it; verify it with a health check (e.g. \
                         `curl localhost:<port>` or `docker compose ps`)."
                    ),
                ),
                DetachKind::Ceiling => (
                    "running",
                    format!(
                        "'{argv0}' was still producing output after {elapsed_s}s and \
                         had not exited, so it was left running in the background \
                         rather than killed. Do NOT re-run it; if you need its final \
                         result, wait and poll its output, otherwise continue."
                    ),
                ),
            };
            // Phase 2 readiness probe: if the output announced a port that is now
            // accepting TCP connections, turn the generic "started" into a
            // confident "ready on :PORT" so the agent skips its own health check.
            let ready_port = first_listening_port(&out_buf, &err_buf).await;
            if let Some(p) = ready_port {
                detail.push_str(&format!(
                    " Verified ready: a process is accepting TCP connections on \
                     127.0.0.1:{p}."
                ));
            }
            debug!(
                "shell_exec: '{}' detached as '{}' after {}s (reason={:?}), left running",
                argv.join(" "),
                status_str,
                elapsed_s,
                kind
            );
            tokio::spawn(async move {
                let mut child = child;
                let mut stdout = stdout;
                let mut stderr = stderr;
                let mut sink_done = (false, false);
                loop {
                    tokio::select! {
                        r = read_chunk(&mut stdout, SHELL_CHUNK_BYTES), if !sink_done.0 => {
                            if matches!(r, Ok(None) | Err(_)) { sink_done.0 = true; }
                        }
                        r = read_chunk(&mut stderr, SHELL_CHUNK_BYTES), if !sink_done.1 => {
                            if matches!(r, Ok(None) | Err(_)) { sink_done.1 = true; }
                        }
                        _ = child.wait() => { break; }
                    }
                }
            });
            let mut outcome = ToolOutcome::ok(json!({
                "stdout": snapshot,
                "stderr": "",
                "returncode": Value::Null,
                "status": status_str,
            }))
            .with_meta("argv0", json!(argv0))
            .with_meta("long_running", json!(true))
            .with_meta("detail", json!(detail));
            if let Some(p) = ready_port {
                outcome = outcome.with_meta("ready_port", json!(p));
            }
            return outcome;
        }

        // Cancelled / timed out: reap the dead process and drain
        // anything queued (best-effort within 2s).
        let _ = tokio::time::timeout(Duration::from_secs(2), child.wait()).await;
        let _ = drain_remaining(&mut stdout, &mut out_buf, SHELL_OUTPUT_CAP).await;
        let _ = drain_remaining(&mut stderr, &mut err_buf, SHELL_OUTPUT_CAP).await;
        finalise_shell(
            argv,
            None,
            started.elapsed(),
            out_buf,
            err_buf,
            cancelled,
            timed_out,
        )
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// A `shell_exec` with no usable command (missing/empty/non-string argv). Real
/// logs showed the agent re-issuing these blind; the detail tells it exactly
/// what shape to send so it stops guessing.
fn empty_argv_error() -> ToolOutcome {
    ToolOutcome::err("shell_empty_argv").with_meta(
        "detail",
        json!(
            "no command to run — `argv` was empty or missing. Pass a non-empty \
             argv list, e.g. [\"ls\", \"-la\"]; for a pipeline/redirection use \
             [\"bash\", \"-lc\", \"<cmd>\"]"
        ),
    )
}

fn first_present(args: &HashMap<String, Value>, keys: &[&str]) -> Option<String> {
    for k in keys {
        if let Some(v) = args.get(*k) {
            if let Some(s) = v.as_str() {
                return Some(s.to_string());
            }
        }
    }
    None
}

fn first_present_value(args: &HashMap<String, Value>, keys: &[&str]) -> Option<Value> {
    for k in keys {
        if let Some(v) = args.get(*k) {
            return Some(v.clone());
        }
    }
    None
}

/// Tokenize the raw command (list or string) for state-change detection. A
/// string is split shell-style (`shlex`); an unparseable string yields `None`.
fn state_change_tokens(argv_value: &Option<Value>) -> Option<Vec<String>> {
    match argv_value {
        Some(Value::Array(arr)) => Some(
            arr.iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect(),
        ),
        Some(Value::String(s)) => shlex::split(s),
        _ => None,
    }
}

/// Recognise a command that is PURELY `cd [dir]` or `export NAME=VALUE` — and
/// nothing else. Any shell operator (`&&`, `|`, `;`, redirection, …)
/// disqualifies it, so chained forms keep their normal one-shot bash behaviour.
fn detect_state_change(tokens: &[String]) -> Option<StateChange> {
    let first = tokens.first()?;
    if tokens.iter().any(|t| SHELL_OPERATORS.contains(&t.as_str())) {
        return None;
    }
    match first.as_str() {
        "cd" => match tokens.len() {
            1 => Some(StateChange::Cd(None)),
            2 => Some(StateChange::Cd(Some(tokens[1].clone()))),
            _ => None, // `cd a b` is not a plain directory change
        },
        "export" if tokens.len() == 2 => {
            let (k, v) = tokens[1].split_once('=')?;
            if k.is_empty() {
                None
            } else {
                Some(StateChange::Export(k.to_string(), v.to_string()))
            }
        }
        _ => None,
    }
}

async fn read_chunk<R>(reader: &mut R, cap: usize) -> std::io::Result<Option<Vec<u8>>>
where
    R: AsyncReadExt + Unpin,
{
    let mut buf = vec![0u8; cap];
    let n = reader.read(&mut buf).await?;
    if n == 0 {
        Ok(None)
    } else {
        buf.truncate(n);
        Ok(Some(buf))
    }
}

async fn drain_remaining<R>(reader: &mut R, sink: &mut Vec<u8>, cap: usize) -> std::io::Result<()>
where
    R: AsyncReadExt + Unpin,
{
    let mut tmp = [0u8; 4096];
    loop {
        if sink.len() >= cap {
            break;
        }
        let n = match tokio::time::timeout(Duration::from_millis(200), reader.read(&mut tmp)).await
        {
            Ok(Ok(n)) => n,
            _ => break,
        };
        if n == 0 {
            break;
        }
        sink.extend_from_slice(&tmp[..n]);
    }
    Ok(())
}

/// Shell builtins that are NOT real executables, so spawning them always fails
/// with "not found". `shell_exec` runs one process with no shell and no
/// persistent cwd, so the agent must fold these into a single command instead.
const SHELL_BUILTINS: &[&str] = &["cd", "export", "source", ".", "alias", "pushd", "popd"];

/// Shell control operators that, if present as their own argv token, mean the
/// model intended a real shell line (pipeline / conjunction / redirection).
const SHELL_OPERATORS: &[&str] = &[
    "&&", "||", "|", ";", ">", ">>", "<", "<<", "&", "2>", "2>&1", "|&",
];

/// Whether an argv LIST should be handed to a shell rather than spawned
/// directly: it leads with a builtin (`cd`…) or contains a control operator.
fn list_needs_shell(argv: &[String]) -> bool {
    SHELL_BUILTINS.contains(&argv[0].as_str())
        || argv.iter().any(|a| SHELL_OPERATORS.contains(&a.as_str()))
}

/// Re-join an argv list into a `bash -lc` command line: control operators stay
/// raw so they keep their shell meaning, every other token is shell-quoted so
/// arguments containing spaces / metacharacters survive intact.
fn join_for_shell(argv: &[String]) -> String {
    argv.iter()
        .map(|a| {
            if SHELL_OPERATORS.contains(&a.as_str()) {
                a.clone()
            } else {
                shlex::try_quote(a)
                    .map(|c| c.into_owned())
                    .unwrap_or_else(|_| a.clone())
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

/// Substrings that mark a long-running server command — one that stays up
/// instead of exiting, so blocking on it to `shell_timeout` only ever yields a
/// misleading `shell_timeout`. Matched against the lowercased, space-joined argv.
const LONG_RUNNING_MARKERS: &[&str] = &[
    "npm run dev",
    "npm start",
    "npm run start",
    "pnpm dev",
    "pnpm start",
    "pnpm run dev",
    "yarn dev",
    "yarn start",
    "vite",
    "next dev",
    "nuxt dev",
    "react-scripts start",
    "ng serve",
    "webpack serve",
    "webpack-dev-server",
    "nodemon",
    "uvicorn",
    "gunicorn",
    "flask run",
    "rails server",
    "rails s ",
    "artisan serve",
    "http.server",
    "http-server",
    ":dev",
    ":serve",
    "docker compose up",
    "docker-compose up",
];

/// Markers that mean the command was ALREADY started in a non-blocking way
/// (detached / backgrounded / time-boxed), so it must NOT be treated as a
/// long-running server to detach again.
const ALREADY_DETACHED_MARKERS: &[&str] = &[
    " -d",
    " --detach",
    "--abort-on-container-exit",
    "nohup ",
    "timeout ",
];

/// Whether `argv` launches a long-running server that will not exit on its own.
///
/// Pure (no spawn) so it is unit-testable. Returns false when the command is
/// already detached/backgrounded/time-boxed, true when it matches a known
/// server marker.
fn is_long_running_command(argv: &[String]) -> bool {
    let joined = argv.join(" ").to_lowercase();
    // An explicit background `&` (but not the `&&` AND operator) means the model
    // already detached it. Checked on the joined line so it catches `&` whether
    // it is its own argv token or embedded in a `bash -lc "… &"` string.
    if joined.split_whitespace().any(|t| t == "&") {
        return false;
    }
    if ALREADY_DETACHED_MARKERS.iter().any(|m| joined.contains(m)) {
        return false;
    }
    LONG_RUNNING_MARKERS.iter().any(|m| joined.contains(m))
}

/// Whether the tail of the combined output looks like an in-progress build, so a
/// live process gets the longer `shell_idle_build` window before silence is
/// treated as "settled". Only the last ~2 KB of each stream is scanned.
fn output_looks_like_build(out: &[u8], err: &[u8]) -> bool {
    fn tail_lower(b: &[u8]) -> String {
        let start = b.len().saturating_sub(2048);
        String::from_utf8_lossy(&b[start..]).to_lowercase()
    }
    let hay = format!("{}{}", tail_lower(out), tail_lower(err));
    BUILD_OUTPUT_MARKERS.iter().any(|m| hay.contains(m))
}

/// Parse a positive-seconds env var into a [`Duration`], falling back to
/// `default_secs` when unset, empty, non-numeric, or zero.
fn env_duration_secs(name: &str, default_secs: u64) -> Duration {
    let secs = std::env::var(name)
        .ok()
        .and_then(|v| v.trim().parse::<u64>().ok())
        .filter(|n| *n > 0)
        .unwrap_or(default_secs);
    Duration::from_secs(secs)
}

/// Idle window for unknown commands (`AGO_SHELL_IDLE`, default
/// [`SHELL_DEFAULT_IDLE`]).
fn shell_idle_default() -> Duration {
    env_duration_secs("AGO_SHELL_IDLE", SHELL_DEFAULT_IDLE.as_secs())
}

/// Extended build idle window (`AGO_SHELL_IDLE_BUILD`, default
/// [`SHELL_DEFAULT_IDLE_BUILD`]).
fn shell_idle_build_default() -> Duration {
    env_duration_secs("AGO_SHELL_IDLE_BUILD", SHELL_DEFAULT_IDLE_BUILD.as_secs())
}

/// Total blocking ceiling (`AGO_SHELL_CEILING`, default
/// [`SHELL_DEFAULT_TIMEOUT`]).
fn shell_ceiling_default() -> Duration {
    env_duration_secs("AGO_SHELL_CEILING", SHELL_DEFAULT_TIMEOUT.as_secs())
}

/// Whether the per-session `file_read` dedup cache is on (default yes).
/// Disable with `AGO_READ_CACHE` in {0,false,no,off}.
fn read_cache_enabled() -> bool {
    !matches!(
        std::env::var("AGO_READ_CACHE")
            .unwrap_or_default()
            .trim()
            .to_ascii_lowercase()
            .as_str(),
        "0" | "false" | "no" | "off"
    )
}

/// Strip ANSI/CSI escape sequences so port parsing survives coloured server
/// banners (CRA/Vite print e.g. `localhost:\x1b[1m3000\x1b[22m`).
fn strip_ansi(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut chars = s.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\u{1b}' {
            // Skip a CSI sequence up to and including its final letter.
            for n in chars.by_ref() {
                if n.is_ascii_alphabetic() {
                    break;
                }
            }
        } else {
            out.push(c);
        }
    }
    out
}

/// Candidate TCP ports a server announced in its output. Scans (ANSI-stripped)
/// for digit runs that follow `:` (e.g. `localhost:3000`) or the word `port`
/// (e.g. `port 8000`). Loose by design — the TCP probe is the real gate, so a
/// false candidate (a timestamp) just fails to connect. Host-prefixed and
/// unprivileged (>=1024) ports are ordered first; the list is capped.
fn extract_listen_ports(out: &[u8], err: &[u8]) -> Vec<u16> {
    let raw = format!(
        "{}\n{}",
        String::from_utf8_lossy(out),
        String::from_utf8_lossy(err)
    );
    let text = strip_ansi(&raw).to_lowercase();
    let b = text.as_bytes();
    let n = b.len();
    let mut high: Vec<u16> = Vec::new();
    let mut low: Vec<u16> = Vec::new();
    let mut i = 0;
    while i < n {
        if b[i].is_ascii_digit() {
            let start = i;
            while i < n && b[i].is_ascii_digit() {
                i += 1;
            }
            let prefixed_by_colon = start > 0 && b[start - 1] == b':';
            let prefixed_by_port = text[..start].trim_end().ends_with("port");
            if (prefixed_by_colon || prefixed_by_port) && i - start <= 5 {
                if let Ok(p) = text[start..i].parse::<u32>() {
                    if (1..=65535).contains(&p) {
                        let p = p as u16;
                        let bucket = if p >= 1024 { &mut high } else { &mut low };
                        if !bucket.contains(&p) {
                            bucket.push(p);
                        }
                    }
                }
            }
        } else {
            i += 1;
        }
    }
    high.extend(low);
    high.truncate(6);
    high
}

/// Whether something is accepting TCP connections on `127.0.0.1:port` right now.
async fn probe_port(port: u16) -> bool {
    matches!(
        tokio::time::timeout(
            Duration::from_millis(250),
            tokio::net::TcpStream::connect(("127.0.0.1", port)),
        )
        .await,
        Ok(Ok(_))
    )
}

/// First announced port that is actually listening, if any.
async fn first_listening_port(out: &[u8], err: &[u8]) -> Option<u16> {
    for p in extract_listen_ports(out, err) {
        if probe_port(p).await {
            return Some(p);
        }
    }
    None
}

/// Build the `detail` for a `shell_spawn_failed` outcome.
///
/// Priority of hints (most specific first):
/// 1. `argv0` is a shell builtin (e.g. `cd`) — it is not an executable and
///    `shell_exec` keeps no cwd between calls, so explain how to chain it.
/// 2. not found *inside the jail* — the bare image likely lacks the binary.
/// 3. otherwise the raw OS error, unchanged.
///
/// Pure so it is unit-testable without spawning.
fn spawn_failure_detail(base: &str, argv0: &str, not_found: bool, in_jail: bool) -> String {
    if not_found && SHELL_BUILTINS.contains(&argv0) {
        format!(
            "{base} — '{argv0}' is a shell builtin, not a program, and shell_exec \
             runs a single process with no persistent working directory. Chain it \
             into one command instead, e.g. argv = [\"bash\", \"-lc\", \"cd sub && <cmd>\"]"
        )
    } else if not_found && in_jail {
        format!(
            "{base} — '{argv0}' is not installed in the jail image; \
             use a richer image via `jail_image:` in .ago.yaml or the \
             AGO_JAIL_IMAGE env var"
        )
    } else {
        base.to_string()
    }
}

/// Build the `output` Value for a shell result and, for failures, an error
/// signature for no-progress detection. Large *failing* output is replaced by
/// an error-salient digest (feature "A") so the diagnosis survives the
/// downstream context cap instead of being elided from the middle.
fn shell_output_value(
    stdout: &str,
    stderr: &str,
    returncode: Option<i32>,
    is_error: bool,
) -> (Value, Option<String>) {
    let signature = if is_error {
        super::error_digest::error_signature(stdout, stderr)
    } else {
        None
    };
    let combined_len = stdout.len() + stderr.len();
    let budget = digest_budget();
    if is_error && digest_enabled() && combined_len > budget {
        let excerpt = super::error_digest::salient_excerpt(stdout, stderr, budget);
        let value = json!({
            "returncode": returncode,
            "error_digest": excerpt,
            "note": format!(
                "output trimmed to salient error lines ({} of {} bytes); \
                 set AGO_ERROR_DIGEST=false for the raw output",
                excerpt.len(),
                combined_len
            ),
        });
        (value, signature)
    } else {
        (
            json!({"stdout": stdout, "stderr": stderr, "returncode": returncode}),
            signature,
        )
    }
}

fn digest_enabled() -> bool {
    !matches!(
        std::env::var("AGO_ERROR_DIGEST")
            .unwrap_or_default()
            .trim()
            .to_ascii_lowercase()
            .as_str(),
        "0" | "false" | "no" | "off"
    )
}

fn digest_budget() -> usize {
    std::env::var("AGO_ERROR_DIGEST_BYTES")
        .ok()
        .and_then(|v| v.trim().parse::<usize>().ok())
        .filter(|n| *n > 0)
        .unwrap_or(4000)
}

fn finalise_shell(
    argv: Vec<String>,
    status: Option<std::process::ExitStatus>,
    elapsed: Duration,
    out_buf: Vec<u8>,
    err_buf: Vec<u8>,
    cancelled: bool,
    timed_out: bool,
) -> ToolOutcome {
    let stdout = String::from_utf8_lossy(&out_buf).to_string();
    let stderr = String::from_utf8_lossy(&err_buf).to_string();
    let returncode = status.and_then(|s| s.code());

    if cancelled {
        return ToolOutcome::err("shell_cancelled")
            .with_meta("argv0", json!(argv[0]))
            .with_meta(
                "output",
                json!({"stdout": stdout, "stderr": stderr, "returncode": returncode}),
            );
    }
    if timed_out {
        let (output, signature) = shell_output_value(&stdout, &stderr, returncode, true);
        let mut out = ToolOutcome::err("shell_timeout")
            .with_meta("argv0", json!(argv[0]))
            .with_meta("output", output);
        if let Some(sig) = signature {
            out.metadata.insert("error_signature".into(), json!(sig));
        }
        return out;
    }
    let success = returncode == Some(0);
    let (output, signature) = shell_output_value(&stdout, &stderr, returncode, !success);
    let mut out = ToolOutcome {
        success,
        output,
        error_code: if success {
            None
        } else {
            Some("shell_nonzero_exit".into())
        },
        metadata: HashMap::new(),
    };
    out.metadata.insert("argv0".into(), json!(argv[0]));
    out.metadata
        .insert("elapsed_ms".into(), json!(elapsed.as_millis() as u64));
    if let Some(sig) = signature {
        out.metadata.insert("error_signature".into(), json!(sig));
    }
    out
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs as stdfs;

    fn tmp_workspace() -> (tempfile::TempDir, PathBuf) {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().to_path_buf();
        (dir, path)
    }

    #[tokio::test]
    async fn file_write_and_read() {
        let (_d, ws) = tmp_workspace();
        let runner = LocalToolRunner::new(ws.clone());
        let mut args = HashMap::new();
        args.insert("file_path".into(), json!("a.txt"));
        args.insert("content".into(), json!("hello"));
        let w = runner.run("file_write", args, None, None).await;
        assert!(w.success, "file_write failed: {w:?}");

        let mut args = HashMap::new();
        args.insert("file_path".into(), json!("a.txt"));
        let r = runner.run("file_read", args, None, None).await;
        assert!(r.success);
        assert_eq!(r.output.as_str().unwrap(), "hello");
    }

    #[tokio::test]
    async fn read_cache_skips_unchanged_repeat() {
        let (_d, ws) = tmp_workspace();
        let runner = LocalToolRunner::new(ws.clone());
        let mut w = HashMap::new();
        w.insert("file_path".into(), json!("a.txt"));
        w.insert("content".into(), json!("hello world"));
        assert!(runner.run("file_write", w, None, None).await.success);

        // First read returns the full content.
        let mut r1 = HashMap::new();
        r1.insert("file_path".into(), json!("a.txt"));
        let o1 = runner.run("file_read", r1, None, None).await;
        assert_eq!(o1.output.as_str(), Some("hello world"));

        // Second read of the unchanged file returns a compact marker, no content.
        let mut r2 = HashMap::new();
        r2.insert("file_path".into(), json!("a.txt"));
        let o2 = runner.run("file_read", r2, None, None).await;
        assert!(o2.success);
        assert_eq!(o2.metadata.get("cached"), Some(&json!(true)));
        assert_eq!(o2.output.get("status"), Some(&json!("unchanged")));
        assert!(
            o2.output.as_str().is_none(),
            "content must be omitted on a cache hit: {o2:?}"
        );
    }

    #[tokio::test]
    async fn read_cache_invalidated_by_write() {
        let (_d, ws) = tmp_workspace();
        let runner = LocalToolRunner::new(ws.clone());
        let write = |c: &str| {
            let mut m = HashMap::new();
            m.insert("file_path".into(), json!("b.txt"));
            m.insert("content".into(), json!(c));
            m
        };
        let read = || {
            let mut m = HashMap::new();
            m.insert("file_path".into(), json!("b.txt"));
            m
        };
        assert!(
            runner
                .run("file_write", write("one!"), None, None)
                .await
                .success
        );
        assert_eq!(
            runner
                .run("file_read", read(), None, None)
                .await
                .output
                .as_str(),
            Some("one!")
        );
        // Overwrite (same length) — the write must invalidate the cache so the
        // next read returns the NEW content, not the "unchanged" marker.
        assert!(
            runner
                .run("file_write", write("two!"), None, None)
                .await
                .success
        );
        let o = runner.run("file_read", read(), None, None).await;
        assert_eq!(
            o.output.as_str(),
            Some("two!"),
            "file_write must invalidate the read cache: {o:?}"
        );
    }

    #[tokio::test]
    async fn alias_path_accepted_for_file_read() {
        let (_d, ws) = tmp_workspace();
        let runner = LocalToolRunner::new(ws.clone());
        stdfs::write(ws.join("x.md"), "hi").unwrap();
        // LLM emits `path` instead of canonical `file_path`.
        let mut args = HashMap::new();
        args.insert("path".into(), json!("x.md"));
        let r = runner.run("file_read", args, None, None).await;
        assert!(r.success);
        assert_eq!(r.output.as_str().unwrap(), "hi");
    }

    #[tokio::test]
    async fn file_read_on_directory_suggests_listing() {
        let (_d, ws) = tmp_workspace();
        let runner = LocalToolRunner::new(ws.clone());
        stdfs::create_dir(ws.join("subdir")).unwrap();
        let mut args = HashMap::new();
        args.insert("file_path".into(), json!("subdir"));
        let r = runner.run("file_read", args, None, None).await;
        assert!(!r.success);
        assert_eq!(r.error_code.as_deref(), Some("is_a_directory"));
        let detail = r.metadata.get("detail").and_then(|v| v.as_str()).unwrap();
        assert!(detail.contains("is a directory"), "got: {detail}");
        assert!(detail.contains("ls"), "should suggest listing: {detail}");
    }

    #[tokio::test]
    async fn file_read_not_found_nudges_to_list_parent() {
        let (_d, ws) = tmp_workspace();
        let runner = LocalToolRunner::new(ws.clone());
        let mut args = HashMap::new();
        args.insert("file_path".into(), json!("apps/frontend/package.json"));
        let r = runner.run("file_read", args, None, None).await;
        assert!(!r.success);
        assert_eq!(r.error_code.as_deref(), Some("not_found"));
        let detail = r.metadata.get("detail").and_then(|v| v.as_str()).unwrap();
        assert!(detail.contains("list the parent"), "got: {detail}");
        assert!(detail.contains("ls"), "should suggest ls: {detail}");
    }

    #[tokio::test]
    async fn empty_argv_returns_actionable_detail() {
        let (_d, ws) = tmp_workspace();
        let runner = LocalToolRunner::new(ws);
        // argv present but empty list.
        let mut args = HashMap::new();
        args.insert("argv".into(), json!([] as [Value; 0]));
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(!r.success);
        assert_eq!(r.error_code.as_deref(), Some("shell_empty_argv"));
        let detail = r.metadata.get("detail").and_then(|v| v.as_str()).unwrap();
        assert!(detail.contains("argv"), "got: {detail}");
    }

    #[tokio::test]
    async fn missing_file_path_returns_typed_error() {
        let (_d, ws) = tmp_workspace();
        let runner = LocalToolRunner::new(ws);
        let r = runner.run("file_read", HashMap::new(), None, None).await;
        assert!(!r.success);
        assert_eq!(r.error_code.as_deref(), Some("missing_file_path"));
    }

    #[tokio::test]
    async fn path_escape_rejected_on_file_read() {
        let (_d, ws) = tmp_workspace();
        let runner = LocalToolRunner::new(ws);
        let mut args = HashMap::new();
        args.insert("file_path".into(), json!("../escape"));
        let r = runner.run("file_read", args, None, None).await;
        assert!(!r.success);
        assert_eq!(r.error_code.as_deref(), Some("path_outside_workspace"));
    }

    #[test]
    fn spawn_detail_adds_jail_hint_when_not_found_in_jail() {
        let d = spawn_failure_detail("No such file or directory (os error 2)", "git", true, true);
        assert!(d.contains("No such file or directory"));
        assert!(d.contains("'git' is not installed in the jail image"));
        assert!(d.contains("jail_image"));
    }

    #[test]
    fn spawn_detail_unchanged_outside_jail() {
        let base = "No such file or directory (os error 2)";
        // Not in jail: raw error, no hint (the binary is genuinely missing on
        // the host, not a jail-image gap).
        assert_eq!(spawn_failure_detail(base, "git", true, false), base);
        // In jail but a non-NotFound error (e.g. permission): no image hint.
        assert_eq!(spawn_failure_detail(base, "git", false, true), base);
    }

    #[test]
    fn spawn_detail_explains_shell_builtin() {
        // `cd` (and friends) are builtins, not programs — the hint must explain
        // the stateless-shell_exec gotcha, and take priority over the jail hint.
        let base = "No such file or directory (os error 2)";
        for builtin in ["cd", "export", "source"] {
            let d = spawn_failure_detail(base, builtin, true, true);
            assert!(
                d.contains("shell builtin"),
                "missing builtin hint for {builtin}"
            );
            assert!(d.contains("bash"), "missing chaining example for {builtin}");
            // The jail-image hint must NOT also fire for a builtin.
            assert!(
                !d.contains("jail image"),
                "builtin {builtin} wrongly got jail hint"
            );
        }
        // A real missing binary still gets the jail hint, not the builtin one.
        let d = spawn_failure_detail(base, "git", true, true);
        assert!(d.contains("jail image"));
        assert!(!d.contains("shell builtin"));
    }

    #[tokio::test]
    async fn shell_argv_string_is_tokenized_not_rejected() {
        // A command STRING is now tokenized with shlex (shell-free), not bounced
        // with "needs a list" — it goes through the SAME policy as an argv list.
        // Here: non-interactive, isolated allowlist -> denied by policy, proving
        // it parsed to argv ["pytest","-q"] rather than being string-rejected.
        let (_d, ws) = tmp_workspace();
        let allow_path = ws.join(".allow.json");
        let runner =
            LocalToolRunner::new(ws.clone()).with_allowlist(ShellAllowlist::new(allow_path));
        let mut args = HashMap::new();
        args.insert("argv".into(), json!("pytest -q"));
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(!r.success);
        assert_eq!(r.error_code.as_deref(), Some("shell_denied"));
        assert_ne!(r.error_code.as_deref(), Some("shell_requires_argv_list"));
    }

    #[tokio::test]
    async fn shell_string_runs_when_allowed() {
        // The split must produce a runnable argv: "true arg" -> ["true","arg"].
        let (_d, ws) = tmp_workspace();
        let allow_path = ws.join(".allow.json");
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(allow_path))
            .with_confirmer(Box::new(AlwaysApprove));
        let mut args = HashMap::new();
        args.insert("argv".into(), json!("true ignored-arg"));
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(r.success, "shell_exec from string failed: {r:?}");
        assert_eq!(r.output["returncode"], json!(0));
    }

    #[tokio::test]
    async fn shell_string_deny_still_applies_to_argv0() {
        // Tokenizing must NOT bypass the deny policy: "rm -rf x" -> argv0 "rm".
        let (_d, ws) = tmp_workspace();
        let allow_path = ws.join(".allow.json");
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(allow_path))
            .with_confirmer(Box::new(AlwaysApprove))
            .with_shell_policy(&[], &["rm".to_string()], false);
        let mut args = HashMap::new();
        args.insert("argv".into(), json!("rm -rf /tmp/nope"));
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(!r.success);
        assert_eq!(r.error_code.as_deref(), Some("shell_denied_by_policy"));
    }

    #[tokio::test]
    async fn shell_unparseable_string_is_clear() {
        // Unbalanced quotes can't tokenize -> a clear, actionable error.
        // (Only the strict, non-allow_all path tokenizes.)
        let (_d, ws) = tmp_workspace();
        let runner = LocalToolRunner::new(ws);
        let mut args = HashMap::new();
        args.insert("argv".into(), json!("echo \"unterminated"));
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(!r.success);
        assert_eq!(r.error_code.as_deref(), Some("shell_unparseable_command"));
    }

    #[cfg(unix)] // requires a POSIX shell (bash); the Windows runner has no usable bash
    #[tokio::test]
    async fn shell_string_allow_all_runs_through_shell_with_cd() {
        // allow_all -> a string runs via `bash -lc`, so `cd sub && pwd` works
        // (the stateless-shell_exec cascade fix). pwd must land in the subdir.
        let (_d, ws) = tmp_workspace();
        std::fs::create_dir(ws.join("sub")).unwrap();
        let allow_path = ws.join(".allow.json");
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(allow_path))
            .with_shell_policy(&[], &[], true);
        let mut args = HashMap::new();
        args.insert("argv".into(), json!("cd sub && pwd"));
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(r.success, "bash -lc cd should succeed: {r:?}");
        let out = r.output["stdout"].as_str().unwrap_or_default();
        assert!(out.trim_end().ends_with("sub"), "cwd not in sub: {out:?}");
    }

    #[cfg(unix)] // requires a POSIX shell (bash); the Windows runner has no usable bash
    #[tokio::test]
    async fn shell_string_allow_all_runs_pipeline() {
        // Pipes/redirects work under allow_all (a real shell), not under strict.
        let (_d, ws) = tmp_workspace();
        let allow_path = ws.join(".allow.json");
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(allow_path))
            .with_shell_policy(&[], &[], true);
        let mut args = HashMap::new();
        args.insert("argv".into(), json!("echo hi | tr a-z A-Z"));
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(r.success, "pipeline should run: {r:?}");
        assert_eq!(r.output["stdout"].as_str().unwrap_or_default().trim(), "HI");
    }

    #[test]
    fn list_needs_shell_detects_builtins_and_operators() {
        assert!(list_needs_shell(&["cd".into(), "x".into()]));
        assert!(list_needs_shell(&[
            "echo".into(),
            "hi".into(),
            "&&".into(),
            "ls".into()
        ]));
        assert!(list_needs_shell(&["a".into(), "|".into(), "b".into()]));
        assert!(!list_needs_shell(&["echo".into(), "hello".into()]));
        assert!(!list_needs_shell(&["ls".into(), "-la".into()]));
    }

    #[test]
    fn join_for_shell_quotes_args_keeps_operators() {
        // operators raw, spaced arg quoted.
        let line = join_for_shell(&["echo".into(), "a b".into(), "&&".into(), "ls".into()]);
        assert_eq!(line, "echo 'a b' && ls");
    }

    #[cfg(unix)] // requires a POSIX shell (bash); the Windows runner has no usable bash
    #[tokio::test]
    async fn shell_list_cd_chain_runs_via_shell() {
        // The real-world regression: ["cd","sub","&&","pwd"] as a LIST must run.
        let (_d, ws) = tmp_workspace();
        std::fs::create_dir(ws.join("sub")).unwrap();
        let allow_path = ws.join(".allow.json");
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(allow_path))
            .with_shell_policy(&[], &[], true);
        let mut args = HashMap::new();
        args.insert("argv".into(), json!(["cd", "sub", "&&", "pwd"]));
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(r.success, "list cd chain should run: {r:?}");
        assert!(r.output["stdout"]
            .as_str()
            .unwrap_or_default()
            .trim_end()
            .ends_with("sub"));
    }

    #[cfg(unix)] // requires a POSIX shell (bash); the Windows runner has no usable bash
    #[tokio::test]
    async fn shell_list_bare_cd_is_noop_success_not_error() {
        // ["cd","sub"] alone no longer errors as a builtin — it's a no-op ok.
        let (_d, ws) = tmp_workspace();
        std::fs::create_dir(ws.join("sub")).unwrap();
        let allow_path = ws.join(".allow.json");
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(allow_path))
            .with_shell_policy(&[], &[], true);
        let mut args = HashMap::new();
        args.insert("argv".into(), json!(["cd", "sub"]));
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(r.success, "bare cd should be a no-op success: {r:?}");
        assert_eq!(r.error_code, None);
    }

    #[tokio::test]
    async fn shell_list_normal_command_stays_direct() {
        // No builtin/operator → direct spawn, args preserved verbatim.
        let (_d, ws) = tmp_workspace();
        let allow_path = ws.join(".allow.json");
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(allow_path))
            .with_shell_policy(&[], &[], true);
        let mut args = HashMap::new();
        args.insert("argv".into(), json!(["echo", "hello world"]));
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(r.success, "{r:?}");
        assert_eq!(
            r.output["stdout"].as_str().unwrap_or_default().trim(),
            "hello world"
        );
    }

    #[test]
    fn long_running_detects_server_commands() {
        let cases = [
            vec!["pnpm", "docker:dev"],
            vec!["npm", "run", "dev"],
            vec!["npm", "start"],
            vec!["yarn", "dev"],
            vec!["bash", "-lc", "vite"],
            vec!["uvicorn", "main:app"],
            vec!["docker", "compose", "up"],
            vec!["docker", "compose", "up", "--build"],
        ];
        for c in cases {
            let argv: Vec<String> = c.iter().map(|s| s.to_string()).collect();
            assert!(is_long_running_command(&argv), "should detect: {c:?}");
        }
    }

    #[test]
    fn long_running_ignores_one_shot_and_detached() {
        let cases = [
            vec!["pytest", "-q"],
            vec!["npm", "run", "build"],
            vec!["ls", "-la"],
            vec!["docker", "compose", "up", "-d"], // detached
            vec!["docker", "compose", "up", "--abort-on-container-exit"],
            vec!["bash", "-lc", "npm start &"], // backgrounded inside the line
            vec!["nohup", "npm", "start"],
            vec!["timeout", "5", "npm", "start"],
        ];
        for c in cases {
            let argv: Vec<String> = c.iter().map(|s| s.to_string()).collect();
            assert!(!is_long_running_command(&argv), "should NOT detect: {c:?}");
        }
    }

    #[cfg(unix)] // uses `bash -lc … sleep`; the Windows runner has no usable bash
    #[tokio::test]
    async fn long_running_command_returns_started_not_timeout() {
        // A server that outlives the grace window must come back as a SUCCESS
        // ("started"), not a shell_timeout — and well before shell_timeout.
        let (_d, ws) = tmp_workspace();
        let allow_path = ws.join(".allow.json");
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(allow_path))
            .with_shell_policy(&[], &[], true)
            .with_long_running_grace(Duration::from_millis(300));
        // `# vite` makes is_long_running_command match; `sleep 2` outlives the
        // 300ms grace, then exits on its own so the detached drainer cleans up.
        let mut args = HashMap::new();
        args.insert("argv".into(), json!(["bash", "-lc", "# vite\nsleep 2"]));
        let started = Instant::now();
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(
            r.success,
            "long-running server should report success: {r:?}"
        );
        assert_eq!(r.error_code, None);
        assert_eq!(r.metadata.get("long_running"), Some(&json!(true)));
        assert!(
            started.elapsed() < Duration::from_secs(30),
            "must return after the grace, not block to shell_timeout"
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn idle_detects_quiet_unknown_command() {
        // A command with NO long-running marker that prints once then goes quiet
        // must still be detached as "started" on the idle window — proving the
        // behavior-based path no longer depends on the marker list.
        let (_d, ws) = tmp_workspace();
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(ws.join(".allow.json")))
            .with_shell_policy(&[], &[], true)
            .with_shell_idle(Duration::from_millis(300));
        let mut args = HashMap::new();
        // `bash -lc "echo hi; sleep 3"` matches no marker; idle fires 300ms after
        // the `hi`, well before the 3s exit.
        args.insert("argv".into(), json!(["bash", "-lc", "echo hi; sleep 3"]));
        let started = Instant::now();
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(
            r.success,
            "quiet unknown command should detach as started: {r:?}"
        );
        assert_eq!(r.error_code, None);
        assert_eq!(r.output.get("status"), Some(&json!("started")));
        assert!(
            r.output
                .get("stdout")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .contains("hi"),
            "snapshot must include output produced before settling: {r:?}"
        );
        assert!(started.elapsed() < Duration::from_secs(3));
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn streaming_past_ceiling_returns_running_not_error() {
        // A command that keeps producing output past the total ceiling is a long
        // but LIVE job: it must come back as success "running", never an error,
        // and never be killed.
        let (_d, ws) = tmp_workspace();
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(ws.join(".allow.json")))
            .with_shell_policy(&[], &[], true)
            .with_shell_timeout(Duration::from_millis(400))
            .with_shell_idle(Duration::from_secs(5));
        let mut args = HashMap::new();
        // Emits a line every 50ms for ~2.5s: idle (5s) never fires, the 400ms
        // ceiling does. The child exits on its own so the drainer reaps it.
        args.insert(
            "argv".into(),
            json!([
                "bash",
                "-lc",
                "for i in $(seq 1 50); do echo .; sleep 0.05; done"
            ]),
        );
        let started = Instant::now();
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(r.success, "streaming job should report success: {r:?}");
        assert_eq!(r.error_code, None);
        assert_eq!(r.output.get("status"), Some(&json!("running")));
        assert!(started.elapsed() < Duration::from_secs(2));
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn quiet_oneshot_under_idle_exits_normally() {
        // A quiet command that finishes before the idle window must return its
        // real outcome (exit 0), NOT be falsely detached as a server.
        let (_d, ws) = tmp_workspace();
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(ws.join(".allow.json")))
            .with_shell_policy(&[], &[], true)
            .with_shell_idle(Duration::from_secs(5));
        let mut args = HashMap::new();
        args.insert("argv".into(), json!(["bash", "-lc", "sleep 0.2"]));
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(r.success, "quiet one-shot should succeed: {r:?}");
        assert_eq!(r.error_code, None);
        assert!(
            !r.metadata.contains_key("long_running"),
            "a finished one-shot must NOT be reported as long-running: {r:?}"
        );
    }

    #[test]
    fn build_output_is_detected() {
        assert!(output_looks_like_build(b"webpack: compiling...", b""));
        assert!(output_looks_like_build(b"", b"Installing dependencies"));
        assert!(!output_looks_like_build(
            b"Listening on http://localhost:3000",
            b""
        ));
        assert!(!output_looks_like_build(b"", b""));
    }

    #[test]
    fn listen_ports_are_extracted() {
        // Plain host:port, ANSI-coloured port, and "port N" forms.
        assert_eq!(
            extract_listen_ports(b"Local: http://localhost:3000", b""),
            vec![3000]
        );
        assert_eq!(
            extract_listen_ports(b"view it at http://localhost:\x1b[1m5173\x1b[22m now", b""),
            vec![5173]
        );
        assert_eq!(
            extract_listen_ports(b"Uvicorn running on port 8000", b""),
            vec![8000]
        );
        // Unprivileged ports are ordered ahead of low ones (a timestamp ":11").
        let ports = extract_listen_ports(b"15:11:28 serving on 127.0.0.1:4321", b"");
        assert_eq!(ports.first(), Some(&4321));
        // No port at all.
        assert!(extract_listen_ports(b"hello world", b"").is_empty());
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn ready_port_is_probed_and_reported() {
        // Bind a real listener, announce its port in the command output, and
        // assert the detached server is reported with a verified `ready_port`.
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let (_d, ws) = tmp_workspace();
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(ws.join(".allow.json")))
            .with_shell_policy(&[], &[], true)
            .with_shell_idle(Duration::from_millis(300));
        let mut args = HashMap::new();
        args.insert(
            "argv".into(),
            json!([
                "bash",
                "-lc",
                format!("echo serving on 127.0.0.1:{port}; sleep 3")
            ]),
        );
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(r.success, "{r:?}");
        assert_eq!(r.metadata.get("ready_port"), Some(&json!(port)));
    }

    #[tokio::test]
    async fn no_port_announced_means_no_ready_port() {
        // When the output announces no port, nothing is probed and no
        // `ready_port` is attached. (Deterministic — no reliance on a free port,
        // which can be re-bound between drop and connect.)
        assert!(
            first_listening_port(b"compiled successfully, no url here", b"")
                .await
                .is_none()
        );
    }

    #[tokio::test]
    async fn cd_to_missing_dir_errors() {
        // A standalone `cd` is now a persistent state change (both modes); a
        // non-existent target is a clean `shell_cd_failed`, no process spawned.
        let (_d, ws) = tmp_workspace();
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(ws.join(".allow.json")))
            .with_confirmer(Box::new(AlwaysApprove));
        let mut args = HashMap::new();
        args.insert("argv".into(), json!(["cd", "sub"]));
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(!r.success);
        assert_eq!(r.error_code.as_deref(), Some("shell_cd_failed"));
    }

    #[test]
    fn state_change_detection() {
        let toks = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(
            detect_state_change(&toks(&["cd", "app"])),
            Some(StateChange::Cd(Some("app".into())))
        );
        assert_eq!(
            detect_state_change(&toks(&["cd"])),
            Some(StateChange::Cd(None))
        );
        assert_eq!(
            detect_state_change(&toks(&["export", "CI=true"])),
            Some(StateChange::Export("CI".into(), "true".into()))
        );
        // Chained forms are NOT a pure state change.
        assert_eq!(detect_state_change(&toks(&["cd", "app", "&&", "ls"])), None);
        assert_eq!(detect_state_change(&toks(&["ls", "-la"])), None);
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn cwd_persists_across_calls() {
        // `cd sub` then a bare command must run inside `sub` on the next call.
        let (_d, ws) = tmp_workspace();
        std::fs::create_dir(ws.join("sub")).unwrap();
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(ws.join(".allow.json")))
            .with_shell_policy(&[], &[], true);
        // 1) cd sub
        let mut a = HashMap::new();
        a.insert("argv".into(), json!(["cd", "sub"]));
        let r = runner.run("shell_exec", a, None, None).await;
        assert!(r.success, "cd should succeed: {r:?}");
        assert_eq!(r.metadata.get("cwd"), Some(&json!("sub")));
        // 2) pwd (bare) runs in sub
        let mut b = HashMap::new();
        b.insert("argv".into(), json!(["bash", "-lc", "pwd"]));
        let r = runner.run("shell_exec", b, None, None).await;
        assert!(r.success, "{r:?}");
        let out = r.output.get("output").unwrap_or(&r.output);
        let stdout = out.get("stdout").and_then(|v| v.as_str()).unwrap_or("");
        assert!(
            stdout.trim_end().ends_with("/sub"),
            "pwd not in sub: {stdout:?}"
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn export_persists_across_calls() {
        let (_d, ws) = tmp_workspace();
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(ws.join(".allow.json")))
            .with_shell_policy(&[], &[], true);
        let mut a = HashMap::new();
        a.insert("argv".into(), json!(["export", "AGO_TESTV=hello"]));
        assert!(runner.run("shell_exec", a, None, None).await.success);
        let mut b = HashMap::new();
        b.insert("argv".into(), json!(["bash", "-lc", "echo $AGO_TESTV"]));
        let r = runner.run("shell_exec", b, None, None).await;
        let stdout = r
            .output
            .get("output")
            .unwrap_or(&r.output)
            .get("stdout")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        assert!(stdout.contains("hello"), "export not visible: {stdout:?}");
    }

    #[tokio::test]
    async fn shell_string_allow_all_still_honors_deny_bash() {
        // The shell wrapper must not bypass a `deny: [bash]` — argv0 is `bash`.
        let (_d, ws) = tmp_workspace();
        let allow_path = ws.join(".allow.json");
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(allow_path))
            .with_shell_policy(&[], &["bash".to_string()], true);
        let mut args = HashMap::new();
        args.insert("argv".into(), json!("echo hi"));
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(!r.success);
        assert_eq!(r.error_code.as_deref(), Some("shell_denied_by_policy"));
    }

    #[tokio::test]
    async fn shell_unknown_binary_rejected_when_noninteractive() {
        let (_d, ws) = tmp_workspace();
        // Isolated allowlist so test order does not leak previous allows.
        let allow_path = ws.join(".allow.json");
        let runner =
            LocalToolRunner::new(ws.clone()).with_allowlist(ShellAllowlist::new(allow_path));
        let mut args = HashMap::new();
        args.insert("argv".into(), json!(["pytest", "-q"]));
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(!r.success);
        assert_eq!(r.error_code.as_deref(), Some("shell_denied"));
    }

    struct AlwaysApprove;

    impl ShellConfirmer for AlwaysApprove {
        fn confirm<'a>(
            &'a self,
            _binary: &'a str,
            _high_risk: bool,
        ) -> std::pin::Pin<Box<dyn std::future::Future<Output = bool> + Send + 'a>> {
            Box::pin(async { true })
        }
    }

    #[tokio::test]
    async fn shell_runs_after_approval() {
        let (_d, ws) = tmp_workspace();
        let allow_path = ws.join(".allow.json");
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(allow_path))
            .with_confirmer(Box::new(AlwaysApprove));
        // `true` is universally available on unix runners.
        let mut args = HashMap::new();
        args.insert("argv".into(), json!(["true"]));
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(r.success, "shell_exec failed: {r:?}");
        assert_eq!(r.output["returncode"], json!(0));
    }

    #[tokio::test]
    async fn shell_deny_policy_blocks_even_with_approver() {
        // A `deny` entry must win over an always-approving confirmer — the
        // binary is refused before it can ever be spawned.
        let (_d, ws) = tmp_workspace();
        let allow_path = ws.join(".allow.json");
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(allow_path))
            .with_confirmer(Box::new(AlwaysApprove))
            .with_shell_policy(&[], &["rm".to_string()], false);
        let mut args = HashMap::new();
        // Never actually runs — refused at the gate.
        args.insert("argv".into(), json!(["rm", "-rf", "/tmp/nope"]));
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(!r.success);
        assert_eq!(r.error_code.as_deref(), Some("shell_denied_by_policy"));
    }

    #[tokio::test]
    async fn shell_deny_matches_by_basename() {
        // A path alias cannot slip past `deny: [rm]`.
        let (_d, ws) = tmp_workspace();
        let runner = LocalToolRunner::new(ws.clone())
            .with_confirmer(Box::new(AlwaysApprove))
            .with_shell_policy(&[], &["rm".to_string()], false);
        let mut args = HashMap::new();
        args.insert("argv".into(), json!(["/usr/bin/rm", "-rf", "/tmp/nope"]));
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(!r.success);
        assert_eq!(r.error_code.as_deref(), Some("shell_denied_by_policy"));
    }

    #[tokio::test]
    async fn shell_project_allow_runs_without_prompt_or_persist() {
        // A project `allow` entry runs with NO confirmer present (which would
        // otherwise refuse) and must NOT write the global allowlist cache.
        let (_d, ws) = tmp_workspace();
        let allow_path = ws.join(".allow.json");
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(allow_path.clone()))
            .with_shell_policy(&["true".to_string()], &[], false);
        let mut args = HashMap::new();
        args.insert("argv".into(), json!(["true"]));
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(r.success, "project-allowed binary should run: {r:?}");
        assert_eq!(r.output["returncode"], json!(0));
        // Project allow is session-local: the global cache is untouched.
        assert!(
            !allow_path.exists(),
            "project allow must not persist to cache"
        );
    }

    #[tokio::test]
    async fn shell_allow_all_runs_unlisted_without_prompt() {
        // allow_all flips the gate to default-allow: an unlisted binary runs
        // with NO confirmer present and is NOT persisted to the cache.
        let (_d, ws) = tmp_workspace();
        let allow_path = ws.join(".allow.json");
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(allow_path.clone()))
            .with_shell_policy(&[], &[], true);
        let mut args = HashMap::new();
        args.insert("argv".into(), json!(["true"]));
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(r.success, "allow_all should run unlisted binary: {r:?}");
        assert!(!allow_path.exists(), "allow_all must not persist to cache");
    }

    #[tokio::test]
    async fn shell_deny_wins_over_allow_all() {
        // Even with allow_all, a denied binary is a hard block.
        let (_d, ws) = tmp_workspace();
        let runner =
            LocalToolRunner::new(ws.clone()).with_shell_policy(&[], &["rm".to_string()], true);
        let mut args = HashMap::new();
        args.insert("argv".into(), json!(["rm", "-rf", "/tmp/nope"]));
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(!r.success);
        assert_eq!(r.error_code.as_deref(), Some("shell_denied_by_policy"));
    }

    #[tokio::test]
    async fn live_process_at_deadline_is_detached_not_errored() {
        // New contract (see docs/shell-execution-redesign.md): a process that is
        // still ALIVE when a clock fires is detached and reported as a success,
        // never killed-and-errored. With idle < ceiling, the idle clock wins and
        // the (silent) `sleep` is reported as "started".
        let (_d, ws) = tmp_workspace();
        let allow_path = ws.join(".allow.json");
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(allow_path))
            .with_confirmer(Box::new(AlwaysApprove))
            .with_shell_idle(Duration::from_millis(50))
            .with_shell_timeout(Duration::from_millis(400));
        let mut args = HashMap::new();
        args.insert("argv".into(), json!(["sleep", "5"]));
        let started = Instant::now();
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(r.success, "live process must not be an error: {r:?}");
        assert_eq!(r.error_code, None);
        assert_eq!(r.output.get("status"), Some(&json!("started")));
        assert!(started.elapsed() < Duration::from_secs(2));
    }

    #[tokio::test]
    async fn shell_cancel_returns_typed_error() {
        let (_d, ws) = tmp_workspace();
        let allow_path = ws.join(".allow.json");
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(allow_path))
            .with_confirmer(Box::new(AlwaysApprove));
        let cancel: CancelSignal = Arc::new(Notify::new());
        let cancel_for_kicker = cancel.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(60)).await;
            cancel_for_kicker.notify_one();
        });
        let mut args = HashMap::new();
        args.insert("argv".into(), json!(["sleep", "5"]));
        let r = runner.run("shell_exec", args, None, Some(cancel)).await;
        assert!(!r.success);
        assert_eq!(r.error_code.as_deref(), Some("shell_cancelled"));
    }

    #[tokio::test]
    async fn unknown_tool_typed() {
        let (_d, ws) = tmp_workspace();
        let runner = LocalToolRunner::new(ws);
        let r = runner.run("nope", HashMap::new(), None, None).await;
        assert!(!r.success);
        assert_eq!(r.error_code.as_deref(), Some("unknown_tool"));
    }

    #[test]
    fn manifest_lists_three_tools() {
        let m = LocalToolRunner::manifest();
        assert!(m.contains(&"file_read".to_string()));
        assert!(m.contains(&"file_write".to_string()));
        assert!(m.contains(&"shell_exec".to_string()));
    }
}
