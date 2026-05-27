use crate::cli::LogoutArgs;
use crate::error::{AgoError, Result};
use crate::runtime::Runtime;

pub fn run(rt: &Runtime, args: LogoutArgs) -> Result<()> {
    let server = args
        .server
        .or_else(|| rt.config.server.clone())
        .ok_or(AgoError::NoServer)?;
    rt.storage.delete(&server)?;
    println!("Logged out from {server}");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::auth::{MemoryStorage, TokenStorage};
    use crate::cli::LogoutArgs;
    use crate::config::Config;
    use secrecy::SecretString;
    use std::sync::Arc;
    use tempfile::tempdir;

    #[test]
    fn logout_removes_token() {
        let storage = Arc::new(MemoryStorage::new());
        storage
            .save("https://x.io", &SecretString::from("k".to_string()))
            .unwrap();
        let dir = tempdir().unwrap();
        let mut cfg = Config::default();
        cfg.set("server", "https://x.io").unwrap();
        let rt = Runtime::with_components(cfg, dir.path().join("c.toml"), storage.clone());
        run(&rt, LogoutArgs { server: None }).unwrap();
        assert!(storage.load("https://x.io").unwrap().is_none());
    }

    #[test]
    fn logout_with_no_server_errors() {
        let storage = Arc::new(MemoryStorage::new());
        let dir = tempdir().unwrap();
        let rt = Runtime::with_components(Config::default(), dir.path().join("c.toml"), storage);
        let err = run(&rt, LogoutArgs { server: None }).unwrap_err();
        assert!(matches!(err, AgoError::NoServer));
    }

    #[test]
    fn logout_explicit_server_overrides_config() {
        let storage = Arc::new(MemoryStorage::new());
        storage
            .save("https://other.io", &SecretString::from("k".to_string()))
            .unwrap();
        let dir = tempdir().unwrap();
        let rt = Runtime::with_components(
            Config::default(),
            dir.path().join("c.toml"),
            storage.clone(),
        );
        run(
            &rt,
            LogoutArgs {
                server: Some("https://other.io".to_string()),
            },
        )
        .unwrap();
        assert!(storage.load("https://other.io").unwrap().is_none());
    }
}
