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
        .stdout(contains("config"))
        .stdout(contains("run"))
        .stdout(contains("completions"));
}

#[test]
fn completions_zsh_emits_compdef() {
    ago()
        .args(["completions", "zsh"])
        .assert()
        .success()
        .stdout(contains("#compdef ago"));
}

#[test]
fn completions_bash_emits_complete() {
    ago()
        .args(["completions", "bash"])
        .assert()
        .success()
        .stdout(contains("complete -F"));
}

#[test]
fn completions_fish_emits_complete() {
    ago()
        .args(["completions", "fish"])
        .assert()
        .success()
        .stdout(contains("complete -c ago"));
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
async fn jobs_list_renders_table() {
    let mock = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/api/jobs/list"))
        .and(header("X-API-Key", "k"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "sessions": [
                {"session_id": "20260527_001", "records": 3, "files": 1, "last_type": "agent_run", "first_prompt": "build hello world"},
                {"session_id": "20260527_002", "records": 5, "files": 0, "last_type": "team_run", "first_prompt": "refactor module"}
            ]
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
        .args(["--config", cfg.to_str().unwrap(), "jobs", "list"])
        .assert()
        .success()
        .stdout(contains("20260527_001"))
        .stdout(contains("build hello world"))
        .stdout(contains("SESSION"));
}

#[tokio::test(flavor = "multi_thread")]
async fn jobs_cancel_posts_to_team_endpoint() {
    let mock = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/api/team/job-xyz/cancel"))
        .and(header("X-API-Key", "k"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "job_id": "job-xyz",
            "status": "running",
            "cancelled": true
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
            "jobs",
            "cancel",
            "job-xyz",
        ])
        .assert()
        .success()
        .stdout(contains("cancelling"));
}

#[tokio::test(flavor = "multi_thread")]
async fn jobs_show_renders_records() {
    let mock = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/api/jobs/abc"))
        .and(header("X-API-Key", "k"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "session_id": "abc",
            "records": [
                {"job_type": "agent_run", "task": "first task"},
                {"job_type": "agent_run", "task": "second task"}
            ]
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
        .args(["--config", cfg.to_str().unwrap(), "jobs", "show", "abc"])
        .assert()
        .success()
        .stdout(contains("first task"))
        .stdout(contains("second task"));
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
async fn ago_yaml_preset_is_used_when_flags_omitted() {
    let mock = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/api/agent/run"))
        .and(wiremock::matchers::body_partial_json(serde_json::json!({
            "agent": "preset-backend",
            "model": "preset-claude",
            "provider": "anthropic",
            "max_steps": 17
        })))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "success": true,
            "output": "FROM_PRESET"
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

    // Project preset lives in the cwd we pass to the child process.
    let project_dir = tempdir().unwrap();
    std::fs::write(
        project_dir.path().join(".ago.yaml"),
        "agent: preset-backend\nmodel: preset-claude\nprovider: anthropic\nmax_steps: 17\n",
    )
    .unwrap();

    ago()
        .env("AGO_TOKEN", "k")
        .current_dir(project_dir.path())
        .args(["--config", cfg.to_str().unwrap(), "run", "task from cwd"])
        .assert()
        .success()
        .stdout(contains("FROM_PRESET"));
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

#[tokio::test(flavor = "multi_thread")]
async fn run_resume_forwards_stored_conversation_id() {
    // Seed state.toml with a known conversation_id for the mock server,
    // then run `ago run --resume` and assert the request body carries it.
    let mock = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/api/agent/run"))
        .and(wiremock::matchers::body_partial_json(serde_json::json!({
            "conversation_id": "saved-conv-xyz"
        })))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "success": true,
            "output": "RESUMED"
        })))
        .mount(&mock)
        .await;

    let dir = tempdir().unwrap();
    let cfg = dir.path().join("config.toml");
    // state.toml lives next to config.toml in the same dir.
    let state_path = dir.path().join("state.toml");
    std::fs::write(
        &state_path,
        format!(
            "[last_conversation]\n\"{}\" = \"saved-conv-xyz\"\n",
            mock.uri()
        ),
    )
    .unwrap();

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
            "--provider",
            "anthropic",
            "--resume",
            "follow up",
        ])
        .assert()
        .success()
        .stdout(contains("RESUMED"))
        .stderr(contains("resuming conversation saved-conv-xyz"));
}

#[tokio::test(flavor = "multi_thread")]
async fn run_local_invokes_python_subprocess() {
    // Use a Python one-liner as the "interpreter" via AGO_PYTHON, so the
    // test does not depend on the real agent_orchestrator package being
    // installed. The stub reads stdin (which the Rust CLI fills with the
    // JSON request) and emits a canned success payload.
    let py = match std::env::var("PYTHON3").ok().or_else(|| {
        if std::process::Command::new("python3")
            .arg("--version")
            .output()
            .is_ok()
        {
            Some("python3".into())
        } else {
            None
        }
    }) {
        Some(p) => p,
        None => {
            eprintln!("skipping run_local_invokes_python_subprocess: no python3 on PATH");
            return;
        }
    };

    // Build a tiny Python wrapper file that ignores its
    // `-m agent_orchestrator.local_cli` args, reads JSON on stdin, and
    // writes a fixed JSON success payload. We install both the .py and a
    // shim shell script (because AGO_PYTHON is invoked with `-m ...`
    // args; the shim discards them and just runs the script).
    let dir = tempdir().unwrap();
    let stub_py = dir.path().join("ago-stub.py");
    std::fs::write(
        &stub_py,
        "import sys,json\n\
         _ = sys.stdin.read()\n\
         sys.stdout.write(json.dumps({\n\
            \"success\": True,\n\
            \"output\": \"FROM_LOCAL_STUB\",\n\
            \"elapsed_s\": 0.1,\n\
            \"total_input_tokens\": 1,\n\
            \"total_output_tokens\": 2,\n\
            \"total_cost_usd\": 0.0,\n\
         }))\n",
    )
    .unwrap();
    let stub_path = dir.path().join("ago-python-stub.sh");
    let body = format!(
        "#!/bin/sh\n# Drop the `-m agent_orchestrator.local_cli` flags the CLI passes.\nexec {py} {}\n",
        stub_py.display()
    );
    std::fs::write(&stub_path, body).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perm = std::fs::metadata(&stub_path).unwrap().permissions();
        perm.set_mode(0o755);
        std::fs::set_permissions(&stub_path, perm).unwrap();
    }
    #[cfg(not(unix))]
    {
        // Windows can't chmod and shell scripts won't run as-is; the
        // python entry path is exercised on Unix CI and locally.
        eprintln!("skipping run_local on non-unix");
        return;
    }

    let cfg = dir.path().join("config.toml");
    ago()
        .args([
            "--config",
            cfg.to_str().unwrap(),
            "config",
            "set",
            "server",
            "https://orch.example.com",
        ])
        .assert()
        .success();

    ago()
        .env("AGO_TOKEN", "dummy")
        .env("AGO_PYTHON", &stub_path)
        .args([
            "--config",
            cfg.to_str().unwrap(),
            "run",
            "--local",
            "--agent",
            "backend",
            "--model",
            "m",
            "--provider",
            "anthropic",
            "do thing",
        ])
        .assert()
        .success()
        .stdout(contains("FROM_LOCAL_STUB"));
}

#[tokio::test(flavor = "multi_thread")]
async fn jobs_download_extracts_zip_to_dir() {
    use std::io::Write;
    // Build a small ZIP in memory the way the server would produce it.
    let mut buf = Vec::new();
    {
        let mut zw = zip::ZipWriter::new(std::io::Cursor::new(&mut buf));
        let opts = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated);
        zw.start_file("hello.txt", opts).unwrap();
        zw.write_all(b"hi from session").unwrap();
        zw.start_file("nested/deeper.json", opts).unwrap();
        zw.write_all(br#"{"ok":true}"#).unwrap();
        zw.finish().unwrap();
    }
    let mock = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/api/jobs/abc123/download"))
        .respond_with(
            ResponseTemplate::new(200)
                .insert_header("content-type", "application/zip")
                .set_body_bytes(buf.clone()),
        )
        .mount(&mock)
        .await;

    let dir = tempdir().unwrap();
    let cfg = dir.path().join("config.toml");
    let extract_dir = dir.path().join("out");

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
            "jobs",
            "download",
            "abc123",
            "--dir",
            extract_dir.to_str().unwrap(),
        ])
        .assert()
        .success();

    assert_eq!(
        std::fs::read_to_string(extract_dir.join("hello.txt")).unwrap(),
        "hi from session"
    );
    assert_eq!(
        std::fs::read_to_string(extract_dir.join("nested").join("deeper.json")).unwrap(),
        r#"{"ok":true}"#
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn jobs_download_refuses_to_overwrite_without_force() {
    let mock = MockServer::start().await;
    let dir = tempdir().unwrap();
    let cfg = dir.path().join("config.toml");
    let extract_dir = dir.path().join("out");
    // Pre-populate destination so the empty-check trips before we even
    // hit the server (no Mock::given registered for /download).
    std::fs::create_dir_all(&extract_dir).unwrap();
    std::fs::write(extract_dir.join("preexisting.txt"), "keep me").unwrap();

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
            "jobs",
            "download",
            "abc123",
            "--dir",
            extract_dir.to_str().unwrap(),
        ])
        .assert()
        .failure()
        .stderr(contains("not empty"));

    // Pre-existing file must be untouched after the failure.
    assert_eq!(
        std::fs::read_to_string(extract_dir.join("preexisting.txt")).unwrap(),
        "keep me"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn run_resume_falls_through_when_state_empty() {
    let mock = MockServer::start().await;
    // Match any request (no body_partial_json on conversation_id) since
    // --resume with no stored state must NOT send a conversation_id.
    Mock::given(method("POST"))
        .and(path("/api/agent/run"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "success": true,
            "output": "FIRST_RUN"
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
            "--provider",
            "anthropic",
            "--resume",
            "first time",
        ])
        .assert()
        .success()
        .stdout(contains("FIRST_RUN"))
        .stderr(contains("no prior conversation"));
}
