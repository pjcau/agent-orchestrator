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
    /// "started" and detached (instead of blocking to `shell_timeout`).
    long_running_grace: Duration,
    /// Project-scoped shell policy (from `.ago.yaml`). `deny` hard-blocks,
    /// `project_allow` pre-approves without touching the global cache.
    /// Both are `argv[0]` basenames. `allow_all` flips the gate to
    /// default-allow (anything not in `deny` runs without a prompt).
    deny: HashSet<String>,
    project_allow: HashSet<String>,
    allow_all: bool,
}

impl LocalToolRunner {
    pub fn new(workspace: PathBuf) -> Self {
        Self {
            workspace,
            allowlist: tokio::sync::Mutex::new(ShellAllowlist::at_default()),
            confirm: None,
            shell_timeout: SHELL_DEFAULT_TIMEOUT,
            long_running_grace: LONG_RUNNING_GRACE,
            deny: HashSet::new(),
            project_allow: HashSet::new(),
            allow_all: false,
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
        match fs::read_to_string(&resolved).await {
            Ok(content) => ToolOutcome::ok(Value::String(content)),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => ToolOutcome::err("not_found")
                .with_meta("path", json!(resolved.display().to_string())),
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
            Ok(()) => ToolOutcome::ok(json!({
                "bytes_written": content.len(),
                "path": resolved.display().to_string(),
            })),
            Err(e) => ToolOutcome::err("io_error").with_meta("detail", json!(e.to_string())),
        }
    }

    // -----------------------------------------------------------------
    // shell_exec
    // -----------------------------------------------------------------

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
                return ToolOutcome::err("shell_empty_argv");
            }
        };
        if argv.is_empty() {
            return ToolOutcome::err("shell_empty_argv");
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

        // Spawn the process — argv list, never shell=True. Env retains
        // the calling environment; AGENT_HOST=1 lets called processes
        // detect they're under agent-host if they care.
        let mut cmd = Command::new(&argv[0]);
        cmd.args(&argv[1..])
            .current_dir(&self.workspace)
            .env("AGENT_HOST", "1")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

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
        let mut timed_out = false;
        // Long-running server commands (dev server, `compose up`, uvicorn, …)
        // never exit, so we wait only `long_running_grace` for an early crash;
        // if still alive we detach instead of killing at `shell_timeout`.
        let long_running = is_long_running_command(&argv);
        let mut detached = false;
        let effective_timeout = if long_running {
            self.long_running_grace
        } else {
            self.shell_timeout
        };

        // Co-operative reader loop: select between stdout / stderr /
        // process exit / cancel signal / timeout. Cap output at
        // SHELL_OUTPUT_CAP combined; once hit, stop reading and let
        // the process exit naturally (or kill on timeout).
        let cancel_fut = async {
            match cancel {
                Some(notify) => notify.notified().await,
                None => std::future::pending::<()>().await,
            }
        };
        tokio::pin!(cancel_fut);
        let timeout_fut = tokio::time::sleep(effective_timeout);
        tokio::pin!(timeout_fut);

        let mut stdout_done = false;
        let mut stderr_done = false;

        loop {
            tokio::select! {
                biased;
                _ = &mut cancel_fut => {
                    cancelled = true;
                    let _ = child.kill().await;
                    break;
                }
                _ = &mut timeout_fut => {
                    if long_running {
                        // Server still alive after the grace window: leave it
                        // running, report success below.
                        detached = true;
                    } else {
                        timed_out = true;
                        let _ = child.kill().await;
                    }
                    break;
                }
                res = read_chunk(&mut stdout, SHELL_CHUNK_BYTES), if !stdout_done => {
                    match res {
                        Ok(Some(buf)) => {
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
            }
        }

        // Long-running server still alive after the grace window: report it as
        // started, then move the child + its pipes into a detached task that
        // drains stdout/stderr until the process exits. Draining matters — an
        // unread pipe buffer would eventually stall the server — and we never
        // kill it, so the app stays up for the agent's follow-up health checks.
        if detached {
            let argv0 = argv[0].clone();
            let snapshot = String::from_utf8_lossy(&out_buf).to_string();
            let grace_s = effective_timeout.as_secs();
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
            return ToolOutcome::ok(json!({
                "stdout": snapshot,
                "stderr": "",
                "returncode": Value::Null,
                "status": "started",
            }))
            .with_meta("argv0", json!(argv0))
            .with_meta("long_running", json!(true))
            .with_meta(
                "detail",
                json!(format!(
                    "'{argv0}' is a long-running server: it was still running after \
                     {grace_s}s, so it was reported as started and left running in the \
                     background. Do NOT re-run it; verify it with a health check \
                     (e.g. `curl localhost:<port>` or `docker compose ps`)."
                )),
            );
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
        return ToolOutcome::err("shell_timeout")
            .with_meta("argv0", json!(argv[0]))
            .with_meta(
                "output",
                json!({"stdout": stdout, "stderr": stderr, "returncode": returncode}),
            );
    }
    let success = returncode == Some(0);
    let mut out = ToolOutcome {
        success,
        output: json!({
            "stdout": stdout,
            "stderr": stderr,
            "returncode": returncode,
        }),
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

    #[tokio::test]
    async fn shell_list_cd_strict_still_fails_without_allow_all() {
        // Without allow_all, a list `cd` keeps the strict (direct-spawn) path.
        let (_d, ws) = tmp_workspace();
        let allow_path = ws.join(".allow.json");
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(allow_path))
            .with_confirmer(Box::new(AlwaysApprove));
        let mut args = HashMap::new();
        args.insert("argv".into(), json!(["cd", "sub"]));
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(!r.success);
        // `cd` is a shell builtin, so the strict direct-spawn path fails — which
        // is the point of the test. *How* it fails is platform-dependent: Linux
        // has no `cd` binary (shell_spawn_failed), while macOS ships a
        // `/usr/bin/cd` shim that spawns and exits non-zero (shell_nonzero_exit).
        // Either proves it ran directly rather than via a bash wrapper.
        assert!(
            matches!(
                r.error_code.as_deref(),
                Some("shell_spawn_failed") | Some("shell_nonzero_exit")
            ),
            "unexpected error_code: {:?}",
            r.error_code
        );
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
    async fn shell_timeout_returns_typed_error() {
        let (_d, ws) = tmp_workspace();
        let allow_path = ws.join(".allow.json");
        let runner = LocalToolRunner::new(ws.clone())
            .with_allowlist(ShellAllowlist::new(allow_path))
            .with_confirmer(Box::new(AlwaysApprove))
            .with_shell_timeout(Duration::from_millis(80));
        let mut args = HashMap::new();
        args.insert("argv".into(), json!(["sleep", "5"]));
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(!r.success);
        assert_eq!(r.error_code.as_deref(), Some("shell_timeout"));
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
