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

    if !follow_symlinks {
        // Walk parents and reject any symlink component. We test on
        // `candidate` rather than its resolved form so the rejection
        // happens BEFORE the symlink could redirect us. We stop at
        // the workspace root or the filesystem root — whichever comes
        // first.
        let mut cur = candidate.clone();
        loop {
            if cur == ws_canon {
                break;
            }
            // is_symlink does NOT follow, by design — exactly what we want.
            if cur.is_symlink() {
                return Err(SandboxError::SymlinkOnPath(raw.to_string()));
            }
            let parent = match cur.parent() {
                Some(p) => p.to_path_buf(),
                None => break,
            };
            if parent == cur {
                // Reached the filesystem root without crossing the
                // workspace: candidate is outside the tree entirely.
                break;
            }
            cur = parent;
        }
    }

    // Canonicalize what we can (may fail for non-existent leaf — that's
    // fine for file_write; we then resolve the parent and rejoin).
    let resolved = match candidate.canonicalize() {
        Ok(p) => p,
        Err(_) => {
            // Leaf does not yet exist (typical for file_write).
            // Canonicalize the closest existing parent and rejoin the
            // missing tail.  If even the parent does not exist we treat
            // it as an escape — file_write does not create deeply nested
            // workspaces.
            let parent = candidate.parent().unwrap_or(Path::new("/"));
            let parent_canon = parent
                .canonicalize()
                .map_err(|e| SandboxError::IoError(e.to_string()))?;
            let file_name = candidate
                .file_name()
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from(""));
            parent_canon.join(file_name)
        }
    };

    if !resolved.starts_with(&ws_canon) {
        return Err(SandboxError::PathEscape(raw.to_string()));
    }
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
    fn absolute_outside_rejected() {
        let dir = tmp_dir();
        let result = enforce_workspace(dir.path(), "/etc/passwd", false);
        assert!(matches!(result, Err(SandboxError::PathEscape(_))));
    }

    #[test]
    fn absolute_inside_accepted() {
        let dir = tmp_dir();
        let inside = dir.path().join("x.txt");
        // Create the file so canonicalize works.
        fs::write(&inside, b"").unwrap();
        let out = enforce_workspace(dir.path(), inside.to_str().unwrap(), false).unwrap();
        assert!(out.starts_with(dir.path().canonicalize().unwrap()));
    }

    #[cfg(unix)]
    #[test]
    fn symlink_default_rejected() {
        let dir = tmp_dir();
        let target_dir = tempfile::tempdir().unwrap(); // outside `dir`
        let outside = target_dir.path().join("outside.txt");
        fs::write(&outside, b"x").unwrap();
        let link = dir.path().join("alias");
        symlink(&outside, &link).unwrap();
        let result = enforce_workspace(dir.path(), "alias", false);
        assert!(matches!(result, Err(SandboxError::SymlinkOnPath(_))));
    }

    #[cfg(unix)]
    #[test]
    fn symlink_inside_with_follow_allowed() {
        let dir = tmp_dir();
        let real = dir.path().join("real.txt");
        fs::write(&real, b"x").unwrap();
        let link = dir.path().join("alias");
        symlink(&real, &link).unwrap();
        let out = enforce_workspace(dir.path(), "alias", true).unwrap();
        // The link target sits inside the workspace.
        assert!(out.starts_with(dir.path().canonicalize().unwrap()));
    }

    #[test]
    fn workspace_must_exist() {
        let result =
            enforce_workspace(Path::new("/no/such/path/zzz"), "x", false);
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
