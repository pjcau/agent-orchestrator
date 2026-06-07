//! Shell-command allowlist for the native agent-host client.
//!
//! Mirrors `src/agent_orchestrator/agent_host/shell_allowlist.py`. The
//! Python version is documented there; this Rust port preserves the
//! same on-disk format so a user who switches between the Python and
//! the Rust client never has to re-approve binaries:
//!
//! ```json
//! {"allowed": ["pytest", "git", "npm"]}
//! ```
//!
//! Path: `${XDG_CACHE_HOME:-~/.cache}/ago/shell-allow.json`.
//!
//! Threats mitigated:
//!
//! * Command injection via `argv[0]` — the registry is keyed by
//!   basename only, refusing path traversal (`..` / `/` / `\\`) in the
//!   binary name. An attacker cannot bypass the allowlist by aliasing
//!   `/tmp/evil/pytest` to the already-allowed `pytest`.
//! * Race on the allow file — single atomic write via temp + rename so
//!   a killed process never leaves corrupt JSON.
//! * Persisting blanket allow for `sh`/`bash`/etc. is flagged via
//!   [`is_high_risk`] so the CLI front-end can mark the confirmation
//!   prompt with an extra warning.

use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum AllowlistError {
    #[error("argv must not be empty")]
    EmptyArgv,
    #[error("argv[0] must not be empty")]
    EmptyBinary,
    #[error("argv[0] must be a bare binary name, not a path: {0:?}")]
    PathInBinary(String),
    #[error("failed to read allowlist {path}: {source}")]
    Read {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("failed to write allowlist {path}: {source}")]
    Write {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("failed to serialise allowlist: {0}")]
    Serde(#[from] serde_json::Error),
}

/// Shells that effectively bypass the allowlist (they can run anything).
/// Not forbidden — sometimes the user really wants to allow `bash` —
/// but [`is_high_risk`] surfaces them so the CLI front-end can warn
/// the user the first time.
const HIGH_RISK: &[&str] = &[
    "sh", "bash", "zsh", "fish", "dash", "ksh", "csh", "tcsh", "ash",
];

pub fn is_high_risk(binary: &str) -> bool {
    let base = Path::new(binary)
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or(binary);
    HIGH_RISK.contains(&base)
}

#[derive(Debug, Serialize, Deserialize, Default)]
struct OnDisk {
    /// Sorted via `BTreeSet` so the JSON output is stable across
    /// rewrites — helpful for `git diff` if the user version-controls
    /// the allowlist.
    allowed: BTreeSet<String>,
}

/// Default path: `${XDG_CACHE_HOME:-~/.cache}/ago/shell-allow.json`.
///
/// Mirrors the Python helper so both clients converge on the same file.
pub fn default_allowlist_path() -> PathBuf {
    let base = std::env::var_os("XDG_CACHE_HOME")
        .map(PathBuf::from)
        .or_else(|| dirs_home_dir().map(|h| h.join(".cache")))
        .unwrap_or_else(|| PathBuf::from("."));
    base.join("ago").join("shell-allow.json")
}

fn dirs_home_dir() -> Option<PathBuf> {
    // Avoid pulling another dirs crate variant: the `directories`
    // crate already on the dep list exposes a home dir via UserDirs.
    directories::UserDirs::new().map(|u| u.home_dir().to_path_buf())
}

/// Persistent set of permitted `argv[0]` basenames.
///
/// Lazily loads on first use; writes atomically on every mutation.
/// Thread safety is not provided — the agent-host client runs all
/// decisions on a single tokio runtime.
pub struct ShellAllowlist {
    path: PathBuf,
    loaded: Option<BTreeSet<String>>,
}

impl ShellAllowlist {
    pub fn new(path: PathBuf) -> Self {
        Self { path, loaded: None }
    }

    pub fn at_default() -> Self {
        Self::new(default_allowlist_path())
    }

    fn ensure_loaded(&mut self) -> Result<(), AllowlistError> {
        if self.loaded.is_some() {
            return Ok(());
        }
        if !self.path.exists() {
            self.loaded = Some(BTreeSet::new());
            return Ok(());
        }
        let raw = fs::read_to_string(&self.path).map_err(|source| AllowlistError::Read {
            path: self.path.clone(),
            source,
        })?;
        // Tolerate a corrupt file — start empty rather than panic. The
        // operator can inspect the broken file on disk; a malformed
        // allowlist must NOT erode security by suddenly allowing
        // arbitrary binaries, hence empty start.
        let parsed: OnDisk = serde_json::from_str(&raw).unwrap_or_default();
        self.loaded = Some(parsed.allowed);
        Ok(())
    }

    fn save(&self) -> Result<(), AllowlistError> {
        let allowed = self.loaded.as_ref().cloned().unwrap_or_default();
        let payload = OnDisk { allowed };
        let serialised = serde_json::to_string_pretty(&payload)?;

        let parent = self.path.parent().unwrap_or(Path::new("."));
        fs::create_dir_all(parent).map_err(|source| AllowlistError::Write {
            path: parent.to_path_buf(),
            source,
        })?;

        // Atomic write: temp file in the same dir, then rename.
        // tempfile::NamedTempFile guarantees both: same FS (so rename
        // is atomic) and cleanup on early failure (so a killed process
        // between write and rename never leaves a stale `.tmp`).
        let mut tmp = tempfile::Builder::new()
            .prefix(".shell-allow-")
            .suffix(".json.tmp")
            .tempfile_in(parent)
            .map_err(|source| AllowlistError::Write {
                path: parent.to_path_buf(),
                source,
            })?;
        tmp.write_all(serialised.as_bytes())
            .and_then(|_| tmp.flush())
            .map_err(|source| AllowlistError::Write {
                path: parent.to_path_buf(),
                source,
            })?;
        tmp.persist(&self.path).map_err(|e| AllowlistError::Write {
            path: self.path.clone(),
            source: e.error,
        })?;
        Ok(())
    }

    /// True if `argv[0]` basename is already permitted.
    pub fn contains(&mut self, argv: &[String]) -> Result<bool, AllowlistError> {
        let bin = basename_or_raise(argv)?;
        self.ensure_loaded()?;
        Ok(self
            .loaded
            .as_ref()
            .map(|s| s.contains(&bin))
            .unwrap_or(false))
    }

    /// Persist `argv[0]` basename as allowed for all future calls.
    pub fn allow(&mut self, argv: &[String]) -> Result<(), AllowlistError> {
        let bin = basename_or_raise(argv)?;
        self.ensure_loaded()?;
        if let Some(set) = self.loaded.as_mut() {
            set.insert(bin);
        }
        self.save()
    }

    pub fn revoke(&mut self, binary: &str) -> Result<bool, AllowlistError> {
        self.ensure_loaded()?;
        let removed = self
            .loaded
            .as_mut()
            .map(|s| s.remove(binary))
            .unwrap_or(false);
        if removed {
            self.save()?;
        }
        Ok(removed)
    }

    pub fn snapshot(&mut self) -> Result<Vec<String>, AllowlistError> {
        self.ensure_loaded()?;
        Ok(self
            .loaded
            .as_ref()
            .map(|s| s.iter().cloned().collect())
            .unwrap_or_default())
    }
}

/// Validate `argv` and return the canonical lookup key.
///
/// Path separators in `argv[0]` are rejected so an attacker cannot
/// bypass the allowlist by aliasing a malicious binary under the same
/// basename as a permitted one (`/tmp/evil/pytest` vs `pytest`).
fn basename_or_raise(argv: &[String]) -> Result<String, AllowlistError> {
    let bin0 = argv.first().ok_or(AllowlistError::EmptyArgv)?;
    if bin0.is_empty() {
        return Err(AllowlistError::EmptyBinary);
    }
    if bin0.contains('/') || bin0.contains('\\') {
        return Err(AllowlistError::PathInBinary(bin0.clone()));
    }
    Ok(bin0.clone())
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_path() -> (tempfile::TempDir, PathBuf) {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("shell-allow.json");
        (dir, p)
    }

    #[test]
    fn empty_argv_rejected() {
        let (_d, p) = temp_path();
        let mut a = ShellAllowlist::new(p);
        assert!(matches!(a.contains(&[]), Err(AllowlistError::EmptyArgv)));
        assert!(matches!(
            a.contains(&["".into()]),
            Err(AllowlistError::EmptyBinary)
        ));
    }

    #[test]
    fn path_in_argv0_rejected() {
        let (_d, p) = temp_path();
        let mut a = ShellAllowlist::new(p);
        assert!(matches!(
            a.contains(&["/usr/bin/pytest".into()]),
            Err(AllowlistError::PathInBinary(_))
        ));
        assert!(matches!(
            a.allow(&["sub\\bin".into()]),
            Err(AllowlistError::PathInBinary(_))
        ));
    }

    #[test]
    fn allow_persists_atomically() {
        let (_d, p) = temp_path();
        {
            let mut a = ShellAllowlist::new(p.clone());
            a.allow(&["pytest".into(), "-q".into()]).unwrap();
        }
        let mut b = ShellAllowlist::new(p);
        assert!(b.contains(&["pytest".into(), "-q".into()]).unwrap());
        assert_eq!(b.snapshot().unwrap(), vec!["pytest".to_string()]);
    }

    #[test]
    fn revoke_idempotent() {
        let (_d, p) = temp_path();
        let mut a = ShellAllowlist::new(p);
        a.allow(&["pytest".into()]).unwrap();
        assert!(a.revoke("pytest").unwrap());
        assert!(!a.contains(&["pytest".into()]).unwrap());
        // Idempotent: revoking absent returns false.
        assert!(!a.revoke("pytest").unwrap());
    }

    #[test]
    fn high_risk_detection() {
        for bad in ["bash", "sh", "zsh", "dash"] {
            assert!(is_high_risk(bad));
        }
        for good in ["pytest", "git", "npm", "python3"] {
            assert!(!is_high_risk(good));
        }
    }

    #[test]
    fn corrupted_file_starts_empty() {
        let (_d, p) = temp_path();
        fs::write(&p, b"{not-json").unwrap();
        let mut a = ShellAllowlist::new(p);
        assert!(a.snapshot().unwrap().is_empty());
    }

    #[test]
    fn snapshot_sorted() {
        let (_d, p) = temp_path();
        let mut a = ShellAllowlist::new(p);
        a.allow(&["pytest".into()]).unwrap();
        a.allow(&["git".into()]).unwrap();
        a.allow(&["npm".into()]).unwrap();
        // BTreeSet → alphabetical.
        assert_eq!(
            a.snapshot().unwrap(),
            vec!["git".to_string(), "npm".to_string(), "pytest".to_string()]
        );
    }
}
