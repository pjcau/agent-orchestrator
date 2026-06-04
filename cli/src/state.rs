//! Per-user runtime state that survives across `ago` invocations.
//!
//! Separate file from `config.toml` because the two have very different
//! lifecycles:
//!   - `config.toml`: edited deliberately by the user (`ago config set …`),
//!     hand-curated, small set of well-typed fields.
//!   - `state.toml`: rewritten by the CLI itself after successful turns,
//!     fast-changing, scoped per-server, more of a cache than a config.
//!
//! Today the only field is `last_conversation` — a map from server URL to
//! the most recent `conversation_id` seen. Used by `--resume` so the user
//! can continue the previous multi-turn conversation without copying a
//! UUID around. The map shape exists so a user who flips between
//! `https://orch.prod` and `http://localhost:5005` does not see their
//! prod thread resumed when they were trying to debug locally.

use crate::error::{AgoError, Result};
use directories::ProjectDirs;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{Path, PathBuf};

const QUALIFIER: &str = "io";
const ORGANIZATION: &str = "agent-orchestrator";
const APPLICATION: &str = "ago";
const STATE_FILENAME: &str = "state.toml";

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct State {
    /// server URL → most recent `conversation_id` observed for that server.
    /// Written after every successful chat turn / `ago run`. Read by
    /// `--resume` to pre-populate the next request.
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub last_conversation: HashMap<String, String>,
}

impl State {
    /// Default location for `state.toml` — the same dir as `config.toml`.
    /// Sharing the dir keeps the 0600-permission boundary identical and
    /// avoids surprising the user with a second location to back up.
    pub fn default_path() -> Result<PathBuf> {
        let dirs = ProjectDirs::from(QUALIFIER, ORGANIZATION, APPLICATION).ok_or_else(|| {
            AgoError::Config("could not determine user state directory".to_string())
        })?;
        Ok(dirs.config_dir().join(STATE_FILENAME))
    }

    pub fn load_or_default(path: &Path) -> Result<Self> {
        if !path.exists() {
            return Ok(Self::default());
        }
        let raw = std::fs::read_to_string(path)
            .map_err(|e| AgoError::Config(format!("read {}: {e}", path.display())))?;
        let state: State = toml::from_str(&raw)?;
        Ok(state)
    }

    pub fn save(&self, path: &Path) -> Result<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|e| AgoError::Config(format!("create {}: {e}", parent.display())))?;
        }
        let raw = toml::to_string_pretty(self)?;
        write_state_file(path, raw.as_bytes())?;
        Ok(())
    }

    pub fn last_conversation_for(&self, server: &str) -> Option<&str> {
        self.last_conversation.get(server).map(|s| s.as_str())
    }

    pub fn set_last_conversation(&mut self, server: &str, conv_id: &str) {
        self.last_conversation
            .insert(server.to_string(), conv_id.to_string());
    }
}

/// Read-modify-write helper used by chat/run handlers after every turn.
/// Doing the round-trip via disk (rather than holding a `RefCell<State>`
/// on the Runtime) means a `kill -9` on the CLI process never loses more
/// than the latest single turn, and two simultaneous `ago` invocations
/// against different servers don't race on the same in-memory state.
pub fn persist_conversation(state_path: &Path, server: &str, conv_id: &str) -> Result<()> {
    let mut state = State::load_or_default(state_path)?;
    state.set_last_conversation(server, conv_id);
    state.save(state_path)
}

#[cfg(unix)]
fn write_state_file(path: &Path, bytes: &[u8]) -> Result<()> {
    use std::io::Write;
    use std::os::unix::fs::OpenOptionsExt;
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .mode(0o600)
        .open(path)?;
    f.write_all(bytes)?;
    Ok(())
}

#[cfg(not(unix))]
fn write_state_file(path: &Path, bytes: &[u8]) -> Result<()> {
    std::fs::write(path, bytes)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn load_missing_file_returns_default() {
        let dir = tempdir().unwrap();
        let st = State::load_or_default(&dir.path().join("absent.toml")).unwrap();
        assert!(st.last_conversation.is_empty());
    }

    #[test]
    fn round_trip_save_load() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("state.toml");
        let mut st = State::default();
        st.set_last_conversation("https://orch.example.com", "conv-abc-123");
        st.save(&path).unwrap();
        let loaded = State::load_or_default(&path).unwrap();
        assert_eq!(loaded, st);
    }

    #[test]
    fn per_server_isolation() {
        let mut st = State::default();
        st.set_last_conversation("https://prod.example.com", "prod-1");
        st.set_last_conversation("http://localhost:5005", "local-1");
        assert_eq!(
            st.last_conversation_for("https://prod.example.com"),
            Some("prod-1")
        );
        assert_eq!(
            st.last_conversation_for("http://localhost:5005"),
            Some("local-1")
        );
        assert_eq!(st.last_conversation_for("https://other.example.com"), None);
    }

    #[test]
    fn persist_helper_round_trip() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("state.toml");
        persist_conversation(&path, "https://x.io", "first").unwrap();
        persist_conversation(&path, "https://x.io", "second").unwrap();
        persist_conversation(&path, "https://y.io", "y-first").unwrap();
        let st = State::load_or_default(&path).unwrap();
        assert_eq!(st.last_conversation_for("https://x.io"), Some("second"));
        assert_eq!(st.last_conversation_for("https://y.io"), Some("y-first"));
    }

    #[test]
    fn unknown_field_rejected() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("bad.toml");
        std::fs::write(&path, "rogue = 1\n").unwrap();
        let err = State::load_or_default(&path).unwrap_err();
        assert!(matches!(err, AgoError::Config(_)));
    }

    #[cfg(unix)]
    #[test]
    fn save_uses_0600_permissions() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempdir().unwrap();
        let path = dir.path().join("state.toml");
        let mut st = State::default();
        st.set_last_conversation("https://x.io", "z");
        st.save(&path).unwrap();
        let mode = std::fs::metadata(&path).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600);
    }
}
