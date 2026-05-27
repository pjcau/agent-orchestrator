use std::process::ExitCode;

#[tokio::main]
async fn main() -> ExitCode {
    match ago::run().await {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            ago::report_error(&err);
            ExitCode::from(err.exit_code())
        }
    }
}
