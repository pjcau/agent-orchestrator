use crate::cli::Command;
use crate::error::Result;
use crate::runtime::Runtime;

pub mod config;
pub mod login;
pub mod logout;
pub mod whoami;

pub async fn dispatch(rt: &Runtime, cmd: Command) -> Result<()> {
    match cmd {
        Command::Login(args) => login::run(rt, args).await,
        Command::Logout(args) => logout::run(rt, args),
        Command::Whoami => whoami::run(rt).await,
        Command::Config(args) => config::run(rt, args),
    }
}
