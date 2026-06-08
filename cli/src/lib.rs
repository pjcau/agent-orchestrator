//! Agent Orchestrator CLI library.
//!
//! Public entry point used by `main.rs` and integration tests.

pub mod agent_host;
pub mod auth;
pub mod cli;
pub mod client;
pub mod commands;
pub mod config;
pub mod context;
pub mod error;
pub mod instructions;
pub mod project;
pub mod render;
pub mod state;

use clap::Parser;
use std::io::IsTerminal;
use tracing_subscriber::{fmt, prelude::*, EnvFilter};

pub use error::{AgoError, Result};

pub async fn run() -> Result<()> {
    let parsed = cli::Cli::parse();
    init_logging(parsed.verbose, parsed.log_file.as_deref());
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

fn init_logging(verbose: u8, log_file: Option<&std::path::Path>) {
    let level = match verbose {
        0 => "warn",
        1 => "info",
        2 => "debug",
        _ => "trace",
    };
    // Terminal layer: honours -v / AGO_LOG, writes to stderr (so stdout stays
    // clean for piping). Per-layer filter so the file layer can differ.
    let stderr_filter =
        EnvFilter::try_from_env("AGO_LOG").unwrap_or_else(|_| EnvFilter::new(level));
    let stderr_layer = fmt::layer()
        .with_writer(std::io::stderr)
        .with_target(false)
        .with_filter(stderr_filter);

    // Optional file layer: ALWAYS at debug (so a recorded session is useful to
    // share even when the terminal ran at the default warn level), no ANSI,
    // appended so multiple invocations accumulate. A bad path warns and is
    // skipped rather than aborting the command.
    let file_layer = log_file.and_then(|path| {
        match std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
        {
            Ok(file) => Some(
                fmt::layer()
                    .with_ansi(false)
                    .with_target(false)
                    .with_writer(std::sync::Mutex::new(file))
                    .with_filter(EnvFilter::new("debug")),
            ),
            Err(e) => {
                eprintln!("warning: --log-file {}: {e}", path.display());
                None
            }
        }
    });

    let _ = tracing_subscriber::registry()
        .with(stderr_layer)
        .with(file_layer)
        .try_init();
}

pub mod runtime {
    //! Shared runtime context passed to every command handler.

    use crate::auth::{EnvOverrideStorage, KeyringStorage, TokenStorage};
    use crate::cli::Cli;
    use crate::client::ApiClient;
    use crate::config::Config;
    use crate::error::Result;
    use crate::instructions::Instructions;
    use crate::project::ProjectPreset;
    use crate::state::State;
    use std::path::PathBuf;
    use std::sync::Arc;

    pub struct Runtime {
        pub config: Config,
        pub config_path: PathBuf,
        pub storage: Arc<dyn TokenStorage>,
        pub project: Option<ProjectPreset>,
        pub project_path: Option<PathBuf>,
        /// User asked to suppress ANSI colour via `--no-color`. The render
        /// layer also consults `NO_COLOR` and TTY detection, this flag is
        /// only the explicit override.
        pub no_color: bool,
        /// Last-known per-server state (last conversation_id). Read once at
        /// startup; chat/run commands re-read and rewrite it on disk after
        /// each successful turn so a kill -9 never loses more than one turn.
        pub state: State,
        pub state_path: PathBuf,
        /// `AGO.md` discovered walking up from cwd (or None). Loaded once at
        /// startup so each turn pays zero filesystem cost — the only cost is
        /// the bytes added to `cache_context`, which the prompt-cache covers
        /// after the first request.
        pub instructions: Option<Instructions>,
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
            // state.toml lives next to config.toml — so a test that passes
            // `--config /tmp/xyz/config.toml` naturally isolates the state
            // file at `/tmp/xyz/state.toml` without needing a second flag.
            // In production both fall in `~/.config/ago/`.
            let state_path = match config_path.parent() {
                Some(dir) => dir.join("state.toml"),
                None => State::default_path()?,
            };
            let state = State::load_or_default(&state_path)?;
            // AGO.md discovery + load (best-effort: a malformed file or a
            // read error must not abort startup — chat/run still work
            // without project instructions). Cap size at the project's
            // max_file_bytes so a huge AGO.md cannot blow the budget.
            let instructions = match std::env::current_dir() {
                Ok(cwd) => match Instructions::discover(&cwd, None) {
                    Ok(Some(p)) => {
                        let cap = project
                            .as_ref()
                            .and_then(|pr| pr.context.as_ref())
                            .and_then(|c| c.max_file_bytes)
                            .unwrap_or(8 * 1024);
                        Instructions::load(&p, cap).ok()
                    }
                    _ => None,
                },
                Err(_) => None,
            };
            Ok(Self {
                config,
                config_path,
                storage,
                project,
                project_path,
                no_color: args.no_color,
                state,
                state_path,
                instructions,
            })
        }

        pub fn with_components(
            config: Config,
            config_path: PathBuf,
            storage: Arc<dyn TokenStorage>,
        ) -> Self {
            // Tests build a Runtime out of pieces and don't care about state;
            // use a tmp path so any persist_conversation() call in the
            // exercised handler is a no-op against an isolated file.
            let state_path = std::env::temp_dir().join(format!(
                "ago-state-test-{}.toml",
                uuid::Uuid::new_v4().as_simple()
            ));
            Self {
                config,
                config_path,
                storage,
                project: None,
                project_path: None,
                no_color: false,
                state: State::default(),
                state_path,
                instructions: None,
            }
        }

        pub fn with_project(mut self, preset: ProjectPreset, path: Option<PathBuf>) -> Self {
            self.project = Some(preset);
            self.project_path = path;
            self
        }

        pub fn with_state(mut self, state: State, path: PathBuf) -> Self {
            self.state = state;
            self.state_path = path;
            self
        }

        pub fn with_instructions(mut self, instructions: Instructions) -> Self {
            self.instructions = Some(instructions);
            self
        }

        /// Whether `--client-tools` runs should be jailed in a container.
        /// `.ago.yaml`'s `jail:` wins; defaults to `true` (jail-by-default)
        /// when there is no project file or the key is omitted.
        pub fn jail_enabled(&self) -> bool {
            self.project
                .as_ref()
                .map(|p| p.jail_enabled())
                .unwrap_or(true)
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
