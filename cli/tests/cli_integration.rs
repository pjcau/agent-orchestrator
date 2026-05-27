//! Black-box integration tests that exec the `ago` binary.
//!
//! These tests rely on `assert_cmd` to run the compiled binary against a
//! mock HTTP server. We isolate every test from the user's real config and
//! keychain by pointing `--config` at a temp file and setting `AGO_TOKEN`.

use assert_cmd::Command;
use predicates::str::contains;
use tempfile::tempdir;
use wiremock::matchers::{header, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

fn ago() -> Command {
    Command::cargo_bin("ago").expect("binary built")
}

#[test]
fn version_flag() {
    ago()
        .arg("--version")
        .assert()
        .success()
        .stdout(contains("ago"));
}

#[test]
fn help_lists_commands() {
    ago()
        .arg("--help")
        .assert()
        .success()
        .stdout(contains("login"))
        .stdout(contains("logout"))
        .stdout(contains("whoami"))
        .stdout(contains("config"));
}

#[test]
fn config_set_and_show() {
    let dir = tempdir().unwrap();
    let cfg = dir.path().join("config.toml");

    ago()
        .args([
            "--config",
            cfg.to_str().unwrap(),
            "config",
            "set",
            "server",
            "https://example.com",
        ])
        .assert()
        .success();

    ago()
        .args(["--config", cfg.to_str().unwrap(), "config", "show"])
        .assert()
        .success()
        .stdout(contains("https://example.com"));
}

#[test]
fn config_set_rejects_remote_http() {
    let dir = tempdir().unwrap();
    let cfg = dir.path().join("config.toml");

    ago()
        .args([
            "--config",
            cfg.to_str().unwrap(),
            "config",
            "set",
            "server",
            "http://example.com",
        ])
        .assert()
        .failure()
        .stderr(contains("https"));
}

#[test]
fn whoami_without_server_fails_cleanly() {
    let dir = tempdir().unwrap();
    let cfg = dir.path().join("config.toml");

    ago()
        .env("AGO_TOKEN", "x")
        .env_remove("AGO_API_KEY")
        .args(["--config", cfg.to_str().unwrap(), "whoami"])
        .assert()
        .failure()
        .stderr(contains("no server configured"));
}

#[tokio::test(flavor = "multi_thread")]
async fn whoami_against_mock_server() {
    let mock = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/api/cli/v1/whoami"))
        .and(header("X-API-Key", "deadbeef"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "email": "alice@example.com",
            "role": "developer",
            "server_version": "0.2.0"
        })))
        .mount(&mock)
        .await;

    let dir = tempdir().unwrap();
    let cfg = dir.path().join("config.toml");

    ago()
        .args([
            "--config",
            cfg.to_str().unwrap(),
            "config",
            "set",
            "server",
            mock.uri().as_str(),
        ])
        .assert()
        .success();

    ago()
        .env("AGO_TOKEN", "deadbeef")
        .args(["--config", cfg.to_str().unwrap(), "whoami"])
        .assert()
        .success()
        .stdout(contains("alice@example.com"))
        .stdout(contains("developer"));
}

#[tokio::test(flavor = "multi_thread")]
async fn run_command_renders_output() {
    let mock = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/api/agent/run"))
        .and(header("X-API-Key", "k"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "success": true,
            "output": "ALL_GOOD",
            "elapsed_s": 0.5
        })))
        .mount(&mock)
        .await;

    let dir = tempdir().unwrap();
    let cfg = dir.path().join("config.toml");

    ago()
        .args([
            "--config",
            cfg.to_str().unwrap(),
            "config",
            "set",
            "server",
            mock.uri().as_str(),
        ])
        .assert()
        .success();

    ago()
        .env("AGO_TOKEN", "k")
        .args([
            "--config",
            cfg.to_str().unwrap(),
            "run",
            "--agent",
            "backend",
            "--model",
            "test-model",
            "do the thing",
        ])
        .assert()
        .success()
        .stdout(contains("ALL_GOOD"));
}

#[tokio::test(flavor = "multi_thread")]
async fn run_command_stream_mode() {
    let mock = MockServer::start().await;
    let body = concat!(
        "event: start\n",
        "data: {\"run_id\":\"abc\"}\n",
        "\n",
        "event: complete\n",
        "data: {\"success\":true,\"output\":\"STREAMED\"}\n",
        "\n",
    );
    Mock::given(method("POST"))
        .and(path("/api/cli/v1/run"))
        .and(header("X-API-Key", "k"))
        .respond_with(
            ResponseTemplate::new(200)
                .insert_header("content-type", "text/event-stream")
                .set_body_raw(body, "text/event-stream"),
        )
        .mount(&mock)
        .await;

    let dir = tempdir().unwrap();
    let cfg = dir.path().join("config.toml");

    ago()
        .args([
            "--config",
            cfg.to_str().unwrap(),
            "config",
            "set",
            "server",
            mock.uri().as_str(),
        ])
        .assert()
        .success();

    ago()
        .env("AGO_TOKEN", "k")
        .args([
            "--config",
            cfg.to_str().unwrap(),
            "run",
            "--agent",
            "backend",
            "--model",
            "m",
            "--stream",
            "task",
        ])
        .assert()
        .success()
        .stdout(contains("STREAMED"));
}

#[tokio::test(flavor = "multi_thread")]
async fn run_command_json_mode() {
    let mock = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/api/agent/run"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "success": true,
            "output": "X"
        })))
        .mount(&mock)
        .await;

    let dir = tempdir().unwrap();
    let cfg = dir.path().join("config.toml");

    ago()
        .args([
            "--config",
            cfg.to_str().unwrap(),
            "config",
            "set",
            "server",
            mock.uri().as_str(),
        ])
        .assert()
        .success();

    ago()
        .env("AGO_TOKEN", "k")
        .args([
            "--config",
            cfg.to_str().unwrap(),
            "run",
            "--agent",
            "backend",
            "--model",
            "m",
            "--json",
            "task",
        ])
        .assert()
        .success()
        .stdout(contains("\"success\""))
        .stdout(contains("\"output\""));
}

#[tokio::test(flavor = "multi_thread")]
async fn whoami_rejects_bad_token() {
    let mock = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/api/cli/v1/whoami"))
        .respond_with(ResponseTemplate::new(401))
        .mount(&mock)
        .await;

    let dir = tempdir().unwrap();
    let cfg = dir.path().join("config.toml");

    ago()
        .args([
            "--config",
            cfg.to_str().unwrap(),
            "config",
            "set",
            "server",
            mock.uri().as_str(),
        ])
        .assert()
        .success();

    ago()
        .env("AGO_TOKEN", "wrong")
        .args(["--config", cfg.to_str().unwrap(), "whoami"])
        .assert()
        .failure()
        .stderr(contains("authentication rejected"));
}
