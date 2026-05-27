use crate::cli::{ConfigAction, ConfigArgs};
use crate::error::Result;
use crate::runtime::Runtime;

pub fn run(rt: &Runtime, args: ConfigArgs) -> Result<()> {
    match args.action {
        ConfigAction::Show => {
            let raw = toml::to_string_pretty(&rt.config)?;
            print!("{raw}");
            Ok(())
        }
        ConfigAction::Get { key } => {
            let value = rt.config.get(&key)?;
            println!("{value}");
            Ok(())
        }
        ConfigAction::Set { key, value } => {
            let mut cfg = rt.config.clone();
            cfg.set(&key, &value)?;
            cfg.save(&rt.config_path)?;
            Ok(())
        }
        ConfigAction::Path => {
            println!("{}", rt.config_path.display());
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::auth::MemoryStorage;
    use crate::cli::{ConfigAction, ConfigArgs};
    use crate::config::Config;
    use crate::error::AgoError;
    use std::sync::Arc;
    use tempfile::tempdir;

    fn rt() -> (Runtime, tempfile::TempDir) {
        let dir = tempdir().unwrap();
        let cfg_path = dir.path().join("config.toml");
        let storage = Arc::new(MemoryStorage::new());
        (
            Runtime::with_components(Config::default(), cfg_path, storage),
            dir,
        )
    }

    #[test]
    fn set_persists_value() {
        let (rt, _d) = rt();
        run(
            &rt,
            ConfigArgs {
                action: ConfigAction::Set {
                    key: "server".to_string(),
                    value: "https://example.com".to_string(),
                },
            },
        )
        .unwrap();
        let reloaded = Config::load_or_default(&rt.config_path).unwrap();
        assert_eq!(reloaded.server.as_deref(), Some("https://example.com"));
    }

    #[test]
    fn set_rejects_insecure_url() {
        let (rt, _d) = rt();
        let err = run(
            &rt,
            ConfigArgs {
                action: ConfigAction::Set {
                    key: "server".to_string(),
                    value: "http://evil.com".to_string(),
                },
            },
        )
        .unwrap_err();
        assert!(matches!(err, AgoError::InsecureServerUrl));
    }

    #[test]
    fn get_unknown_key_errors() {
        let (rt, _d) = rt();
        let err = run(
            &rt,
            ConfigArgs {
                action: ConfigAction::Get {
                    key: "bogus".to_string(),
                },
            },
        )
        .unwrap_err();
        assert!(matches!(err, AgoError::Config(_)));
    }
}
