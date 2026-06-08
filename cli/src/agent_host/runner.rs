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
    /// Project-scoped shell policy (from `.ago.yaml`). `deny` hard-blocks,
    /// `project_allow` pre-approves without touching the global cache.
    /// Both are `argv[0]` basenames.
    deny: HashSet<String>,
    project_allow: HashSet<String>,
}

impl LocalToolRunner {
    pub fn new(workspace: PathBuf) -> Self {
        Self {
            workspace,
            allowlist: tokio::sync::Mutex::new(ShellAllowlist::at_default()),
            confirm: None,
            shell_timeout: SHELL_DEFAULT_TIMEOUT,
            deny: HashSet::new(),
            project_allow: HashSet::new(),
        }
    }

    pub fn with_allowlist(mut self, allowlist: ShellAllowlist) -> Self {
        self.allowlist = tokio::sync::Mutex::new(allowlist);
        self
    }

    /// Apply a project shell policy. Entries are normalised to basenames so
    /// they match the same way the allowlist cache does.
    pub fn with_shell_policy(mut self, allow: &[String], deny: &[String]) -> Self {
        let base = |s: &String| {
            Path::new(s)
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or(s)
                .to_string()
        };
        self.project_allow = allow.iter().map(base).collect();
        self.deny = deny.iter().map(base).collect();
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
        // Refuse string-form argv outright — shell=True would be unsafe.
        let argv_value = first_present_value(args, &["argv", "command", "cmd", "args"]);
        let argv: Vec<String> = match argv_value {
            Some(Value::Array(arr)) => arr
                .iter()
                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                .collect(),
            Some(Value::String(_)) => {
                return ToolOutcome::err("shell_requires_argv_list").with_meta(
                    "detail",
                    json!("shell_exec via agent-host expects argv as a list; got a string"),
                );
            }
            _ => {
                return ToolOutcome::err("shell_empty_argv");
            }
        };
        if argv.is_empty() {
            return ToolOutcome::err("shell_empty_argv");
        }

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
        // without one, refuse (fail-closed). A project `allow` entry
        // short-circuits the prompt without persisting to the global cache.
        if !self.project_allow.contains(&bin_base) {
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
                return ToolOutcome::err("shell_spawn_failed")
                    .with_meta("argv0", json!(argv[0]))
                    .with_meta("detail", json!(e.to_string()));
            }
        };

        let mut stdout = child.stdout.take().expect("stdout piped");
        let mut stderr = child.stderr.take().expect("stderr piped");

        let started = Instant::now();
        let mut out_buf = Vec::<u8>::new();
        let mut err_buf = Vec::<u8>::new();
        let mut cancelled = false;
        let mut timed_out = false;

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
        let timeout_fut = tokio::time::sleep(self.shell_timeout);
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
                    timed_out = true;
                    let _ = child.kill().await;
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

    #[tokio::test]
    async fn shell_argv_string_rejected() {
        let (_d, ws) = tmp_workspace();
        let runner = LocalToolRunner::new(ws);
        let mut args = HashMap::new();
        args.insert("argv".into(), json!("rm -rf /"));
        let r = runner.run("shell_exec", args, None, None).await;
        assert!(!r.success);
        assert_eq!(r.error_code.as_deref(), Some("shell_requires_argv_list"));
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
            .with_shell_policy(&[], &["rm".to_string()]);
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
            .with_shell_policy(&[], &["rm".to_string()]);
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
            .with_shell_policy(&["true".to_string()], &[]);
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
