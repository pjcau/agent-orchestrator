use crate::auth::validate_token;
use crate::cli::LoginArgs;
use crate::client::{ApiClient, DevicePollOutcome};
use crate::config::validate_server_url;
use crate::error::{AgoError, Result};
use crate::runtime::Runtime;
use secrecy::SecretString;
use std::io::{BufRead, IsTerminal, Write};
use std::time::Duration;

pub async fn run(rt: &Runtime, args: LoginArgs) -> Result<()> {
    let server = resolve_server(rt, args.server.as_deref())?;
    validate_server_url(&server)?;

    let token = if args.device {
        device_flow(&server, args.no_browser).await?
    } else {
        api_key_login(args.key_env.as_deref(), args.with_stdin, &server).await?
    };

    rt.storage.save(&server, &token)?;
    let mut cfg = rt.config.clone();
    cfg.set("server", &server)?;
    cfg.save(&rt.config_path)?;

    let me = ApiClient::new(&server, Some(token.clone()))?
        .whoami()
        .await?;
    let identity = me
        .email
        .or(me.name)
        .unwrap_or_else(|| "unknown".to_string());
    println!("Authenticated as {identity} on {server}");
    Ok(())
}

fn resolve_server(rt: &Runtime, override_server: Option<&str>) -> Result<String> {
    if let Some(s) = override_server {
        return Ok(s.to_string());
    }
    if let Some(s) = rt.effective_server() {
        return Ok(s.to_string());
    }
    Err(AgoError::NoServer)
}

async fn api_key_login(
    key_env: Option<&str>,
    with_stdin: bool,
    server: &str,
) -> Result<SecretString> {
    let raw_token = read_token(key_env, with_stdin)?;
    validate_token(&raw_token)?;
    let token = SecretString::from(raw_token);
    let client = ApiClient::new(server, Some(token.clone()))?;
    let _ = client.whoami().await?;
    Ok(token)
}

async fn device_flow(server: &str, no_browser: bool) -> Result<SecretString> {
    let client = ApiClient::new(server, None)?;
    let auth = client.device_authorization().await?;
    let url = auth
        .verification_uri_complete
        .clone()
        .unwrap_or_else(|| format!("{}?user_code={}", auth.verification_uri, auth.user_code));
    eprintln!();
    eprintln!("To authorize this device, open:");
    eprintln!("    {url}");
    eprintln!();
    eprintln!("and confirm the pairing code:  {}", auth.user_code);
    eprintln!();
    if !no_browser {
        // Best-effort browser open; failure is non-fatal — the user can copy
        // the URL by hand.
        if let Err(e) = open::that_detached(&url) {
            tracing::debug!(error = %e, "failed to open browser");
        }
    }
    eprintln!("Waiting for approval (Ctrl-C to cancel)...");

    let mut interval = auth.interval.max(1);
    let started = std::time::Instant::now();
    let deadline = Duration::from_secs(auth.expires_in);
    loop {
        tokio::time::sleep(Duration::from_secs(interval)).await;
        if started.elapsed() > deadline {
            return Err(AgoError::Other(
                "device-flow expired before approval".into(),
            ));
        }
        match client.device_token(&auth.device_code).await? {
            DevicePollOutcome::Approved { access_token } => {
                eprintln!();
                return Ok(SecretString::from(access_token));
            }
            DevicePollOutcome::Pending => {
                eprint!(".");
                let _ = std::io::stderr().flush();
            }
            DevicePollOutcome::SlowDown => {
                interval += 5;
                tracing::debug!(interval, "received slow_down");
            }
            DevicePollOutcome::Denied => {
                return Err(AgoError::Other("user denied the device pairing".into()));
            }
            DevicePollOutcome::Expired => {
                return Err(AgoError::Other(
                    "device-flow expired before approval".into(),
                ));
            }
            DevicePollOutcome::Unknown => {
                return Err(AgoError::Other(
                    "server forgot the device_code (was the server restarted?)".into(),
                ));
            }
        }
    }
}

fn read_token(env_var: Option<&str>, with_stdin: bool) -> Result<String> {
    if let Some(var) = env_var {
        return std::env::var(var)
            .map(|v| v.trim().to_string())
            .map_err(|_| AgoError::Config(format!("env var {var} is not set")));
    }
    let stdin = std::io::stdin();
    if with_stdin || !stdin.is_terminal() {
        let mut buf = String::new();
        stdin.lock().read_line(&mut buf)?;
        return Ok(buf.trim().to_string());
    }
    // Interactive fallback — visible echo, with a warning. For private input
    // users should use `--key-env` or pipe via stdin.
    let mut stderr = std::io::stderr();
    writeln!(
        stderr,
        "warning: input is visible. For private input, pipe stdin or use --key-env VAR."
    )?;
    write!(stderr, "Paste API key: ")?;
    stderr.flush()?;
    let mut buf = String::new();
    stdin.lock().read_line(&mut buf)?;
    Ok(buf.trim().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::auth::MemoryStorage;
    use crate::config::Config;
    use std::sync::Arc;
    use tempfile::tempdir;
    use wiremock::matchers::{header, method, path};
    use wiremock::{Mock, MockServer, ResponseTemplate};

    fn rt_with(server: &str) -> (Runtime, tempfile::TempDir) {
        let dir = tempdir().unwrap();
        let cfg_path = dir.path().join("config.toml");
        let mut cfg = Config::default();
        cfg.set("server", server).unwrap();
        let storage = Arc::new(MemoryStorage::new());
        (Runtime::with_components(cfg, cfg_path, storage), dir)
    }

    #[tokio::test]
    async fn login_with_env_var_succeeds() {
        let server_mock = MockServer::start().await;
        Mock::given(method("GET"))
            .and(path("/api/cli/v1/whoami"))
            .and(header("X-API-Key", "supersecret"))
            .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
                "email": "alice@example.com",
                "role": "developer"
            })))
            .mount(&server_mock)
            .await;

        let (rt, _dir) = rt_with(&server_mock.uri());
        let var = "AGO_TEST_LOGIN_ENV";
        std::env::set_var(var, "supersecret");
        let args = LoginArgs {
            server: None,
            key_env: Some(var.to_string()),
            with_stdin: false,
            device: false,
            no_browser: false,
        };
        super::run(&rt, args).await.unwrap();
        std::env::remove_var(var);

        let got = rt.storage.load(&server_mock.uri()).unwrap();
        assert!(got.is_some());
    }

    #[tokio::test]
    async fn login_with_bad_key_does_not_persist() {
        let server_mock = MockServer::start().await;
        Mock::given(method("GET"))
            .and(path("/api/cli/v1/whoami"))
            .respond_with(ResponseTemplate::new(401))
            .mount(&server_mock)
            .await;

        let (rt, _dir) = rt_with(&server_mock.uri());
        let var = "AGO_TEST_LOGIN_BAD";
        std::env::set_var(var, "wrong");
        let args = LoginArgs {
            server: None,
            key_env: Some(var.to_string()),
            with_stdin: false,
            device: false,
            no_browser: false,
        };
        let err = super::run(&rt, args).await.unwrap_err();
        std::env::remove_var(var);
        assert!(matches!(err, AgoError::AuthRejected));
        assert!(rt.storage.load(&server_mock.uri()).unwrap().is_none());
    }

    #[tokio::test]
    async fn login_requires_server() {
        let dir = tempdir().unwrap();
        let cfg_path = dir.path().join("config.toml");
        let storage = Arc::new(MemoryStorage::new());
        let rt = Runtime::with_components(Config::default(), cfg_path, storage);
        let var = "AGO_TEST_LOGIN_NO_SERVER";
        std::env::set_var(var, "k");
        let err = super::run(
            &rt,
            LoginArgs {
                server: None,
                key_env: Some(var.to_string()),
                with_stdin: false,
                device: false,
                no_browser: false,
            },
        )
        .await
        .unwrap_err();
        std::env::remove_var(var);
        assert!(matches!(err, AgoError::NoServer));
    }
}
