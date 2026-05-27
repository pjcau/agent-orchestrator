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
    /// Inspect or cancel jobs/sessions on the active server.
    Jobs(JobsArgs),
    /// Start an interactive chat session (REPL).
    ///
    /// Resolves agent/model/provider once at startup (CLI flags > .ago.yaml >
    /// config), opens a conversation, then loops reading user input and
    /// streaming responses. Slash commands: `:help` to list.
    Chat(ChatArgs),
    /// Print a shell completion script for `ago`.
    ///
    /// Pipe the output to your shell's completion path, e.g.
    /// `ago completions zsh > ~/.zfunc/_ago` and add `fpath+=~/.zfunc` to
    /// your `.zshrc`.
    Completions(CompletionsArgs),
}

#[derive(Debug, clap::Args)]
pub struct CompletionsArgs {
    /// Target shell (`bash`, `zsh`, `fish`, `powershell`, `elvish`).
    #[arg(value_enum)]
    pub shell: clap_complete::Shell,
}

#[derive(Debug, clap::Args)]
pub struct JobsArgs {
    #[command(subcommand)]
    pub action: JobsAction,
}

#[derive(Debug, Subcommand)]
pub enum JobsAction {
    /// List recent job sessions on the server.
    List(JobsListArgs),
    /// Show the records of a single session.
    Show {
        /// Session id (the value printed by `ago jobs list`).
        session_id: String,
        /// Emit raw JSON instead of a human-readable summary.
        #[arg(long)]
        json: bool,
    },
    /// Cancel an in-flight team run.
    Cancel {
        /// Job id of the running team task (UUID printed by `ago run` or the dashboard).
        job_id: String,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, clap::ValueEnum)]
pub enum ChatMode {
    /// Run through the agent loop (tools, multi-step) via `/api/cli/v1/run`.
    Agent,
    /// Direct LLM completion — no tools, no step counter — via `/api/prompt`.
    /// Better suited to chat-only models that should NOT be using shell_exec.
    Prompt,
}

impl std::fmt::Display for ChatMode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            ChatMode::Agent => "agent",
            ChatMode::Prompt => "prompt",
        })
    }
}

#[derive(Debug, clap::Args)]
pub struct ChatArgs {
    /// Conversation style: `agent` (default) drives the tool-using agent loop;
    /// `prompt` calls the LLM directly without tools.
    #[arg(long, value_enum, default_value_t = ChatMode::Agent)]
    pub mode: ChatMode,

    /// Agent name. Falls back to `.ago.yaml` / `default_agent` if omitted.
    /// Ignored in `--mode prompt`.
    #[arg(long, value_name = "NAME")]
    pub agent: Option<String>,

    /// Model identifier (provider-specific).
    #[arg(long, value_name = "ID")]
    pub model: Option<String>,

    /// Provider type (anthropic, openai, openrouter, google, ollama).
    #[arg(long, value_name = "TYPE")]
    pub provider: Option<String>,

    /// Maximum agent steps per turn (server-side cap). Ignored in prompt mode.
    #[arg(long, value_name = "N", default_value_t = 10)]
    pub max_steps: u32,

    /// Disable the indicatif spinner (useful when output is piped).
    #[arg(long)]
    pub no_progress: bool,
}

#[derive(Debug, clap::Args)]
pub struct JobsListArgs {
    /// Limit the number of sessions returned.
    #[arg(long, value_name = "N", default_value_t = 20)]
    pub limit: usize,
    /// Emit raw JSON instead of a human-readable table.
    #[arg(long)]
    pub json: bool,
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

    /// Authenticate via the RFC 8628 OAuth device flow.
    ///
    /// The CLI prints a verification URL and a short user code; the user
    /// approves the pairing in a browser logged into the dashboard, and the
    /// CLI polls the server for the resulting ephemeral API token.
    #[arg(long)]
    pub device: bool,

    /// When using --device, do NOT attempt to open the browser automatically.
    #[arg(long)]
    pub no_browser: bool,
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

    /// Stream events from the server as they happen (SSE).
    ///
    /// When set, the CLI uses `/api/cli/v1/run` and prints progress events
    /// to stderr as the agent runs. When unset (default), the CLI calls the
    /// blocking `/api/agent/run` endpoint and waits for the final JSON.
    #[arg(long)]
    pub stream: bool,
}
