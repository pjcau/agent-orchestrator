//! `ago print-token` — internal helper for the jail launcher (`cli/ago`).
//!
//! Under `--client-tools` the launcher runs the binary inside a container that
//! has no OS Secret Service, so `KeyringStorage` fails with a permission error.
//! The launcher therefore calls this command on the HOST (where the keychain
//! works), captures the token, and forwards it into the sandbox via the
//! `AGO_TOKEN` environment variable — which `EnvOverrideStorage` honors. This
//! keeps the "never persist tokens in plaintext files" guarantee from `auth.rs`:
//! the secret only ever lives in the keychain and in process memory/env.
//!
//! Hidden from `--help`. Prints the token followed by a newline, or nothing at
//! all (exit 0) when no server is configured or no token is stored, so the
//! launcher can fall back gracefully to letting the container report
//! `NotAuthenticated`.

use crate::error::Result;
use crate::runtime::Runtime;
use secrecy::ExposeSecret;

/// Resolve the stored token for the effective server, if any. Pure (no I/O to
/// stdout) so it can be unit-tested without capturing process output.
pub fn resolve_token(rt: &Runtime) -> Result<Option<String>> {
    let Some(server) = rt.effective_server() else {
        return Ok(None);
    };
    Ok(rt
        .storage
        .load(server)?
        .map(|t| t.expose_secret().to_string()))
}

pub async fn run(rt: &Runtime) -> Result<()> {
    if let Some(token) = resolve_token(rt)? {
        println!("{token}");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::auth::{MemoryStorage, TokenStorage};
    use crate::config::Config;
    use crate::runtime::Runtime;
    use secrecy::SecretString;
    use std::path::PathBuf;
    use std::sync::Arc;

    fn runtime_with(server: Option<&str>, storage: Arc<MemoryStorage>) -> Runtime {
        let config = Config {
            server: server.map(|s| s.to_string()),
            ..Config::default()
        };
        Runtime::with_components(config, PathBuf::from("/tmp/ago-test-config.toml"), storage)
    }

    #[test]
    fn none_when_no_server_configured() {
        let storage = Arc::new(MemoryStorage::new());
        let rt = runtime_with(None, storage);
        assert_eq!(resolve_token(&rt).unwrap(), None);
    }

    #[test]
    fn none_when_no_token_stored() {
        let storage = Arc::new(MemoryStorage::new());
        let rt = runtime_with(Some("https://example.com"), storage);
        assert_eq!(resolve_token(&rt).unwrap(), None);
    }

    #[test]
    fn returns_token_for_effective_server() {
        let storage = Arc::new(MemoryStorage::new());
        storage
            .save(
                "https://example.com",
                &SecretString::from("ago_pat_secret".to_string()),
            )
            .unwrap();
        let rt = runtime_with(Some("https://example.com"), storage);
        assert_eq!(
            resolve_token(&rt).unwrap().as_deref(),
            Some("ago_pat_secret")
        );
    }
}
