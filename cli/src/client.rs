use crate::error::{AgoError, Result};
use eventsource_stream::Eventsource;
use futures_util::stream::{Stream, StreamExt};
use reqwest::header::{HeaderMap, HeaderValue, ACCEPT, USER_AGENT};
use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Serialize};
use std::pin::Pin;
use std::time::Duration;

const DEFAULT_TIMEOUT: Duration = Duration::from_secs(30);
const RUN_TIMEOUT: Duration = Duration::from_secs(600);
const USER_AGENT_VALUE: &str = concat!("ago-cli/", env!("CARGO_PKG_VERSION"));

#[derive(Debug, Deserialize)]
pub struct WhoamiResponse {
    pub name: Option<String>,
    pub email: Option<String>,
    pub role: Option<String>,
    pub provider: Option<String>,
    pub server_version: Option<String>,
}

/// Request payload for `POST /api/prompt` — direct LLM completion, no agent loop.
#[derive(Debug, Serialize)]
pub struct PromptRequest<'a> {
    pub prompt: &'a str,
    pub model: &'a str,
    pub provider: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub conversation_id: Option<&'a str>,
    /// Cacheable prefix containing the @file / @dir expansion. When the
    /// provider supports it (OpenRouter and Anthropic in v0.4.1+), the
    /// server marks this block with `cache_control: ephemeral` so repeated
    /// turns pay 10–50% of input cost on the repeated bytes.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cache_context: Option<&'a str>,
}

/// Loose response wrapper for `POST /api/prompt`. Keeps the full JSON so
/// future server fields (RAG metadata, citations) flow through unchanged.
#[derive(Debug, Clone)]
pub struct PromptResponse {
    pub raw: serde_json::Value,
}

impl PromptResponse {
    pub fn success(&self) -> Option<bool> {
        self.raw.get("success")?.as_bool()
    }
    pub fn output(&self) -> Option<&str> {
        self.raw.get("output")?.as_str()
    }
    pub fn error(&self) -> Option<&str> {
        self.raw.get("error")?.as_str()
    }
    pub fn elapsed_s(&self) -> Option<f64> {
        self.raw.get("elapsed_s")?.as_f64()
    }
    pub fn input_tokens(&self) -> Option<u64> {
        self.raw.get("usage")?.get("input_tokens")?.as_u64()
    }
    pub fn output_tokens(&self) -> Option<u64> {
        self.raw.get("usage")?.get("output_tokens")?.as_u64()
    }
    pub fn cost_usd(&self) -> Option<f64> {
        self.raw.get("usage")?.get("cost_usd")?.as_f64()
    }
}

/// Request payload for `POST /api/agent/run` and `POST /api/cli/v1/run`.
#[derive(Debug, Serialize)]
pub struct AgentRunRequest<'a> {
    pub agent: &'a str,
    pub task: &'a str,
    pub model: &'a str,
    pub provider: &'a str,
    pub max_steps: u32,
    /// Optional conversation thread ID — sent by `ago chat` so the server's
    /// ConversationManager restores prior turns across the same session.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub conversation_id: Option<&'a str>,
    /// Cacheable prefix (see `PromptRequest::cache_context`). When set on
    /// an agent run, the server forwards it as a cache marker to the
    /// underlying provider so multi-turn agent loops re-use the prefix.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cache_context: Option<&'a str>,
}

/// Response from `POST /api/agent/run`.
///
/// The dashboard returns a free-form dict; we hold the full JSON and expose
/// helpers for the fields the CLI cares about in human-render mode.
#[derive(Debug, Clone)]
pub struct AgentRunResponse {
    pub raw: serde_json::Value,
}

/// A single Server-Sent Event from `/api/cli/v1/run`.
#[derive(Debug, Clone)]
pub struct RunEvent {
    pub event: String,
    pub data: serde_json::Value,
}

/// Response from `POST /api/cli/v1/auth/device` — start of an RFC 8628 flow.
#[derive(Debug, Clone, Deserialize)]
pub struct DeviceAuthResponse {
    pub device_code: String,
    pub user_code: String,
    pub verification_uri: String,
    pub verification_uri_complete: Option<String>,
    pub expires_in: u64,
    pub interval: u64,
}

/// Outcome of polling `POST /api/cli/v1/auth/token`.
#[derive(Debug, Clone)]
pub enum DevicePollOutcome {
    Pending,
    SlowDown,
    Approved { access_token: String },
    Denied,
    Expired,
    Unknown,
}

impl AgentRunResponse {
    pub fn success(&self) -> Option<bool> {
        self.raw.get("success")?.as_bool()
    }
    pub fn output(&self) -> Option<&str> {
        self.raw.get("output")?.as_str()
    }
    pub fn error(&self) -> Option<&str> {
        self.raw.get("error")?.as_str()
    }
    pub fn elapsed_s(&self) -> Option<f64> {
        self.raw.get("elapsed_s")?.as_f64()
    }
    pub fn total_input_tokens(&self) -> Option<u64> {
        self.raw.get("total_input_tokens")?.as_u64()
    }
    pub fn total_output_tokens(&self) -> Option<u64> {
        self.raw.get("total_output_tokens")?.as_u64()
    }
    pub fn total_cost_usd(&self) -> Option<f64> {
        self.raw.get("total_cost_usd")?.as_f64()
    }
}

#[derive(Debug, Clone)]
pub struct ApiClient {
    base: String,
    http: reqwest::Client,
}

impl ApiClient {
    pub fn new(server: &str, token: Option<SecretString>) -> Result<Self> {
        crate::config::validate_server_url(server)?;
        let mut headers = HeaderMap::new();
        headers.insert(USER_AGENT, HeaderValue::from_static(USER_AGENT_VALUE));
        headers.insert(ACCEPT, HeaderValue::from_static("application/json"));
        if let Some(t) = token.as_ref() {
            let mut v =
                HeaderValue::from_str(t.expose_secret()).map_err(|_| AgoError::InvalidToken)?;
            v.set_sensitive(true);
            headers.insert("X-API-Key", v);
        }
        let mut builder = reqwest::Client::builder()
            .user_agent(USER_AGENT_VALUE)
            .default_headers(headers)
            .timeout(DEFAULT_TIMEOUT)
            .https_only(should_force_https(server));
        // Dev-only escape hatch for self-signed local dashboards (e.g. the
        // docker-compose dashboard that ships with the orchestrator). Opt-in
        // per process via env var — never persisted in any config file, and
        // a single-line warning is emitted to stderr so it cannot fly under
        // the radar in CI logs.
        if std::env::var("AGO_INSECURE").is_ok_and(|v| !v.is_empty() && v != "0") {
            eprintln!(
                "\x1b[33mwarning:\x1b[0m AGO_INSECURE=1 — TLS certificate validation is disabled. Use only against trusted local servers."
            );
            builder = builder.danger_accept_invalid_certs(true);
        }
        let http = builder
            .build()
            .map_err(|e| AgoError::Network(e.to_string()))?;
        Ok(Self {
            base: server.trim_end_matches('/').to_string(),
            http,
        })
    }

    fn url(&self, path: &str) -> String {
        format!("{}{}", self.base, path)
    }

    /// POST /api/cli/v1/run — streaming single-agent task execution.
    ///
    /// Returns a `Stream` of `RunEvent` items. The stream ends after the
    /// `complete` event is delivered (or on error). The CLI is responsible
    /// for inspecting `RunEvent::Complete` to determine final success.
    pub async fn agent_run_stream(
        &self,
        req: &AgentRunRequest<'_>,
    ) -> Result<Pin<Box<dyn Stream<Item = Result<RunEvent>> + Send>>> {
        let resp = self
            .http
            .post(self.url("/api/cli/v1/run"))
            .timeout(RUN_TIMEOUT)
            .header(ACCEPT, "text/event-stream")
            .json(req)
            .send()
            .await?;
        let status = resp.status();
        if status == reqwest::StatusCode::UNAUTHORIZED {
            return Err(AgoError::AuthRejected);
        }
        if !status.is_success() {
            let body = resp.text().await.unwrap_or_default();
            return Err(AgoError::ServerError {
                status: status.as_u16(),
                message: body,
            });
        }
        let stream = resp.bytes_stream().eventsource().map(|item| match item {
            Ok(event) => {
                let data: serde_json::Value = serde_json::from_str(&event.data)
                    .unwrap_or(serde_json::Value::String(event.data));
                Ok(RunEvent {
                    event: event.event,
                    data,
                })
            }
            Err(e) => Err(AgoError::Network(format!("sse parse error: {e}"))),
        });
        Ok(Box::pin(stream))
    }

    /// POST /api/cli/v1/auth/device-start — start an RFC 8628 device flow.
    ///
    /// This endpoint is anonymous on the server side — the trust boundary is
    /// the browser-side approval step, which still requires a valid
    /// dashboard session.
    pub async fn device_authorization(&self) -> Result<DeviceAuthResponse> {
        let resp = self
            .http
            .post(self.url("/api/cli/v1/auth/device-start"))
            .json(&serde_json::json!({}))
            .send()
            .await?;
        let status = resp.status();
        if status == reqwest::StatusCode::UNAUTHORIZED {
            return Err(AgoError::AuthRejected);
        }
        if !status.is_success() {
            let body = resp.text().await.unwrap_or_default();
            return Err(AgoError::ServerError {
                status: status.as_u16(),
                message: body,
            });
        }
        let parsed: DeviceAuthResponse = resp.json().await.map_err(AgoError::from)?;
        Ok(parsed)
    }

    /// POST /api/cli/v1/auth/token — poll once for the access token.
    ///
    /// Maps the RFC 8628 error codes to a `DevicePollOutcome` enum so the
    /// caller can drive a polling loop without re-parsing the HTTP layer.
    pub async fn device_token(&self, device_code: &str) -> Result<DevicePollOutcome> {
        let resp = self
            .http
            .post(self.url("/api/cli/v1/auth/device-poll"))
            .json(&serde_json::json!({ "device_code": device_code }))
            .send()
            .await?;
        let status = resp.status();
        let body_text = resp.text().await.map_err(AgoError::from)?;
        if status == reqwest::StatusCode::UNAUTHORIZED {
            return Err(AgoError::AuthRejected);
        }
        if status == reqwest::StatusCode::NOT_FOUND {
            return Ok(DevicePollOutcome::Unknown);
        }
        if status.is_success() {
            #[derive(Deserialize)]
            struct Body {
                access_token: String,
            }
            let parsed: Body =
                serde_json::from_str(&body_text).map_err(|e| AgoError::ServerError {
                    status: status.as_u16(),
                    message: format!("malformed JSON response: {e}"),
                })?;
            return Ok(DevicePollOutcome::Approved {
                access_token: parsed.access_token,
            });
        }
        // Server returns 400 with `{error: "authorization_pending|...}`.
        #[derive(Deserialize)]
        struct Err400 {
            error: String,
        }
        let parsed: Err400 =
            serde_json::from_str(&body_text).map_err(|e| AgoError::ServerError {
                status: status.as_u16(),
                message: format!("malformed JSON response: {e}"),
            })?;
        Ok(match parsed.error.as_str() {
            "authorization_pending" => DevicePollOutcome::Pending,
            "slow_down" => DevicePollOutcome::SlowDown,
            "access_denied" => DevicePollOutcome::Denied,
            "expired_token" => DevicePollOutcome::Expired,
            other => {
                return Err(AgoError::ServerError {
                    status: status.as_u16(),
                    message: format!("unexpected device-flow error: {other}"),
                });
            }
        })
    }

    /// POST /api/prompt — direct LLM completion (no agent loop, no tools).
    ///
    /// Suitable for chat-style models. The response shape is whatever
    /// `dashboard.agent_runtime_router.prompt` returns: success / output /
    /// usage{input_tokens, output_tokens, cost_usd} / elapsed_s.
    pub async fn prompt(&self, req: &PromptRequest<'_>) -> Result<PromptResponse> {
        let resp = self
            .http
            .post(self.url("/api/prompt"))
            .timeout(RUN_TIMEOUT)
            .json(req)
            .send()
            .await?;
        let status = resp.status();
        if status == reqwest::StatusCode::UNAUTHORIZED {
            return Err(AgoError::AuthRejected);
        }
        let body_text = resp.text().await.map_err(AgoError::from)?;
        if !status.is_success() {
            return Err(AgoError::ServerError {
                status: status.as_u16(),
                message: body_text,
            });
        }
        let raw: serde_json::Value =
            serde_json::from_str(&body_text).map_err(|e| AgoError::ServerError {
                status: status.as_u16(),
                message: format!("malformed JSON response: {e}"),
            })?;
        Ok(PromptResponse { raw })
    }

    /// GET /api/jobs/list — return all recorded job sessions.
    pub async fn jobs_list(&self) -> Result<serde_json::Value> {
        self.get_json("/api/jobs/list").await
    }

    /// GET /api/jobs/{session_id} — return the records of a single session.
    pub async fn jobs_show(&self, session_id: &str) -> Result<serde_json::Value> {
        let path = format!("/api/jobs/{}", url_encode(session_id));
        self.get_json(&path).await
    }

    /// POST /api/team/{job_id}/cancel — request cancellation of an active team run.
    pub async fn job_cancel(&self, job_id: &str) -> Result<serde_json::Value> {
        let resp = self
            .http
            .post(self.url(&format!("/api/team/{}/cancel", url_encode(job_id))))
            .send()
            .await?;
        let status = resp.status();
        if status == reqwest::StatusCode::UNAUTHORIZED {
            return Err(AgoError::AuthRejected);
        }
        if status == reqwest::StatusCode::NOT_FOUND {
            return Err(AgoError::Other(format!("job {job_id} not found")));
        }
        if !status.is_success() {
            let body = resp.text().await.unwrap_or_default();
            return Err(AgoError::ServerError {
                status: status.as_u16(),
                message: body,
            });
        }
        let parsed: serde_json::Value = resp.json().await.map_err(AgoError::from)?;
        Ok(parsed)
    }

    async fn get_json(&self, path: &str) -> Result<serde_json::Value> {
        let resp = self.http.get(self.url(path)).send().await?;
        let status = resp.status();
        if status == reqwest::StatusCode::UNAUTHORIZED {
            return Err(AgoError::AuthRejected);
        }
        if status == reqwest::StatusCode::NOT_FOUND {
            return Err(AgoError::Other(format!("not found: {path}")));
        }
        if !status.is_success() {
            let body = resp.text().await.unwrap_or_default();
            return Err(AgoError::ServerError {
                status: status.as_u16(),
                message: body,
            });
        }
        resp.json().await.map_err(AgoError::from)
    }

    /// POST /api/agent/run — blocking single-agent task execution.
    ///
    /// Phase 2a uses the existing dashboard endpoint as-is. A dedicated
    /// streaming endpoint at `/api/cli/v1/run` lands in Phase 2b.
    pub async fn agent_run(&self, req: &AgentRunRequest<'_>) -> Result<AgentRunResponse> {
        let resp = self
            .http
            .post(self.url("/api/agent/run"))
            .timeout(RUN_TIMEOUT)
            .json(req)
            .send()
            .await?;
        let status = resp.status();
        if status == reqwest::StatusCode::UNAUTHORIZED {
            return Err(AgoError::AuthRejected);
        }
        let body_text = resp.text().await.map_err(AgoError::from)?;
        if !status.is_success() {
            return Err(AgoError::ServerError {
                status: status.as_u16(),
                message: body_text,
            });
        }
        let raw: serde_json::Value =
            serde_json::from_str(&body_text).map_err(|e| AgoError::ServerError {
                status: status.as_u16(),
                message: format!("malformed JSON response: {e}"),
            })?;
        Ok(AgentRunResponse { raw })
    }

    /// GET /api/cli/v1/whoami — used by `ago login` to validate and by `ago whoami`.
    pub async fn whoami(&self) -> Result<WhoamiResponse> {
        let resp = self.http.get(self.url("/api/cli/v1/whoami")).send().await?;
        let status = resp.status();
        if status == reqwest::StatusCode::UNAUTHORIZED {
            return Err(AgoError::AuthRejected);
        }
        if !status.is_success() {
            let body = resp.text().await.unwrap_or_default();
            return Err(AgoError::ServerError {
                status: status.as_u16(),
                message: body,
            });
        }
        let parsed: WhoamiResponse = resp.json().await.map_err(|e| AgoError::ServerError {
            status: status.as_u16(),
            message: format!("malformed JSON response: {e}"),
        })?;
        Ok(parsed)
    }
}

fn url_encode(s: &str) -> String {
    url::form_urlencoded::byte_serialize(s.as_bytes()).collect()
}

fn should_force_https(server: &str) -> bool {
    !(server.starts_with("http://localhost")
        || server.starts_with("http://127.0.0.1")
        || server.starts_with("http://[::1]"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use wiremock::matchers::{header, method, path};
    use wiremock::{Mock, MockServer, ResponseTemplate};

    #[test]
    fn force_https_for_remote() {
        assert!(should_force_https("https://example.com"));
        assert!(should_force_https("http://example.com"));
        assert!(!should_force_https("http://localhost:5005"));
        assert!(!should_force_https("http://127.0.0.1:8080"));
    }

    #[tokio::test]
    async fn whoami_success() {
        let server = MockServer::start().await;
        Mock::given(method("GET"))
            .and(path("/api/cli/v1/whoami"))
            .and(header("X-API-Key", "secret"))
            .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
                "name": "alice",
                "email": "alice@example.com",
                "role": "developer",
                "server_version": "0.2.0"
            })))
            .expect(1)
            .mount(&server)
            .await;

        let client = ApiClient::new(
            &server.uri(),
            Some(SecretString::from("secret".to_string())),
        )
        .unwrap();
        let me = client.whoami().await.unwrap();
        assert_eq!(me.name.as_deref(), Some("alice"));
        assert_eq!(me.role.as_deref(), Some("developer"));
    }

    #[tokio::test]
    async fn whoami_unauthorized_maps_to_auth_rejected() {
        let server = MockServer::start().await;
        Mock::given(method("GET"))
            .and(path("/api/cli/v1/whoami"))
            .respond_with(ResponseTemplate::new(401).set_body_string("nope"))
            .mount(&server)
            .await;

        let client =
            ApiClient::new(&server.uri(), Some(SecretString::from("bad".to_string()))).unwrap();
        let err = client.whoami().await.unwrap_err();
        assert!(matches!(err, AgoError::AuthRejected));
    }

    #[tokio::test]
    async fn whoami_500_maps_to_server_error() {
        let server = MockServer::start().await;
        Mock::given(method("GET"))
            .and(path("/api/cli/v1/whoami"))
            .respond_with(ResponseTemplate::new(500).set_body_string("boom"))
            .mount(&server)
            .await;

        let client =
            ApiClient::new(&server.uri(), Some(SecretString::from("k".to_string()))).unwrap();
        let err = client.whoami().await.unwrap_err();
        match err {
            AgoError::ServerError { status, .. } => assert_eq!(status, 500),
            other => panic!("unexpected: {other:?}"),
        }
    }

    #[test]
    fn rejects_remote_http() {
        let err = ApiClient::new("http://evil.com", None).unwrap_err();
        assert!(matches!(err, AgoError::InsecureServerUrl));
    }

    #[tokio::test]
    async fn device_authorization_parses_pair() {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/api/cli/v1/auth/device-start"))
            .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
                "device_code": "DEV-CODE",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://x.io/api/cli/v1/auth/device",
                "verification_uri_complete": "https://x.io/api/cli/v1/auth/device?user_code=ABCD-EFGH",
                "expires_in": 600,
                "interval": 5
            })))
            .mount(&server)
            .await;

        let client = ApiClient::new(&server.uri(), None).unwrap();
        let auth = client.device_authorization().await.unwrap();
        assert_eq!(auth.device_code, "DEV-CODE");
        assert_eq!(auth.user_code, "ABCD-EFGH");
        assert_eq!(auth.interval, 5);
    }

    #[tokio::test]
    async fn device_token_maps_pending() {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/api/cli/v1/auth/device-poll"))
            .respond_with(
                ResponseTemplate::new(400)
                    .set_body_json(serde_json::json!({ "error": "authorization_pending" })),
            )
            .mount(&server)
            .await;
        let client = ApiClient::new(&server.uri(), None).unwrap();
        let outcome = client.device_token("dc").await.unwrap();
        assert!(matches!(outcome, DevicePollOutcome::Pending));
    }

    #[tokio::test]
    async fn device_token_maps_slow_down() {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/api/cli/v1/auth/device-poll"))
            .respond_with(
                ResponseTemplate::new(400)
                    .set_body_json(serde_json::json!({ "error": "slow_down" })),
            )
            .mount(&server)
            .await;
        let client = ApiClient::new(&server.uri(), None).unwrap();
        let outcome = client.device_token("dc").await.unwrap();
        assert!(matches!(outcome, DevicePollOutcome::SlowDown));
    }

    #[tokio::test]
    async fn device_token_maps_denied() {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/api/cli/v1/auth/device-poll"))
            .respond_with(
                ResponseTemplate::new(400)
                    .set_body_json(serde_json::json!({ "error": "access_denied" })),
            )
            .mount(&server)
            .await;
        let client = ApiClient::new(&server.uri(), None).unwrap();
        let outcome = client.device_token("dc").await.unwrap();
        assert!(matches!(outcome, DevicePollOutcome::Denied));
    }

    #[tokio::test]
    async fn device_token_maps_expired() {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/api/cli/v1/auth/device-poll"))
            .respond_with(
                ResponseTemplate::new(400)
                    .set_body_json(serde_json::json!({ "error": "expired_token" })),
            )
            .mount(&server)
            .await;
        let client = ApiClient::new(&server.uri(), None).unwrap();
        let outcome = client.device_token("dc").await.unwrap();
        assert!(matches!(outcome, DevicePollOutcome::Expired));
    }

    #[tokio::test]
    async fn device_token_maps_unknown_device_code() {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/api/cli/v1/auth/device-poll"))
            .respond_with(
                ResponseTemplate::new(404)
                    .set_body_json(serde_json::json!({ "error": "unknown_device_code" })),
            )
            .mount(&server)
            .await;
        let client = ApiClient::new(&server.uri(), None).unwrap();
        let outcome = client.device_token("dc").await.unwrap();
        assert!(matches!(outcome, DevicePollOutcome::Unknown));
    }

    #[tokio::test]
    async fn device_token_maps_approved() {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/api/cli/v1/auth/device-poll"))
            .respond_with(
                ResponseTemplate::new(200)
                    .set_body_json(serde_json::json!({ "access_token": "ago_eph_xyz" })),
            )
            .mount(&server)
            .await;
        let client = ApiClient::new(&server.uri(), None).unwrap();
        let outcome = client.device_token("dc").await.unwrap();
        match outcome {
            DevicePollOutcome::Approved { access_token } => assert_eq!(access_token, "ago_eph_xyz"),
            other => panic!("unexpected: {other:?}"),
        }
    }
}
