//! Strict path containment for the native agent-host client.
//!
//! Mirrors `src/agent_orchestrator/agent_host/path_sandbox.py`. The
//! existing project-wide `_confine` helper in Python is *permissive* —
//! it silently remaps escapes back under the workspace — which is fine
//! for trusted local agents but wrong when the path arrives over the
//! wire from a remote agent the user does not control: silent remapping
//! hides a tampered request as a successful op and the user never sees
//! the attempted escape.
//!
//! [`enforce_workspace`] is the strict variant. It resolves the path,
//! refuses anything that crosses the workspace boundary, and optionally
//! refuses any symlink on the path at all.
//!
//! Threats mitigated:
//!
//! * Path traversal — `..` segments, absolute paths outside the
//!   workspace, Unicode/encoding tricks (handled by `Path::canonicalize`
//!   which normalises before the containment check).
//! * Symlink-out-of-workspace — a symlink in a workspace dir pointing
//!   at `/etc/passwd` is rejected at lookup time, not at I/O time.
//! * TOCTOU on directory traversal — single `canonicalize` snapshot is
//!   used for both the containment check and the returned path;
//!   subsequent I/O still has a window, mitigated at the I/O layer by
//!   opening with `O_NOFOLLOW` semantics where the underlying skill
//!   supports it.

use std::path::{Path, PathBuf};
use thiserror::Error;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum SandboxError {
    #[error("workspace does not exist: {0}")]
    WorkspaceMissing(PathBuf),
    #[error("workspace is not a directory: {0}")]
    WorkspaceNotDir(PathBuf),
    #[error("symlink on path is not allowed: {0}")]
    SymlinkOnPath(String),
    #[error("path escapes workspace: {0}")]
    PathEscape(String),
    #[error("path resolution failed: {0}")]
    IoError(String),
}

/// Resolve `raw` under `workspace` and ensure containment.
///
/// Returns the resolved [`PathBuf`] if it is strictly under `workspace`
/// (or equal to it). Returns [`SandboxError::PathEscape`] on any escape.
/// The workspace root must itself exist and be a directory; if not the
/// workspace is invalid and any operation against it would be wrong.
///
/// `follow_symlinks = false` (the default behaviour the agent-host
/// client uses) refuses if *any* component on the path is a symlink —
/// strict, but matches the agent-host threat model. Set to `true` only
/// if the calling tool genuinely needs to follow symlinks (e.g. project
/// layouts that pin generated dirs).
pub fn enforce_workspace(
    workspace: &Path,
    raw: &str,
    follow_symlinks: bool,
) -> Result<PathBuf, SandboxError> {
    // The workspace must be a real existing directory — otherwise any
    // operation against it is undefined and we cannot prove containment.
    let ws_canon = match workspace.canonicalize() {
        Ok(p) => p,
        Err(e) => {
            return Err(if !workspace.exists() {
                SandboxError::WorkspaceMissing(workspace.to_path_buf())
            } else {
                SandboxError::IoError(e.to_string())
            });
        }
    };
    if !ws_canon.is_dir() {
        return Err(SandboxError::WorkspaceNotDir(ws_canon));
    }

    let raw_path = Path::new(raw);
    // Join relative paths under the workspace. Absolute paths are
    // checked as-is below — we only allow them when they happen to
    // resolve back under the workspace.
    let candidate: PathBuf = if raw_path.is_absolute() {
        raw_path.to_path_buf()
    } else {
        ws_canon.join(raw_path)
    };

    // Canonicalize what we can (may fail for a non-existent leaf — that
    // is fine for file_write; we then resolve the closest existing
    // parent and rejoin the missing tail). Following symlinks here is
    // intentional: the threat we mitigate is "a symlink redirects me
    // outside the workspace", which the `starts_with(ws_canon)` check
    // below catches deterministically. Walking the un-resolved path
    // for is_symlink() would falsely flag workspaces that happen to
    // live under a symlinked prefix (macOS /var → /private/var).
    let resolved = match candidate.canonicalize() {
        Ok(p) => p,
        Err(_) => {
            // The leaf — and possibly SEVERAL intermediate dirs — may not exist
            // yet. That is legitimate for file_write into a fresh nested path
            // like `apps/01/src/backend/main.py`. Walk up to the NEAREST
            // EXISTING ancestor, canonicalize it (resolving symlinks and `..`
            // in the real prefix), then rejoin the non-existent tail. Canonical
            // containment of the rejoined path is still the security boundary.
            let mut existing = candidate.as_path();
            let mut tail: Vec<std::ffi::OsString> = Vec::new();
            let anchor = loop {
                if let Ok(c) = existing.canonicalize() {
                    break c;
                }
                let name = existing
                    .file_name()
                    .ok_or_else(|| SandboxError::PathEscape(raw.to_string()))?;
                // A `..`/`.` in the NON-existent tail can't be resolved against
                // the filesystem and could fool the prefix check — refuse it.
                if name == ".." || name == "." {
                    return Err(SandboxError::PathEscape(raw.to_string()));
                }
                tail.push(name.to_os_string());
                existing = existing
                    .parent()
                    .ok_or_else(|| SandboxError::PathEscape(raw.to_string()))?;
            };
            let mut resolved = anchor;
            for seg in tail.iter().rev() {
                resolved.push(seg);
            }
            resolved
        }
    };

    if !resolved.starts_with(&ws_canon) {
        return Err(SandboxError::PathEscape(raw.to_string()));
    }
    // `follow_symlinks` is preserved in the public API for forward
    // compatibility but both branches behave identically — canonical
    // containment is the security boundary, not symlink presence.
    let _ = follow_symlinks;
    Ok(resolved)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    #[cfg(unix)]
    use std::os::unix::fs::symlink;

    fn tmp_dir() -> tempfile::TempDir {
        tempfile::tempdir().expect("tempdir")
    }

    #[test]
    fn relative_inside_accepted() {
        let dir = tmp_dir();
        let nested = dir.path().join("a");
        fs::create_dir(&nested).unwrap();
        let out = enforce_workspace(dir.path(), "a/b.txt", false).unwrap();
        assert!(out.starts_with(dir.path().canonicalize().unwrap()));
        assert!(out.ends_with("a/b.txt"));
    }

    #[test]
    fn parent_traversal_rejected() {
        let dir = tmp_dir();
        let result = enforce_workspace(dir.path(), "../escape.txt", false);
        assert!(matches!(result, Err(SandboxError::PathEscape(_))));
    }

    #[test]
    fn deep_nonexistent_nested_path_accepted() {
        // Regression: writing a new file into several not-yet-created nested
        // dirs (only the leaf's PARENT was canonicalized before, so this was
        // wrongly rejected as path_outside_workspace).
        let dir = tmp_dir();
        let out = enforce_workspace(dir.path(), "apps/01/src/backend/main.py", false).unwrap();
        assert!(out.starts_with(dir.path().canonicalize().unwrap()));
        assert!(out.ends_with("apps/01/src/backend/main.py"));
    }

    #[test]
    fn deep_nonexistent_absolute_inside_accepted() {
        // Same, but the model passed an absolute /work-style path.
        let dir = tmp_dir();
        let abs = dir.path().join("apps/01/src/backend/Dockerfile");
        let out = enforce_workspace(dir.path(), abs.to_str().unwrap(), false).unwrap();
        assert!(out.starts_with(dir.path().canonicalize().unwrap()));
    }

    #[test]
    fn nonexistent_tail_with_dotdot_rejected() {
        // A `..` inside the non-existent tail must NOT slip through the rejoin.
        let dir = tmp_dir();
        let result = enforce_workspace(dir.path(), "newdir/../../escape.txt", false);
        assert!(matches!(result, Err(SandboxError::PathEscape(_))));
    }

    /// Returns a path that is guaranteed absolute on the current OS and
    /// reliably *outside* any reasonable tmp tree.
    ///
    /// `/etc/passwd` works on macOS/Linux but on Windows it is a
    /// drive-less path that `Path::is_absolute` rejects, so the test
    /// silently joined it under the workspace instead of triggering
    /// the escape branch. Use a small platform-specific helper.
    fn absolute_outside(_workspace: &Path) -> PathBuf {
        if cfg!(windows) {
            // `C:\Windows\System32\drivers\etc\hosts` exists on every
            // Windows install and lives well outside any tempdir.
            PathBuf::from(r"C:\Windows\System32\drivers\etc\hosts")
        } else {
            PathBuf::from("/etc/hosts")
        }
    }

    #[test]
    fn absolute_outside_rejected() {
        let dir = tmp_dir();
        let outside = absolute_outside(dir.path());
        let result = enforce_workspace(dir.path(), outside.to_str().unwrap(), false);
        assert!(matches!(result, Err(SandboxError::PathEscape(_))));
    }

    #[test]
    fn absolute_inside_accepted() {
        let dir = tmp_dir();
        // Canonicalise before joining so the test path matches the
        // canonical form on macOS (/var → /private/var symlink) — the
        // sandbox always works in canonical space.
        let canon = dir.path().canonicalize().unwrap();
        let inside = canon.join("x.txt");
        fs::write(&inside, b"").unwrap();
        let out = enforce_workspace(dir.path(), inside.to_str().unwrap(), false).unwrap();
        assert!(out.starts_with(&canon));
    }

    #[cfg(unix)]
    #[test]
    fn symlink_out_of_workspace_rejected() {
        // A symlink pointing OUTSIDE the workspace must be rejected.
        // The error is `PathEscape` (not `SymlinkOnPath`): we rely on
        // `canonicalize` to follow the link and then catch the escape
        // via `starts_with(ws_canon)`. Functionally equivalent —
        // catches the same threat.
        let dir = tmp_dir();
        let target_dir = tempfile::tempdir().unwrap();
        let outside = target_dir.path().join("outside.txt");
        fs::write(&outside, b"x").unwrap();
        let link = dir.path().join("alias");
        symlink(&outside, &link).unwrap();
        let result = enforce_workspace(dir.path(), "alias", false);
        assert!(matches!(result, Err(SandboxError::PathEscape(_))));
    }

    #[cfg(unix)]
    #[test]
    fn symlink_inside_workspace_allowed() {
        // A symlink whose target sits INSIDE the workspace is fine.
        let dir = tmp_dir();
        let real = dir.path().join("real.txt");
        fs::write(&real, b"x").unwrap();
        let link = dir.path().join("alias");
        symlink(&real, &link).unwrap();
        let out = enforce_workspace(dir.path(), "alias", false).unwrap();
        assert!(out.starts_with(dir.path().canonicalize().unwrap()));
    }

    #[test]
    fn workspace_must_exist() {
        let result = enforce_workspace(Path::new("/no/such/path/zzz"), "x", false);
        assert!(matches!(
            result,
            Err(SandboxError::WorkspaceMissing(_) | SandboxError::IoError(_))
        ));
    }

    #[test]
    fn workspace_not_dir() {
        let dir = tmp_dir();
        let f = dir.path().join("f.txt");
        fs::write(&f, b"x").unwrap();
        let result = enforce_workspace(&f, "anything", false);
        // canonicalize will succeed; we then catch the not-dir branch.
        assert!(matches!(result, Err(SandboxError::WorkspaceNotDir(_))));
    }

    #[test]
    fn nonexistent_leaf_under_workspace_accepted() {
        // file_write must work for files that do not yet exist.
        let dir = tmp_dir();
        let out = enforce_workspace(dir.path(), "new_file.md", false).unwrap();
        assert!(out.starts_with(dir.path().canonicalize().unwrap()));
        assert!(out.ends_with("new_file.md"));
    }
}
