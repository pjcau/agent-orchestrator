//! Token storage abstractions.
//!
//! The CLI never persists tokens in plaintext files. The default storage chain is:
//!
//! 1. `AGO_TOKEN` environment variable (read-only, useful for CI).
//! 2. The OS keychain via the `keyring` crate (macOS Keychain, Linux Secret
//!    Service, Windows Credential Manager).
//!
//! Tokens in memory are wrapped in `secrecy::SecretString` and zeroized on drop.

use crate::error::{AgoError, Result};
use secrecy::{ExposeSecret, SecretString};
use std::sync::{Arc, Mutex};

const ENV_VAR: &str = "AGO_TOKEN";

/// Identifies a token in a `TokenStorage` by server URL.
///
/// Server URLs are normalized to remove trailing slashes before being used
/// as keychain account names so that `https://x` and `https://x/` map to
/// the same record.
pub fn normalize_server(server: &str) -> String {
    server.trim_end_matches('/').to_string()
}

pub trait TokenStorage: Send + Sync {
    fn load(&self, server: &str) -> Result<Option<SecretString>>;
    fn save(&self, server: &str, token: &SecretString) -> Result<()>;
    fn delete(&self, server: &str) -> Result<()>;
    /// Human-readable name used in `whoami`-style diagnostics. Default: type name.
    fn describe(&self) -> &'static str {
        "token-storage"
    }
}

// ---- keyring-backed storage ------------------------------------------------

pub struct KeyringStorage {
    service: String,
}

impl KeyringStorage {
    pub fn new(service: impl Into<String>) -> Self {
        Self {
            service: service.into(),
        }
    }

    fn entry(&self, server: &str) -> Result<keyring::Entry> {
        let account = normalize_server(server);
        Ok(keyring::Entry::new(&self.service, &account)?)
    }
}

impl TokenStorage for KeyringStorage {
    fn load(&self, server: &str) -> Result<Option<SecretString>> {
        let entry = self.entry(server)?;
        match entry.get_password() {
            Ok(p) => Ok(Some(SecretString::from(p))),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(e) => Err(e.into()),
        }
    }

    fn save(&self, server: &str, token: &SecretString) -> Result<()> {
        let entry = self.entry(server)?;
        entry.set_password(token.expose_secret())?;
        Ok(())
    }

    fn delete(&self, server: &str) -> Result<()> {
        let entry = self.entry(server)?;
        match entry.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(e) => Err(e.into()),
        }
    }

    fn describe(&self) -> &'static str {
        "os-keychain"
    }
}

// ---- env-override storage --------------------------------------------------

/// Wraps another storage and lets `AGO_TOKEN` override `load`. `save`/`delete`
/// are forwarded to the inner storage.
pub struct EnvOverrideStorage<T: TokenStorage> {
    inner: T,
    var: String,
}

impl<T: TokenStorage> EnvOverrideStorage<T> {
    pub fn new(inner: T) -> Self {
        Self::with_var(inner, ENV_VAR)
    }

    pub fn with_var(inner: T, var: impl Into<String>) -> Self {
        Self {
            inner,
            var: var.into(),
        }
    }
}

impl<T: TokenStorage> TokenStorage for EnvOverrideStorage<T> {
    fn load(&self, server: &str) -> Result<Option<SecretString>> {
        if let Ok(v) = std::env::var(&self.var) {
            if !v.is_empty() {
                return Ok(Some(SecretString::from(v)));
            }
        }
        self.inner.load(server)
    }

    fn save(&self, server: &str, token: &SecretString) -> Result<()> {
        self.inner.save(server, token)
    }

    fn delete(&self, server: &str) -> Result<()> {
        self.inner.delete(server)
    }

    fn describe(&self) -> &'static str {
        "env-then-keychain"
    }
}

// ---- in-memory storage (for tests) -----------------------------------------

#[derive(Default, Clone)]
pub struct MemoryStorage {
    inner: Arc<Mutex<std::collections::HashMap<String, String>>>,
}

impl MemoryStorage {
    pub fn new() -> Self {
        Self::default()
    }
}

impl TokenStorage for MemoryStorage {
    fn load(&self, server: &str) -> Result<Option<SecretString>> {
        let map = self.inner.lock().unwrap();
        Ok(map
            .get(&normalize_server(server))
            .map(|s| SecretString::from(s.clone())))
    }

    fn save(&self, server: &str, token: &SecretString) -> Result<()> {
        let mut map = self.inner.lock().unwrap();
        map.insert(normalize_server(server), token.expose_secret().to_string());
        Ok(())
    }

    fn delete(&self, server: &str) -> Result<()> {
        let mut map = self.inner.lock().unwrap();
        map.remove(&normalize_server(server));
        Ok(())
    }

    fn describe(&self) -> &'static str {
        "in-memory"
    }
}

/// Sanity-check a token before storing or sending. Rejects empty / control
/// characters / overly long values. Real authorization happens server-side.
pub fn validate_token(value: &str) -> Result<()> {
    if value.is_empty() {
        return Err(AgoError::InvalidToken);
    }
    if value.len() > 4096 {
        return Err(AgoError::InvalidToken);
    }
    if value
        .chars()
        .any(|c| c.is_control() || c == ' ' || c == '\t')
    {
        return Err(AgoError::InvalidToken);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_strips_trailing_slash() {
        assert_eq!(normalize_server("https://x/"), "https://x");
        assert_eq!(normalize_server("https://x"), "https://x");
        assert_eq!(normalize_server("https://x///"), "https://x");
    }

    #[test]
    fn memory_storage_round_trip() {
        let s = MemoryStorage::new();
        assert!(s.load("https://x").unwrap().is_none());
        s.save("https://x", &SecretString::from("k".to_string()))
            .unwrap();
        let got = s.load("https://x").unwrap().unwrap();
        assert_eq!(got.expose_secret(), "k");
        s.delete("https://x").unwrap();
        assert!(s.load("https://x").unwrap().is_none());
    }

    #[test]
    fn delete_missing_is_idempotent() {
        let s = MemoryStorage::new();
        s.delete("https://x").unwrap();
    }

    #[test]
    fn env_override_uses_env_when_set() {
        let inner = MemoryStorage::new();
        let var = "AGO_TEST_TOKEN_VAR_1";
        let s = EnvOverrideStorage::with_var(inner.clone(), var);
        // Safety: tests run single-threaded for this var by using a unique name.
        std::env::set_var(var, "env-key");
        let token = s.load("https://x").unwrap().unwrap();
        assert_eq!(token.expose_secret(), "env-key");
        std::env::remove_var(var);
    }

    #[test]
    fn env_override_falls_back_when_unset() {
        let inner = MemoryStorage::new();
        inner
            .save("https://x", &SecretString::from("inner".to_string()))
            .unwrap();
        let var = "AGO_TEST_TOKEN_VAR_2";
        std::env::remove_var(var);
        let s = EnvOverrideStorage::with_var(inner, var);
        let token = s.load("https://x").unwrap().unwrap();
        assert_eq!(token.expose_secret(), "inner");
    }

    #[test]
    fn validate_token_rejects_empty() {
        assert!(validate_token("").is_err());
    }

    #[test]
    fn validate_token_rejects_whitespace() {
        assert!(validate_token("ab cd").is_err());
        assert!(validate_token("ab\tcd").is_err());
        assert!(validate_token("ab\ncd").is_err());
    }

    #[test]
    fn validate_token_rejects_too_long() {
        let big = "a".repeat(5000);
        assert!(validate_token(&big).is_err());
    }

    #[test]
    fn validate_token_accepts_typical() {
        assert!(validate_token("ago_pat_abcDEF123-_").is_ok());
    }
}
