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
    pub kind: RefKind, // File | Directory
    pub bytes: usize,  // bytes inlined (post-truncation)
    pub truncated: bool,
}

#[derive(Debug, Clone, Copy)]
pub enum RefKind {
    File,
    Directory,
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
/// Tokens are recognised as: `@` followed by characters that look like a
/// path — either starting with `/` or `.` or containing at least one `/`
/// or `.` AND resolving to an existing file/dir. Bare `@alice` mentions
/// are left untouched.
pub fn expand_refs(input: &str, cwd: &Path, cfg: &ContextConfig) -> Result<(String, ExpandReport)> {
    let mut report = ExpandReport::default();
    let tokens = scan_tokens(input);
    if tokens.is_empty() {
        return Ok((input.to_string(), report));
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
        let want_dir = raw_path.ends_with('/');
        let path_str = raw_path.trim_end_matches('/');
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
            });
        }
    }

    let mut out = String::with_capacity(input.len() + appended.len());
    out.push_str(input);
    if !appended.is_empty() {
        out.push_str("\n\n---\n");
        out.push_str(appended.trim_end());
        out.push('\n');
    }
    Ok((out, report))
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
    b.is_ascii_alphanumeric()
        || matches!(
            b,
            b'/' | b'\\' | b':' | b'.' | b'-' | b'_' | b'~' | b'+' | b'@' | b'#'
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
        let (out, rep) = expand_refs("hello world", dir.path(), &cfg_default()).unwrap();
        assert_eq!(out, "hello world");
        assert!(rep.resolved.is_empty());
    }

    #[test]
    fn bare_mention_not_treated_as_path() {
        let dir = tempdir().unwrap();
        let (out, rep) = expand_refs("send email to @alice", dir.path(), &cfg_default()).unwrap();
        assert_eq!(out, "send email to @alice");
        assert!(rep.resolved.is_empty());
        assert!(rep.skipped.is_empty());
    }

    #[test]
    fn file_ref_inlines_content() {
        let dir = tempdir().unwrap();
        fs::write(dir.path().join("hello.txt"), "world").unwrap();
        let (out, rep) = expand_refs("read @hello.txt please", dir.path(), &cfg_default()).unwrap();
        assert!(out.contains("read @hello.txt please"));
        assert!(out.contains("[@hello.txt]"));
        assert!(out.contains("world"));
        assert_eq!(rep.resolved.len(), 1);
        assert!(matches!(rep.resolved[0].kind, RefKind::File));
    }

    #[test]
    fn missing_file_is_reported() {
        let dir = tempdir().unwrap();
        let (out, rep) = expand_refs("show @does/not.exist", dir.path(), &cfg_default()).unwrap();
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
        let (out, rep) = expand_refs(&prompt, dir.path(), &cfg_default()).unwrap();
        assert!(out.contains("a.txt"));
        assert!(out.contains("b.txt"));
        assert!(out.contains("sub/"));
        assert_eq!(rep.resolved.len(), 1);
        assert!(matches!(rep.resolved[0].kind, RefKind::Directory));
    }

    #[test]
    fn exclude_pattern_skips_dotenv() {
        let dir = tempdir().unwrap();
        fs::write(dir.path().join(".env"), "SECRET=1").unwrap();
        let (out, rep) = expand_refs("look at @.env", dir.path(), &cfg_default()).unwrap();
        assert!(!out.contains("SECRET=1"));
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
        let (out, rep) = expand_refs("see @big.txt", dir.path(), &cfg).unwrap();
        assert!(out.contains("(truncated to 100 bytes)"));
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
        let (_, rep) = expand_refs(
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
        let (_, rep) = expand_refs(&prompt, dir.path(), &cfg).unwrap();
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
        let (out, rep) =
            expand_refs("compare @a.txt and @b.txt", dir.path(), &cfg_default()).unwrap();
        let a = out.find("AAA").unwrap();
        let b = out.find("BBB").unwrap();
        assert!(a < b);
        assert_eq!(rep.resolved.len(), 2);
    }

    #[test]
    fn email_address_not_split_into_ref() {
        let dir = tempdir().unwrap();
        let (out, rep) =
            expand_refs("ping alice@example.com please", dir.path(), &cfg_default()).unwrap();
        assert_eq!(out, "ping alice@example.com please");
        assert!(rep.resolved.is_empty());
    }
}
