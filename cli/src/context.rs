//! `@file` and `@dir` reference expansion.
//!
//! Users can write things like:
//!
//! ```text
//! > explain @src/main.rs to me
//! > compare @./Cargo.toml and @cli/Cargo.toml
//! > what's in @src/  (trailing slash → directory listing)
//! ```
//!
//! before the prompt is sent to the server, this module rewrites the input
//! so each `@<path>` becomes (a) the original token left in place AND (b)
//! a code-fenced block appended at the end with the file content or
//! directory listing. The server / LLM sees the full context inlined —
//! `ago` itself does the file reading on the user's machine, which keeps
//! the security boundary at the CLI.
//!
//! Safe defaults are enforced (size caps, exclude patterns) so a giant
//! `@dir(./node_modules)` cannot accidentally cost real money in tokens.

use crate::error::{AgoError, Result};
use globset::{Glob, GlobSet, GlobSetBuilder};
use std::path::{Path, PathBuf};

/// Settings for how `@<path>` references are expanded.
#[derive(Debug, Clone)]
pub struct ContextConfig {
    /// Cap per single file in bytes. Files exceeding this are truncated with
    /// a clear marker so the LLM knows it did not see everything.
    pub max_file_bytes: usize,
    /// Cap total bytes across all `@` refs in a single turn.
    pub max_total_bytes: usize,
    /// Maximum number of `@` refs that can be resolved in a single turn.
    /// Hard backstop against pathological inputs.
    pub max_refs: usize,
    /// Cap on the number of files a single `@dir/**` recursive reference is
    /// allowed to inline. Independent from `max_refs`: one user-typed token
    /// counts as one ref but may fan out to up to `max_dir_files` files.
    pub max_dir_files: usize,
    /// Glob patterns that are always skipped — secrets / heavy build artifacts
    /// / dotfiles. Matched against the *path relative to the CLI's cwd*.
    pub exclude: GlobSet,
}

impl Default for ContextConfig {
    fn default() -> Self {
        let exclude = build_default_exclude();
        Self {
            max_file_bytes: 8 * 1024,   // 8 KB per file
            max_total_bytes: 50 * 1024, // 50 KB total per turn (~12K tokens)
            max_refs: 16,
            max_dir_files: 64,
            exclude,
        }
    }
}

impl ContextConfig {
    /// Apply overrides from `.ago.yaml context:`. Unset fields keep the
    /// built-in default. `exclude_extra` is APPENDED to the default
    /// deny-list — there is no API to weaken the built-in safety patterns
    /// (no `exclude_replace`), by design.
    pub fn apply_overrides(mut self, ov: &crate::project::ContextOverrides) -> Self {
        if let Some(n) = ov.max_file_bytes {
            self.max_file_bytes = n;
        }
        if let Some(n) = ov.max_total_bytes {
            self.max_total_bytes = n;
        }
        if let Some(n) = ov.max_refs {
            self.max_refs = n;
        }
        if let Some(n) = ov.max_dir_files {
            self.max_dir_files = n;
        }
        if !ov.exclude_extra.is_empty() {
            let mut b = GlobSetBuilder::new();
            // Rebuild from defaults...
            for p in DEFAULT_EXCLUDE_PATTERNS {
                if let Ok(g) = Glob::new(p) {
                    b.add(g);
                }
            }
            // ...then append the project extras.
            for p in &ov.exclude_extra {
                if let Ok(g) = Glob::new(p) {
                    b.add(g);
                }
            }
            if let Ok(set) = b.build() {
                self.exclude = set;
            }
        }
        self
    }

    /// Resolve a `ContextConfig` from the runtime's project preset (if any).
    /// Built-in defaults when no `.ago.yaml` or no `context:` block.
    pub fn from_runtime(rt: &crate::runtime::Runtime) -> Self {
        let base = Self::default();
        match rt.project.as_ref().and_then(|p| p.context.as_ref()) {
            Some(ov) => base.apply_overrides(ov),
            None => base,
        }
    }
}

const DEFAULT_EXCLUDE_PATTERNS: &[&str] = &[
    "**/.env",
    "**/.env.*",
    "**/.git/**",
    "**/secrets/**",
    "**/*secret*",
    "**/*credential*",
    "**/node_modules/**",
    "**/target/**",
    "**/dist/**",
    "**/.next/**",
    "**/.venv/**",
    "**/__pycache__/**",
    "**/*.pyc",
    "**/.DS_Store",
    "**/Cargo.lock",
    "**/package-lock.json",
    "**/yarn.lock",
];

fn build_default_exclude() -> GlobSet {
    let mut b = GlobSetBuilder::new();
    for p in DEFAULT_EXCLUDE_PATTERNS {
        if let Ok(g) = Glob::new(p) {
            b.add(g);
        }
    }
    b.build()
        .unwrap_or_else(|_| GlobSetBuilder::new().build().unwrap())
}

/// Output of an expansion pass — the rewritten prompt + a report of what
/// happened so the REPL can surface it to the user.
#[derive(Debug, Default)]
pub struct ExpandReport {
    pub resolved: Vec<ResolvedRef>,
    pub skipped: Vec<SkippedRef>,
}

#[derive(Debug)]
pub struct ResolvedRef {
    pub token: String, // exact text in the input, e.g. "@src/main.rs"
    pub path: PathBuf, // canonical path
    pub kind: RefKind, // File | Directory | DirectoryRecursive
    pub bytes: usize,  // bytes inlined (post-truncation)
    pub truncated: bool,
    /// Populated only when `kind == DirectoryRecursive`. Lets the REPL show
    /// a concise breakdown ("12 files, 3 excluded, 1 truncated").
    pub recursive: Option<RecursiveStats>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RefKind {
    File,
    Directory,
    DirectoryRecursive,
}

/// Outcome counters from a `@dir/**` walk. Used both for the stderr report
/// and to flag when the walk stopped early (file cap or byte cap hit before
/// the whole tree was exhausted).
#[derive(Debug, Default, Clone, Copy)]
pub struct RecursiveStats {
    pub files_inlined: usize,
    pub bytes: usize,
    pub excluded: usize,
    pub symlinks_skipped: usize,
    pub read_errors: usize,
    pub truncated_files: usize,
    /// True if the walk stopped because `max_dir_files` was reached and the
    /// tree still had more files to visit.
    pub stopped_files: bool,
    /// True if the walk stopped because adding the next file would have
    /// pushed the total prompt over `max_total_bytes`.
    pub stopped_bytes: bool,
}

#[derive(Debug)]
pub struct SkippedRef {
    pub token: String,
    pub reason: SkipReason,
}

#[derive(Debug, Clone)]
pub enum SkipReason {
    NotFound,
    Excluded,
    TooManyRefs,
    TotalSizeExceeded,
    ReadError(String),
}

/// Walk the input string, find every `@<path>` token whose path exists on
/// disk, and produce an expanded prompt + a report.
///
/// Returns a triple `(prompt, cache_context, report)`:
///   - `prompt` is the original user input unchanged (a small benefit for
///     downstream display, but mostly so the server can render exactly what
///     the user typed without the appended @-ref blocks).
///   - `cache_context` is the concatenation of every resolved @-ref's
///     content, formatted with labeled code-fences. The server, when
///     `cache_enabled` is on AND the provider supports it (OpenRouter,
///     Anthropic), marks this string with `cache_control: ephemeral` so
///     subsequent turns repeating the same prefix pay 10–50% of input cost.
///   - `report` is a per-token resolved / skipped log for stderr.
///
/// When no @-refs resolve, `cache_context` is empty and the caller can
/// fall back to sending only `prompt`. When the server does not honour
/// `cache_context`, it concatenates `cache_context` to `prompt` exactly as
/// `ago` v0.3.x did — no behaviour regression.
pub fn expand_refs(
    input: &str,
    cwd: &Path,
    cfg: &ContextConfig,
) -> Result<(String, String, ExpandReport)> {
    let mut report = ExpandReport::default();
    let tokens = scan_tokens(input);
    if tokens.is_empty() {
        return Ok((input.to_string(), String::new(), report));
    }

    let mut total_bytes = 0usize;
    let mut appended = String::new();

    for (idx, (token, raw_path)) in tokens.iter().enumerate() {
        if idx >= cfg.max_refs {
            report.skipped.push(SkippedRef {
                token: token.clone(),
                reason: SkipReason::TooManyRefs,
            });
            continue;
        }
        // Recursive syntax: `@src/**` → walk the dir and inline every file
        // (subject to exclude, max_dir_files, max_total_bytes). The `/**`
        // marker is stripped before path canonicalization.
        let (path_str, want_recursive, want_dir) = if let Some(p) = raw_path.strip_suffix("/**") {
            (p, true, true)
        } else if let Some(p) = raw_path.strip_suffix('/') {
            (p, false, true)
        } else {
            (raw_path.as_str(), false, false)
        };
        let resolved = resolve_path(cwd, path_str);
        let Some(canonical) = resolved else {
            report.skipped.push(SkippedRef {
                token: token.clone(),
                reason: SkipReason::NotFound,
            });
            continue;
        };
        // Apply exclude — matched against the path relative to cwd when
        // possible, otherwise the absolute path.
        let match_path = canonical
            .strip_prefix(cwd)
            .unwrap_or(&canonical)
            .to_path_buf();
        if cfg.exclude.is_match(&match_path) {
            report.skipped.push(SkippedRef {
                token: token.clone(),
                reason: SkipReason::Excluded,
            });
            continue;
        }

        let metadata = match std::fs::metadata(&canonical) {
            Ok(m) => m,
            Err(e) => {
                report.skipped.push(SkippedRef {
                    token: token.clone(),
                    reason: SkipReason::ReadError(e.to_string()),
                });
                continue;
            }
        };

        if metadata.is_dir() || want_dir {
            if !metadata.is_dir() {
                report.skipped.push(SkippedRef {
                    token: token.clone(),
                    reason: SkipReason::ReadError("not a directory".into()),
                });
                continue;
            }
            if want_recursive {
                let budget = cfg.max_total_bytes.saturating_sub(total_bytes);
                if budget == 0 {
                    report.skipped.push(SkippedRef {
                        token: token.clone(),
                        reason: SkipReason::TotalSizeExceeded,
                    });
                    continue;
                }
                let (body, stats) = render_dir_recursive(&canonical, cwd, cfg, budget);
                if body.is_empty() && stats.files_inlined == 0 {
                    // Nothing to inline (empty dir, or every file was excluded /
                    // a symlink). Surface this as a no-op skip so the user can
                    // tell the ref was parsed but produced nothing.
                    report.skipped.push(SkippedRef {
                        token: token.clone(),
                        reason: SkipReason::ReadError(format!(
                            "recursive walk found no inlinable files ({} excluded, {} symlinks)",
                            stats.excluded, stats.symlinks_skipped
                        )),
                    });
                    continue;
                }
                let bytes = body.len();
                total_bytes += bytes;
                appended.push_str(&format_recursive_block(token, &canonical, &body, &stats));
                report.resolved.push(ResolvedRef {
                    token: token.clone(),
                    path: canonical,
                    kind: RefKind::DirectoryRecursive,
                    bytes,
                    truncated: stats.stopped_files || stats.stopped_bytes,
                    recursive: Some(stats),
                });
                continue;
            }
            let listing = render_dir_listing(&canonical, cfg.max_file_bytes);
            let bytes = listing.len();
            if total_bytes.saturating_add(bytes) > cfg.max_total_bytes {
                report.skipped.push(SkippedRef {
                    token: token.clone(),
                    reason: SkipReason::TotalSizeExceeded,
                });
                continue;
            }
            total_bytes += bytes;
            appended.push_str(&format_block(
                token,
                &canonical,
                RefKind::Directory,
                &listing,
            ));
            report.resolved.push(ResolvedRef {
                token: token.clone(),
                path: canonical,
                kind: RefKind::Directory,
                bytes,
                truncated: false,
                recursive: None,
            });
        } else if metadata.is_file() {
            let (content, truncated) = read_file_capped(&canonical, cfg.max_file_bytes)?;
            let bytes = content.len();
            if total_bytes.saturating_add(bytes) > cfg.max_total_bytes {
                report.skipped.push(SkippedRef {
                    token: token.clone(),
                    reason: SkipReason::TotalSizeExceeded,
                });
                continue;
            }
            total_bytes += bytes;
            appended.push_str(&format_block(token, &canonical, RefKind::File, &content));
            if truncated {
                appended.push_str("\n(truncated to ");
                appended.push_str(&cfg.max_file_bytes.to_string());
                appended.push_str(" bytes)\n");
            }
            report.resolved.push(ResolvedRef {
                token: token.clone(),
                path: canonical,
                kind: RefKind::File,
                bytes,
                truncated,
                recursive: None,
            });
        }
    }

    // `prompt` stays the original user input; the appended blocks live in
    // `cache_context` so the server can mark them cacheable independently.
    Ok((input.to_string(), appended.trim_end().to_string(), report))
}

/// Find every `@<token>` candidate in the input. We do NOT verify path
/// existence here — that happens in `expand_refs`, which uses the resulting
/// tokens to drive the (path, exclude, size-cap) logic.
fn scan_tokens(input: &str) -> Vec<(String, String)> {
    let mut out = Vec::new();
    let bytes = input.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] != b'@' {
            i += 1;
            continue;
        }
        // Position must be the start of input or preceded by whitespace /
        // start-of-line punctuation. Otherwise it is probably part of a
        // longer identifier (e.g., an email).
        if i > 0 {
            let prev = bytes[i - 1];
            if !matches!(prev, b' ' | b'\t' | b'\n' | b'(' | b'[' | b'"' | b'\'') {
                i += 1;
                continue;
            }
        }
        let start = i + 1;
        let mut j = start;
        while j < bytes.len() && is_path_byte(bytes[j]) {
            j += 1;
        }
        if j == start {
            i += 1;
            continue;
        }
        let raw = &input[start..j];
        // Heuristic: require the candidate to look like a path — contain a
        // `/`, `\`, `:` (drive letter on Windows) or `.`. Plain `@alice`
        // mentions are skipped.
        if raw.contains('/') || raw.contains('\\') || raw.contains(':') || raw.contains('.') {
            let token = format!("@{raw}");
            out.push((token, raw.to_string()));
        }
        i = j;
    }
    out
}

fn is_path_byte(b: u8) -> bool {
    // `\` is the Windows path separator and `:` shows up in drive letters
    // (`C:\Users\...`). Including them keeps `@C:\foo\bar` parseable on
    // Windows. On Unix they cause no false positives — `\` never resolves
    // to a real path so the token is silently dropped at expansion time,
    // and `:` in a Unix filename is rare but legal.
    //
    // `*` is accepted so `@src/**` parses as one token; only the trailing
    // `/**` is honoured (recursive walk). Anything else with `*` will fail
    // to canonicalize and is silently dropped.
    b.is_ascii_alphanumeric()
        || matches!(
            b,
            b'/' | b'\\' | b':' | b'.' | b'-' | b'_' | b'~' | b'+' | b'@' | b'#' | b'*'
        )
}

fn resolve_path(cwd: &Path, raw: &str) -> Option<PathBuf> {
    let p = if raw.starts_with('/') {
        PathBuf::from(raw)
    } else {
        cwd.join(raw)
    };
    std::fs::canonicalize(&p).ok()
}

fn read_file_capped(path: &Path, max: usize) -> Result<(String, bool)> {
    let bytes = std::fs::read(path).map_err(|e| {
        AgoError::Io(std::io::Error::new(
            e.kind(),
            format!("{}: {e}", path.display()),
        ))
    })?;
    let truncated = bytes.len() > max;
    let slice = if truncated { &bytes[..max] } else { &bytes[..] };
    // Replace invalid UTF-8 with U+FFFD so binary files do not blow up.
    Ok((String::from_utf8_lossy(slice).into_owned(), truncated))
}

fn render_dir_listing(path: &Path, _limit: usize) -> String {
    let mut entries = match std::fs::read_dir(path) {
        Ok(it) => it.flatten().collect::<Vec<_>>(),
        Err(e) => return format!("(error reading directory: {e})\n"),
    };
    entries.sort_by_key(|e| e.file_name());
    let mut out = String::new();
    for entry in entries.iter().take(200) {
        let name = entry.file_name().to_string_lossy().into_owned();
        let kind = entry.file_type().ok();
        let suffix = match kind {
            Some(t) if t.is_dir() => "/",
            Some(t) if t.is_symlink() => "@",
            _ => "",
        };
        let size = entry
            .metadata()
            .ok()
            .filter(|m| m.is_file())
            .map(|m| m.len())
            .map(|n| format!("  {n} B"))
            .unwrap_or_default();
        out.push_str(&format!("{name}{suffix}{size}\n"));
    }
    if entries.len() > 200 {
        out.push_str(&format!("... and {} more entries\n", entries.len() - 200));
    }
    out
}

fn format_block(token: &str, path: &Path, kind: RefKind, body: &str) -> String {
    let label = match kind {
        RefKind::File => "file",
        RefKind::Directory => "dir",
        RefKind::DirectoryRecursive => "dir (recursive)",
    };
    let mut s = String::new();
    s.push_str(&format!("\n[{token}] {label}: {}\n", path.display()));
    s.push_str("```\n");
    s.push_str(body);
    if !body.ends_with('\n') {
        s.push('\n');
    }
    s.push_str("```\n");
    s
}

/// Outer wrapper around a `@dir/**` walk. The body already contains
/// per-file labeled code blocks emitted by `render_dir_recursive`; this
/// function only adds a short header so the LLM can see which user token
/// the cluster came from, plus a trailing summary line when the walk
/// stopped early.
fn format_recursive_block(token: &str, root: &Path, body: &str, stats: &RecursiveStats) -> String {
    let mut s = String::new();
    s.push_str(&format!(
        "\n[{token}] dir (recursive): {} — {} files\n",
        root.display(),
        stats.files_inlined
    ));
    s.push_str(body);
    if !body.ends_with('\n') {
        s.push('\n');
    }
    if stats.stopped_files {
        s.push_str(&format!(
            "(stopped at max_dir_files; {} files inlined, more present)\n",
            stats.files_inlined
        ));
    }
    if stats.stopped_bytes {
        s.push_str("(stopped at max_total_bytes; walk truncated)\n");
    }
    s
}

/// Walk `root` depth-first and inline every file as its own labeled block.
///
/// Determinism: each directory's entries are sorted by file name before
/// descending, so the same tree produces the same bytes — important for
/// prompt caching (a stable prefix hits the cache).
///
/// Safety:
///   - symlinks are skipped (never followed) to avoid infinite loops and
///     to prevent `@dir/**` from exfiltrating files outside the dir via a
///     dangling symlink.
///   - `cfg.exclude` is checked against the path relative to `cwd` (or the
///     absolute path as a fallback) — same semantics as the non-recursive
///     `@dir/` listing.
///   - the walk stops as soon as `cfg.max_dir_files` or `bytes_budget` is
///     about to be exceeded; the partial result is returned with the
///     corresponding `stopped_*` flag set.
fn render_dir_recursive(
    root: &Path,
    cwd: &Path,
    cfg: &ContextConfig,
    bytes_budget: usize,
) -> (String, RecursiveStats) {
    let mut out = String::new();
    let mut stats = RecursiveStats::default();
    walk_dir(root, cwd, cfg, bytes_budget, &mut out, &mut stats);
    (out, stats)
}

fn walk_dir(
    dir: &Path,
    cwd: &Path,
    cfg: &ContextConfig,
    bytes_budget: usize,
    out: &mut String,
    stats: &mut RecursiveStats,
) {
    if stats.stopped_files || stats.stopped_bytes {
        return;
    }
    let mut entries = match std::fs::read_dir(dir) {
        Ok(it) => it.flatten().collect::<Vec<_>>(),
        Err(_) => {
            stats.read_errors += 1;
            return;
        }
    };
    entries.sort_by_key(|e| e.file_name());
    for entry in entries {
        if stats.stopped_files || stats.stopped_bytes {
            return;
        }
        let path = entry.path();
        let ft = match entry.file_type() {
            Ok(t) => t,
            Err(_) => {
                stats.read_errors += 1;
                continue;
            }
        };
        if ft.is_symlink() {
            stats.symlinks_skipped += 1;
            continue;
        }
        let match_path = path.strip_prefix(cwd).unwrap_or(&path).to_path_buf();
        if cfg.exclude.is_match(&match_path) {
            stats.excluded += 1;
            continue;
        }
        if ft.is_dir() {
            walk_dir(&path, cwd, cfg, bytes_budget, out, stats);
            continue;
        }
        if !ft.is_file() {
            continue;
        }
        if stats.files_inlined >= cfg.max_dir_files {
            stats.stopped_files = true;
            return;
        }
        let (content, truncated) = match read_file_capped(&path, cfg.max_file_bytes) {
            Ok(c) => c,
            Err(_) => {
                stats.read_errors += 1;
                continue;
            }
        };
        // Use the path relative to cwd as the label so the LLM sees the
        // same shape a user would have typed (`@src/main.rs`). Fall back to
        // the absolute path for files outside cwd.
        let label_token = format!("@{}", match_path.display());
        let mut block = format_block(&label_token, &path, RefKind::File, &content);
        if truncated {
            block.push_str("(truncated to ");
            block.push_str(&cfg.max_file_bytes.to_string());
            block.push_str(" bytes)\n");
        }
        if out.len().saturating_add(block.len()) > bytes_budget {
            stats.stopped_bytes = true;
            return;
        }
        out.push_str(&block);
        stats.files_inlined += 1;
        stats.bytes += block.len();
        if truncated {
            stats.truncated_files += 1;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    fn cfg_default() -> ContextConfig {
        ContextConfig::default()
    }

    #[test]
    fn no_refs_returns_input_unchanged() {
        let dir = tempdir().unwrap();
        let (out, _cache, rep) = expand_refs("hello world", dir.path(), &cfg_default()).unwrap();
        assert_eq!(out, "hello world");
        assert!(rep.resolved.is_empty());
    }

    #[test]
    fn bare_mention_not_treated_as_path() {
        let dir = tempdir().unwrap();
        let (out, _cache, rep) =
            expand_refs("send email to @alice", dir.path(), &cfg_default()).unwrap();
        assert_eq!(out, "send email to @alice");
        assert!(rep.resolved.is_empty());
        assert!(rep.skipped.is_empty());
    }

    #[test]
    fn file_ref_inlines_content() {
        let dir = tempdir().unwrap();
        fs::write(dir.path().join("hello.txt"), "world").unwrap();
        let (out, cache, rep) =
            expand_refs("read @hello.txt please", dir.path(), &cfg_default()).unwrap();
        // Prompt stays unchanged.
        assert_eq!(out, "read @hello.txt please");
        // Inlined content lives in cache_context (opt-in caching).
        assert!(cache.contains("[@hello.txt]"));
        assert!(cache.contains("world"));
        assert_eq!(rep.resolved.len(), 1);
        assert!(matches!(rep.resolved[0].kind, RefKind::File));
    }

    #[test]
    fn missing_file_is_reported() {
        let dir = tempdir().unwrap();
        let (out, _cache, rep) =
            expand_refs("show @does/not.exist", dir.path(), &cfg_default()).unwrap();
        assert_eq!(out, "show @does/not.exist");
        assert_eq!(rep.skipped.len(), 1);
        assert!(matches!(rep.skipped[0].reason, SkipReason::NotFound));
    }

    #[test]
    fn directory_ref_lists_entries() {
        let dir = tempdir().unwrap();
        fs::write(dir.path().join("a.txt"), "1").unwrap();
        fs::write(dir.path().join("b.txt"), "2").unwrap();
        fs::create_dir(dir.path().join("sub")).unwrap();
        let target = format!("@{}/", dir.path().display());
        let prompt = format!("look at {target}");
        let (_out, cache, rep) = expand_refs(&prompt, dir.path(), &cfg_default()).unwrap();
        assert!(cache.contains("a.txt"));
        assert!(cache.contains("b.txt"));
        assert!(cache.contains("sub/"));
        assert_eq!(rep.resolved.len(), 1);
        assert!(matches!(rep.resolved[0].kind, RefKind::Directory));
    }

    #[test]
    fn exclude_pattern_skips_dotenv() {
        let dir = tempdir().unwrap();
        fs::write(dir.path().join(".env"), "SECRET=1").unwrap();
        let (_out, cache, rep) = expand_refs("look at @.env", dir.path(), &cfg_default()).unwrap();
        assert!(!cache.contains("SECRET=1"));
        assert_eq!(rep.skipped.len(), 1);
        assert!(matches!(rep.skipped[0].reason, SkipReason::Excluded));
    }

    #[test]
    fn file_too_big_is_truncated() {
        let dir = tempdir().unwrap();
        let payload = "x".repeat(10_000);
        fs::write(dir.path().join("big.txt"), &payload).unwrap();
        let cfg = ContextConfig {
            max_file_bytes: 100,
            ..ContextConfig::default()
        };
        let (_out, cache, rep) = expand_refs("see @big.txt", dir.path(), &cfg).unwrap();
        assert!(cache.contains("(truncated to 100 bytes)"));
        assert_eq!(rep.resolved.len(), 1);
        assert!(rep.resolved[0].truncated);
    }

    #[test]
    fn total_size_cap_skips_overflow() {
        let dir = tempdir().unwrap();
        for i in 0..5 {
            fs::write(dir.path().join(format!("f{i}.txt")), "x".repeat(800)).unwrap();
        }
        let cfg = ContextConfig {
            max_total_bytes: 2000,
            max_file_bytes: 1000,
            ..ContextConfig::default()
        };
        let (_, _cache, rep) = expand_refs(
            "see @f0.txt @f1.txt @f2.txt @f3.txt @f4.txt",
            dir.path(),
            &cfg,
        )
        .unwrap();
        assert!(rep.resolved.len() <= 2);
        assert!(rep
            .skipped
            .iter()
            .any(|s| matches!(s.reason, SkipReason::TotalSizeExceeded)));
    }

    #[test]
    fn max_refs_cap_enforced() {
        let dir = tempdir().unwrap();
        let mut prompt = String::new();
        for i in 0..20 {
            fs::write(dir.path().join(format!("f{i}.txt")), "1").unwrap();
            prompt.push_str(&format!("@f{i}.txt "));
        }
        let cfg = ContextConfig {
            max_refs: 3,
            ..ContextConfig::default()
        };
        let (_, _cache, rep) = expand_refs(&prompt, dir.path(), &cfg).unwrap();
        assert_eq!(rep.resolved.len(), 3);
        assert!(rep
            .skipped
            .iter()
            .any(|s| matches!(s.reason, SkipReason::TooManyRefs)));
    }

    #[test]
    fn multiple_refs_appended_in_order() {
        let dir = tempdir().unwrap();
        fs::write(dir.path().join("a.txt"), "AAA").unwrap();
        fs::write(dir.path().join("b.txt"), "BBB").unwrap();
        let (_out, cache, rep) =
            expand_refs("compare @a.txt and @b.txt", dir.path(), &cfg_default()).unwrap();
        let a = cache.find("AAA").unwrap();
        let b = cache.find("BBB").unwrap();
        assert!(a < b);
        assert_eq!(rep.resolved.len(), 2);
    }

    #[test]
    fn email_address_not_split_into_ref() {
        let dir = tempdir().unwrap();
        let (out, cache, rep) =
            expand_refs("ping alice@example.com please", dir.path(), &cfg_default()).unwrap();
        assert_eq!(out, "ping alice@example.com please");
        assert!(cache.is_empty());
        assert!(rep.resolved.is_empty());
    }

    // ---- Recursive `@dir/**` expansion -------------------------------------

    #[test]
    fn recursive_inlines_every_file_in_tree() {
        let dir = tempdir().unwrap();
        fs::write(dir.path().join("a.txt"), "AAA").unwrap();
        fs::create_dir(dir.path().join("sub")).unwrap();
        fs::write(dir.path().join("sub").join("b.txt"), "BBB").unwrap();
        fs::create_dir(dir.path().join("sub").join("deep")).unwrap();
        fs::write(dir.path().join("sub").join("deep").join("c.txt"), "CCC").unwrap();

        let target = format!("@{}/**", dir.path().display());
        let prompt = format!("review {target}");
        let (_out, cache, rep) = expand_refs(&prompt, dir.path(), &cfg_default()).unwrap();

        assert_eq!(rep.resolved.len(), 1, "exactly one user-typed ref");
        assert_eq!(rep.resolved[0].kind, RefKind::DirectoryRecursive);
        let stats = rep.resolved[0].recursive.as_ref().expect("stats present");
        assert_eq!(stats.files_inlined, 3);
        // All three file contents must show up. Order is deterministic
        // (alphabetic within each dir, depth-first), so AAA precedes BBB
        // precedes CCC.
        let pa = cache.find("AAA").expect("a.txt content");
        let pb = cache.find("BBB").expect("b.txt content");
        let pc = cache.find("CCC").expect("c.txt content");
        assert!(pa < pb && pb < pc, "depth-first alphabetical order");
    }

    #[test]
    fn recursive_respects_exclude_glob() {
        let dir = tempdir().unwrap();
        fs::write(dir.path().join("keep.txt"), "KEEP").unwrap();
        fs::create_dir(dir.path().join("nested")).unwrap();
        // .env inside a subdir must still be excluded by the default deny-list.
        fs::write(dir.path().join("nested").join(".env"), "SECRET=1").unwrap();
        fs::write(dir.path().join("nested").join("ok.txt"), "OK").unwrap();

        let target = format!("@{}/**", dir.path().display());
        let (_out, cache, rep) = expand_refs(&target, dir.path(), &cfg_default()).unwrap();

        assert!(cache.contains("KEEP"));
        assert!(cache.contains("OK"));
        assert!(!cache.contains("SECRET=1"), ".env must not leak");
        let stats = rep.resolved[0].recursive.as_ref().unwrap();
        assert_eq!(stats.excluded, 1);
        assert_eq!(stats.files_inlined, 2);
    }

    #[test]
    fn recursive_stops_at_max_dir_files() {
        let dir = tempdir().unwrap();
        for i in 0..10 {
            fs::write(dir.path().join(format!("f{i}.txt")), "x").unwrap();
        }
        let cfg = ContextConfig {
            max_dir_files: 3,
            ..ContextConfig::default()
        };
        let target = format!("@{}/**", dir.path().display());
        let (_out, _cache, rep) = expand_refs(&target, dir.path(), &cfg).unwrap();

        let stats = rep.resolved[0].recursive.as_ref().unwrap();
        assert_eq!(stats.files_inlined, 3);
        assert!(stats.stopped_files);
        assert!(rep.resolved[0].truncated);
    }

    #[test]
    fn recursive_stops_at_max_total_bytes() {
        let dir = tempdir().unwrap();
        for i in 0..6 {
            fs::write(dir.path().join(format!("f{i}.txt")), "x".repeat(800)).unwrap();
        }
        let cfg = ContextConfig {
            max_file_bytes: 1000,
            max_total_bytes: 2000,
            ..ContextConfig::default()
        };
        let target = format!("@{}/**", dir.path().display());
        let (_out, _cache, rep) = expand_refs(&target, dir.path(), &cfg).unwrap();

        let stats = rep.resolved[0].recursive.as_ref().unwrap();
        assert!(stats.stopped_bytes, "must stop on byte budget");
        // Each block is ~800B + framing; we should get at most ~2 before the cap.
        assert!(stats.files_inlined <= 3);
    }

    #[test]
    fn recursive_and_listing_coexist_in_one_prompt() {
        let dir = tempdir().unwrap();
        fs::write(dir.path().join("a.txt"), "AAA").unwrap();
        let other = dir.path().join("other");
        fs::create_dir(&other).unwrap();
        fs::write(other.join("b.txt"), "BBB").unwrap();

        let listing = format!("@{}/", dir.path().display());
        let recursive = format!("@{}/**", other.display());
        let prompt = format!("see {listing} and {recursive}");
        let (_out, cache, rep) = expand_refs(&prompt, dir.path(), &cfg_default()).unwrap();

        assert_eq!(rep.resolved.len(), 2);
        let kinds: Vec<_> = rep.resolved.iter().map(|r| r.kind).collect();
        assert!(kinds.contains(&RefKind::Directory));
        assert!(kinds.contains(&RefKind::DirectoryRecursive));
        // Listing shows the file name, recursive shows the file content.
        assert!(cache.contains("a.txt"));
        assert!(cache.contains("BBB"));
    }

    #[test]
    fn recursive_skips_symlink_targets() {
        let dir = tempdir().unwrap();
        fs::write(dir.path().join("real.txt"), "REAL").unwrap();
        // Symlink to a file that, if followed, would be inlined twice.
        // is_symlink on the entry must short-circuit before any read happens.
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(dir.path().join("real.txt"), dir.path().join("link.txt"))
                .unwrap();
        }
        #[cfg(windows)]
        {
            // Windows symlink creation requires admin; skip the link side of
            // the assertion but still exercise the walker.
        }
        let target = format!("@{}/**", dir.path().display());
        let (_out, _cache, rep) = expand_refs(&target, dir.path(), &cfg_default()).unwrap();
        let stats = rep.resolved[0].recursive.as_ref().unwrap();
        assert_eq!(stats.files_inlined, 1, "real file inlined once");
        #[cfg(unix)]
        assert_eq!(stats.symlinks_skipped, 1);
    }

    #[test]
    fn recursive_empty_dir_reports_skip() {
        let dir = tempdir().unwrap();
        let empty = dir.path().join("empty");
        fs::create_dir(&empty).unwrap();
        let target = format!("@{}/**", empty.display());
        let (_out, cache, rep) = expand_refs(&target, dir.path(), &cfg_default()).unwrap();
        assert!(rep.resolved.is_empty());
        assert_eq!(rep.skipped.len(), 1);
        assert!(cache.is_empty());
    }

    #[test]
    fn recursive_token_parses_with_trailing_star_star() {
        // Regression: is_path_byte must accept `*` so `@src/**` is one token.
        let tokens = scan_tokens("review @src/** carefully");
        assert_eq!(tokens.len(), 1);
        assert_eq!(tokens[0].0, "@src/**");
        assert_eq!(tokens[0].1, "src/**");
    }

    #[test]
    fn recursive_walk_is_deterministic_across_runs() {
        // The same tree must produce identical bytes — caching depends on it.
        let dir = tempdir().unwrap();
        fs::create_dir(dir.path().join("z")).unwrap();
        fs::write(dir.path().join("z").join("c.txt"), "C").unwrap();
        fs::write(dir.path().join("a.txt"), "A").unwrap();
        fs::write(dir.path().join("b.txt"), "B").unwrap();
        let target = format!("@{}/**", dir.path().display());
        let (_, c1, _) = expand_refs(&target, dir.path(), &cfg_default()).unwrap();
        let (_, c2, _) = expand_refs(&target, dir.path(), &cfg_default()).unwrap();
        assert_eq!(c1, c2);
    }
}
