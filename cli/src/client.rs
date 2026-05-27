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

/// Request payload for `POST /api/agent/run`.
#[derive(Debug, Serialize)]
pub struct AgentRunRequest<'a> {
    pub agent: &'a str,
    pub task: &'a str,
    pub model: &'a str,
    pub provider: &'a str,
    pub max_steps: u32,
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
        let http = reqwest::Client::builder()
            .user_agent(USER_AGENT_VALUE)
            .default_headers(headers)
            .timeout(DEFAULT_TIMEOUT)
            .https_only(should_force_https(server))
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
}
