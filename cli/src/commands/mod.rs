use crate::cli::Command;
use crate::error::Result;
use crate::runtime::Runtime;

pub mod cache;
pub mod chat;
pub mod completions;
pub mod config;
pub mod jobs;
pub mod login;
pub mod logout;
pub mod print_token;
pub mod run;
pub mod self_cmd;
pub mod whoami;

pub async fn dispatch(rt: &Runtime, cmd: Command) -> Result<()> {
    match cmd {
        Command::Login(args) => login::run(rt, args).await,
        Command::Logout(args) => logout::run(rt, args),
        Command::Whoami => whoami::run(rt).await,
        Command::PrintToken => print_token::run(rt).await,
        Command::Config(args) => config::run(rt, args),
        Command::Run(args) => run::run(rt, args).await,
        Command::Jobs(args) => jobs::run(rt, args).await,
        Command::Chat(args) => chat::run(rt, args).await,
        Command::Cache(args) => cache::run(rt, args),
        Command::Completions(args) => completions::run(args),
        Command::SelfCmd(args) => self_cmd::run(rt, args).await,
    }
}
