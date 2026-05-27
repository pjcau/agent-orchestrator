use thiserror::Error;

#[derive(Debug, Error)]
pub enum AgoError {
    #[error(
        "no server configured — run `ago config set server <URL>` or `ago login --server <URL>`"
    )]
    NoServer,

    #[error("not authenticated — run `ago login` or set the AGO_TOKEN environment variable")]
    NotAuthenticated,

    #[error("authentication rejected by server (HTTP 401)")]
    AuthRejected,

    #[error("invalid server URL: {0}")]
    InvalidServerUrl(String),

    #[error("server URL must use https:// (only http://localhost and http://127.0.0.1 are allowed for development)")]
    InsecureServerUrl,

    #[error("server returned HTTP {status}: {message}")]
    ServerError { status: u16, message: String },

    #[error("network error: {0}")]
    Network(String),

    #[error("config error: {0}")]
    Config(String),

    #[error("token storage error: {0}")]
    Storage(String),

    #[error("i/o error: {0}")]
    Io(#[from] std::io::Error),

    #[error("invalid token format")]
    InvalidToken,

    #[error("operation cancelled")]
    Cancelled,

    #[error("{0}")]
    Other(String),
}

impl AgoError {
    /// POSIX-style exit codes.
    pub fn exit_code(&self) -> u8 {
        match self {
            AgoError::NoServer | AgoError::NotAuthenticated => 2,
            AgoError::AuthRejected => 4,
            AgoError::InvalidServerUrl(_) | AgoError::InsecureServerUrl => 64, // EX_USAGE
            AgoError::Cancelled => 130,
            AgoError::Network(_) | AgoError::ServerError { .. } => 3,
            _ => 1,
        }
    }
}

pub type Result<T, E = AgoError> = std::result::Result<T, E>;

impl From<reqwest::Error> for AgoError {
    fn from(value: reqwest::Error) -> Self {
        if value.is_timeout() {
            AgoError::Network(format!("timeout: {value}"))
        } else if value.is_connect() {
            AgoError::Network(format!("connect failed: {value}"))
        } else if let Some(status) = value.status() {
            AgoError::ServerError {
                status: status.as_u16(),
                message: value.to_string(),
            }
        } else {
            AgoError::Network(value.to_string())
        }
    }
}

impl From<toml::de::Error> for AgoError {
    fn from(value: toml::de::Error) -> Self {
        AgoError::Config(value.to_string())
    }
}

impl From<toml::ser::Error> for AgoError {
    fn from(value: toml::ser::Error) -> Self {
        AgoError::Config(value.to_string())
    }
}

impl From<keyring::Error> for AgoError {
    fn from(value: keyring::Error) -> Self {
        AgoError::Storage(value.to_string())
    }
}
