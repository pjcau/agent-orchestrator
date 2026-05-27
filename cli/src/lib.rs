//! Agent Orchestrator CLI library.
//!
//! Public entry point used by `main.rs` and integration tests.

pub mod auth;
pub mod cli;
pub mod client;
pub mod commands;
pub mod config;
pub mod error;
pub mod project;

use clap::Parser;
use std::io::IsTerminal;
use tracing_subscriber::EnvFilter;

pub use error::{AgoError, Result};

pub async fn run() -> Result<()> {
    let parsed = cli::Cli::parse();
    init_logging(parsed.verbose);
    let runtime = runtime::Runtime::from_args(&parsed)?;
    commands::dispatch(&runtime, parsed.command).await
}

pub fn report_error(err: &AgoError) {
    // Use stderr — never stdout, which scripts may parse.
    let color = std::io::stderr().is_terminal();
    let prefix = if color {
        "\x1b[31merror:\x1b[0m"
    } else {
        "error:"
    };
    eprintln!("{prefix} {err}");
    let mut source = std::error::Error::source(err);
    while let Some(cause) = source {
        eprintln!("  caused by: {cause}");
        source = cause.source();
    }
}

fn init_logging(verbose: u8) {
    let level = match verbose {
        0 => "warn",
        1 => "info",
        2 => "debug",
        _ => "trace",
    };
    let filter = EnvFilter::try_from_env("AGO_LOG").unwrap_or_else(|_| EnvFilter::new(level));
    let _ = tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_writer(std::io::stderr)
        .with_target(false)
        .try_init();
}

pub mod runtime {
    //! Shared runtime context passed to every command handler.

    use crate::auth::{EnvOverrideStorage, KeyringStorage, TokenStorage};
    use crate::cli::Cli;
    use crate::client::ApiClient;
    use crate::config::Config;
    use crate::error::Result;
    use crate::project::ProjectPreset;
    use std::path::PathBuf;
    use std::sync::Arc;

    pub struct Runtime {
        pub config: Config,
        pub config_path: PathBuf,
        pub storage: Arc<dyn TokenStorage>,
        pub project: Option<ProjectPreset>,
        pub project_path: Option<PathBuf>,
    }

    impl Runtime {
        pub fn from_args(args: &Cli) -> Result<Self> {
            let config_path = match &args.config {
                Some(p) => p.clone(),
                None => Config::default_path()?,
            };
            let config = Config::load_or_default(&config_path)?;
            let storage: Arc<dyn TokenStorage> =
                Arc::new(EnvOverrideStorage::new(KeyringStorage::new("ago-cli")));
            let (project_path, project) = match std::env::current_dir() {
                Ok(cwd) => match ProjectPreset::discover(&cwd, None)? {
                    Some((p, preset)) => (Some(p), Some(preset)),
                    None => (None, None),
                },
                Err(_) => (None, None),
            };
            Ok(Self {
                config,
                config_path,
                storage,
                project,
                project_path,
            })
        }

        pub fn with_components(
            config: Config,
            config_path: PathBuf,
            storage: Arc<dyn TokenStorage>,
        ) -> Self {
            Self {
                config,
                config_path,
                storage,
                project: None,
                project_path: None,
            }
        }

        pub fn with_project(mut self, preset: ProjectPreset, path: Option<PathBuf>) -> Self {
            self.project = Some(preset);
            self.project_path = path;
            self
        }

        /// Effective server URL: `.ago.yaml > config.toml`.
        pub fn effective_server(&self) -> Option<&str> {
            self.project
                .as_ref()
                .and_then(|p| p.server.as_deref())
                .or(self.config.server.as_deref())
        }

        pub fn server_url(&self) -> Result<&str> {
            self.effective_server()
                .ok_or(crate::error::AgoError::NoServer)
        }

        pub fn api_client(&self) -> Result<ApiClient> {
            let server = self.server_url()?;
            let token = self
                .storage
                .load(server)?
                .ok_or(crate::error::AgoError::NotAuthenticated)?;
            ApiClient::new(server, Some(token))
        }
    }
}
