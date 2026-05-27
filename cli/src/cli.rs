use clap::{Parser, Subcommand};
use std::path::PathBuf;

/// Agent Orchestrator CLI.
///
/// Authenticate once, then talk to a remote orchestrator from any local project.
#[derive(Debug, Parser)]
#[command(
    name = "ago",
    version,
    about = "Agent Orchestrator CLI",
    long_about = "Authenticate against a remote Agent Orchestrator dashboard and run tasks from your local project."
)]
pub struct Cli {
    /// Path to an alternate config file (default: platform-specific user config dir).
    #[arg(long, global = true, value_name = "FILE")]
    pub config: Option<PathBuf>,

    /// Increase log verbosity. -v info, -vv debug, -vvv trace. Logs go to stderr.
    #[arg(short = 'v', long, global = true, action = clap::ArgAction::Count)]
    pub verbose: u8,

    #[command(subcommand)]
    pub command: Command,
}

#[derive(Debug, Subcommand)]
pub enum Command {
    /// Authenticate against a server using an API key.
    Login(LoginArgs),
    /// Remove the stored token for a server (or the active server by default).
    Logout(LogoutArgs),
    /// Display the authenticated identity for the active server.
    Whoami,
    /// Inspect or modify the CLI configuration.
    Config(ConfigArgs),
    /// Run a task against an agent on the active server.
    Run(RunArgs),
}

#[derive(Debug, clap::Args)]
pub struct LoginArgs {
    /// Server base URL, e.g. https://orch.example.com. Saved as the active server on success.
    #[arg(long, value_name = "URL")]
    pub server: Option<String>,

    /// Read the API key from this environment variable (recommended for CI).
    /// Default: AGO_API_KEY.
    #[arg(long, value_name = "VAR", env = "AGO_API_KEY_ENV")]
    pub key_env: Option<String>,

    /// Pipe the API key on stdin instead of prompting (for headless / CI usage).
    #[arg(long)]
    pub with_stdin: bool,
}

#[derive(Debug, clap::Args)]
pub struct LogoutArgs {
    /// Server URL to logout from. Defaults to the active server.
    #[arg(long, value_name = "URL")]
    pub server: Option<String>,
}

#[derive(Debug, clap::Args)]
pub struct ConfigArgs {
    #[command(subcommand)]
    pub action: ConfigAction,
}

#[derive(Debug, Subcommand)]
pub enum ConfigAction {
    /// Print the resolved config (token values are never displayed).
    Show,
    /// Read a single config value.
    Get {
        /// Key name (e.g. `server`).
        key: String,
    },
    /// Set a config value.
    Set {
        /// Key name (e.g. `server`).
        key: String,
        /// Value to assign.
        value: String,
    },
    /// Print the path to the active config file.
    Path,
}

#[derive(Debug, clap::Args)]
pub struct RunArgs {
    /// The task description to send to the agent. Reads from stdin when omitted.
    #[arg(value_name = "TASK")]
    pub task: Option<String>,

    /// Agent name to run against (e.g. "backend"). Falls back to `default_agent`
    /// from the config, then errors if neither is set.
    #[arg(long, value_name = "NAME")]
    pub agent: Option<String>,

    /// Model identifier (provider-specific, e.g. "claude-sonnet-4-6").
    #[arg(long, value_name = "ID")]
    pub model: Option<String>,

    /// Provider type. One of: anthropic, openai, openrouter, google, ollama.
    #[arg(long, value_name = "TYPE", default_value = "ollama")]
    pub provider: String,

    /// Maximum number of agent steps before the server aborts the run.
    #[arg(long, value_name = "N", default_value_t = 10)]
    pub max_steps: u32,

    /// Emit machine-readable JSON instead of human-readable text.
    #[arg(long)]
    pub json: bool,
}
