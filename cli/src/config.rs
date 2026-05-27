use crate::error::{AgoError, Result};
use directories::ProjectDirs;
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use url::Url;

const QUALIFIER: &str = "io";
const ORGANIZATION: &str = "agent-orchestrator";
const APPLICATION: &str = "ago";
const CONFIG_FILENAME: &str = "config.toml";

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Config {
    /// The active orchestrator server URL.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub server: Option<String>,

    /// Optional default agent for `ago run` (used by future phases).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub default_agent: Option<String>,
}

impl Config {
    pub fn default_path() -> Result<PathBuf> {
        let dirs = ProjectDirs::from(QUALIFIER, ORGANIZATION, APPLICATION).ok_or_else(|| {
            AgoError::Config("could not determine user config directory".to_string())
        })?;
        Ok(dirs.config_dir().join(CONFIG_FILENAME))
    }

    pub fn load_or_default(path: &Path) -> Result<Self> {
        if !path.exists() {
            return Ok(Self::default());
        }
        let raw = std::fs::read_to_string(path)
            .map_err(|e| AgoError::Config(format!("read {}: {e}", path.display())))?;
        let cfg: Config = toml::from_str(&raw)?;
        Ok(cfg)
    }

    pub fn save(&self, path: &Path) -> Result<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|e| AgoError::Config(format!("create {}: {e}", parent.display())))?;
        }
        let raw = toml::to_string_pretty(self)?;
        write_with_mode(path, raw.as_bytes(), 0o600)?;
        Ok(())
    }

    pub fn set(&mut self, key: &str, value: &str) -> Result<()> {
        match key {
            "server" => {
                validate_server_url(value)?;
                self.server = Some(value.to_string());
                Ok(())
            }
            "default_agent" => {
                if value.is_empty() {
                    return Err(AgoError::Config("default_agent cannot be empty".into()));
                }
                self.default_agent = Some(value.to_string());
                Ok(())
            }
            other => Err(AgoError::Config(format!("unknown config key: {other}"))),
        }
    }

    pub fn get(&self, key: &str) -> Result<String> {
        match key {
            "server" => Ok(self.server.clone().unwrap_or_default()),
            "default_agent" => Ok(self.default_agent.clone().unwrap_or_default()),
            other => Err(AgoError::Config(format!("unknown config key: {other}"))),
        }
    }
}

/// Validate that a server URL is well-formed and uses a safe scheme.
///
/// Allows:
/// - https://... (any host)
/// - http://localhost or http://127.0.0.1 (development only)
pub fn validate_server_url(value: &str) -> Result<()> {
    let url = Url::parse(value).map_err(|e| AgoError::InvalidServerUrl(e.to_string()))?;
    match url.scheme() {
        "https" => Ok(()),
        "http" => {
            let host = url.host_str().unwrap_or("");
            if host == "localhost" || host == "127.0.0.1" || host == "::1" {
                Ok(())
            } else {
                Err(AgoError::InsecureServerUrl)
            }
        }
        _ => Err(AgoError::InvalidServerUrl(format!(
            "scheme must be http or https, got {}",
            url.scheme()
        ))),
    }
}

#[cfg(unix)]
fn write_with_mode(path: &Path, bytes: &[u8], mode: u32) -> Result<()> {
    use std::io::Write;
    use std::os::unix::fs::OpenOptionsExt;
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .mode(mode)
        .open(path)?;
    file.write_all(bytes)?;
    Ok(())
}

#[cfg(not(unix))]
fn write_with_mode(path: &Path, bytes: &[u8], _mode: u32) -> Result<()> {
    std::fs::write(path, bytes)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn validate_https_ok() {
        assert!(validate_server_url("https://example.com").is_ok());
        assert!(validate_server_url("https://example.com:8443/api").is_ok());
    }

    #[test]
    fn validate_localhost_http_ok() {
        assert!(validate_server_url("http://localhost:5005").is_ok());
        assert!(validate_server_url("http://127.0.0.1").is_ok());
    }

    #[test]
    fn validate_remote_http_rejected() {
        assert!(matches!(
            validate_server_url("http://example.com"),
            Err(AgoError::InsecureServerUrl)
        ));
    }

    #[test]
    fn validate_garbage_rejected() {
        assert!(validate_server_url("not a url").is_err());
        assert!(validate_server_url("ftp://example.com").is_err());
    }

    #[test]
    fn round_trip_save_load() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("config.toml");
        let mut cfg = Config::default();
        cfg.set("server", "https://example.com").unwrap();
        cfg.save(&path).unwrap();
        let loaded = Config::load_or_default(&path).unwrap();
        assert_eq!(loaded, cfg);
    }

    #[test]
    fn load_missing_file_returns_default() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("missing.toml");
        let loaded = Config::load_or_default(&path).unwrap();
        assert_eq!(loaded, Config::default());
    }

    #[test]
    fn rejects_unknown_keys_on_set() {
        let mut cfg = Config::default();
        assert!(cfg.set("bogus", "value").is_err());
    }

    #[test]
    fn rejects_unknown_fields_on_load() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("bad.toml");
        std::fs::write(&path, "server = \"https://x.io\"\nrogue_field = 42\n").unwrap();
        let err = Config::load_or_default(&path).unwrap_err();
        assert!(matches!(err, AgoError::Config(_)));
    }

    #[cfg(unix)]
    #[test]
    fn save_uses_0600_permissions() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempdir().unwrap();
        let path = dir.path().join("config.toml");
        let mut cfg = Config::default();
        cfg.set("server", "https://example.com").unwrap();
        cfg.save(&path).unwrap();
        let mode = std::fs::metadata(&path).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600);
    }
}
