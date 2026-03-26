/// Port of `core/rate_limiter.py` — per-provider sliding-window rate limiting.
///
/// The sliding window is 60 seconds.  `acquire()` checks whether a new
/// request would exceed the configured RPM or TPM limits.  `record_usage()`
/// records that a request was made.  This separation mirrors the Python API.
use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use pyo3::prelude::*;
use pyo3::types::PyDict;

/// Sliding-window size in seconds — matches `RateLimiter._WINDOW`.
const WINDOW: f64 = 60.0;

/// Return the current Unix timestamp as `f64`.
fn now_f64() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// Per-provider rate-limit configuration.
#[derive(Clone, Debug)]
struct RateLimitConfig {
    requests_per_minute: i64,
    tokens_per_minute: i64,
}

/// Internal sliding-window state for one provider.
#[derive(Clone, Default, Debug)]
struct ProviderState {
    /// Timestamps of recent requests (within the window).
    request_timestamps: Vec<f64>,
    /// (timestamp, token_count) pairs for recent requests.
    token_timestamps: Vec<(f64, i64)>,
}

impl ProviderState {
    /// Remove entries older than `cutoff` (now - WINDOW).
    fn evict(&mut self, now: f64) {
        let cutoff = now - WINDOW;
        self.request_timestamps.retain(|&ts| ts > cutoff);
        self.token_timestamps.retain(|(ts, _)| *ts > cutoff);
    }

    /// Oldest timestamp across both lists, or `None` if both are empty.
    fn oldest_timestamp(&self) -> Option<f64> {
        let r = self.request_timestamps.first().copied();
        let t = self.token_timestamps.first().map(|(ts, _)| *ts);
        match (r, t) {
            (Some(a), Some(b)) => Some(a.min(b)),
            (Some(a), None) => Some(a),
            (None, Some(b)) => Some(b),
            (None, None) => None,
        }
    }
}

/// Per-provider rate limiter with a 60-second sliding window.
///
/// Constructor argument: list of `(provider_key, rpm, tpm)` tuples.
#[pyclass]
pub struct RustRateLimiter {
    configs: HashMap<String, RateLimitConfig>,
    states: HashMap<String, ProviderState>,
}

#[pymethods]
impl RustRateLimiter {
    /// Build the limiter from a list of `(key, rpm, tpm)` tuples.
    #[new]
    pub fn new(configs: Vec<(String, i64, i64)>) -> Self {
        let mut cfg_map = HashMap::new();
        let mut state_map = HashMap::new();
        for (key, rpm, tpm) in configs {
            cfg_map.insert(
                key.clone(),
                RateLimitConfig {
                    requests_per_minute: rpm,
                    tokens_per_minute: tpm,
                },
            );
            state_map.insert(key, ProviderState::default());
        }
        RustRateLimiter {
            configs: cfg_map,
            states: state_map,
        }
    }

    /// Return `True` if the request is allowed under current rate limits.
    ///
    /// Mirrors `RateLimiter.acquire()` (synchronous here — no `await`):
    /// - Unknown provider: allow by default.
    /// - Evicts stale entries from the window before checking.
    /// - Returns `False` if `requests_used >= rpm` or if the estimated token
    ///   count would exceed the TPM limit.
    pub fn acquire(&mut self, provider_key: &str, estimated_tokens: i64) -> bool {
        let config = match self.configs.get(provider_key) {
            Some(c) => c.clone(),
            None => return true, // unknown provider — allow
        };

        let now = now_f64();
        let state = self.states.entry(provider_key.to_string()).or_default();
        state.evict(now);

        let requests_used = state.request_timestamps.len() as i64;
        let tokens_used: i64 = state.token_timestamps.iter().map(|(_, t)| t).sum();

        if requests_used >= config.requests_per_minute {
            return false;
        }
        if estimated_tokens > 0 && tokens_used + estimated_tokens > config.tokens_per_minute {
            return false;
        }

        true
    }

    /// Record that a request with `tokens` tokens was just made.
    ///
    /// Mirrors `RateLimiter.record_usage()`.  If the provider was not
    /// configured, a default empty state is created automatically.
    pub fn record_usage(&mut self, provider_key: &str, tokens: i64) {
        let now = now_f64();
        let state = self.states.entry(provider_key.to_string()).or_default();
        state.request_timestamps.push(now);
        if tokens > 0 {
            state.token_timestamps.push((now, tokens));
        }
    }

    /// Return current rate-limit status for `provider_key` as a Python dict.
    ///
    /// Keys: `provider_key`, `requests_remaining`, `tokens_remaining`,
    ///       `resets_at`, `is_limited`.
    pub fn get_status(&mut self, py: Python<'_>, provider_key: &str) -> PyResult<PyObject> {
        let now = now_f64();

        let (requests_remaining, tokens_remaining, resets_at, is_limited) =
            if let Some(config) = self.configs.get(provider_key).cloned() {
                let state = self.states.entry(provider_key.to_string()).or_default();
                state.evict(now);

                let requests_used = state.request_timestamps.len() as i64;
                let tokens_used: i64 = state.token_timestamps.iter().map(|(_, t)| t).sum();

                let rr = (config.requests_per_minute - requests_used).max(0);
                let tr = (config.tokens_per_minute - tokens_used).max(0);
                let oldest = state.oldest_timestamp();
                let ra = oldest.map(|o| o + WINDOW).unwrap_or(now);
                let limited = rr == 0 || tr == 0;

                (rr, tr, ra, limited)
            } else {
                // Unknown provider — mirrors Python: 0 remaining, not limited.
                (0, 0, now, false)
            };

        let dict = PyDict::new_bound(py);
        dict.set_item("provider_key", provider_key)?;
        dict.set_item("requests_remaining", requests_remaining)?;
        dict.set_item("tokens_remaining", tokens_remaining)?;
        dict.set_item("resets_at", resets_at)?;
        dict.set_item("is_limited", is_limited)?;
        Ok(dict.into())
    }

    /// Clear all recorded usage for `provider_key`.
    pub fn reset(&mut self, provider_key: &str) {
        if let Some(state) = self.states.get_mut(provider_key) {
            *state = ProviderState::default();
        }
    }
}

impl Default for RustRateLimiter {
    fn default() -> Self {
        Self::new(vec![])
    }
}
