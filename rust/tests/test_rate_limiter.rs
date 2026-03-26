/// Unit tests for RustRateLimiter.
///
/// Behaviour mirrors Python `RateLimiter` exactly.
use agent_orchestrator_rust::*;
use pyo3::prelude::*;

fn limiter(rpm: i64, tpm: i64) -> RustRateLimiter {
    RustRateLimiter::new(vec![("test".to_string(), rpm, tpm)])
}

// ---------------------------------------------------------------------------
// acquire — basic allow/deny
// ---------------------------------------------------------------------------

#[test]
fn test_acquire_allows_when_under_limit() {
    let mut rl = limiter(60, 100_000);
    assert!(rl.acquire("test", 100));
}

#[test]
fn test_acquire_allows_unknown_provider() {
    let mut rl = limiter(60, 100_000);
    // "other" was never configured → allowed by default.
    assert!(rl.acquire("other", 1_000_000));
}

#[test]
fn test_acquire_denies_when_rpm_exhausted() {
    let mut rl = limiter(2, 1_000_000);
    // Record 2 requests to fill the window.
    rl.record_usage("test", 100);
    rl.record_usage("test", 100);
    // Third request should be denied (rpm=2, used=2).
    assert!(!rl.acquire("test", 100));
}

#[test]
fn test_acquire_denies_when_tpm_would_be_exceeded() {
    let mut rl = limiter(1000, 500);
    rl.record_usage("test", 400);
    // Adding 200 more would take total to 600 > 500 → denied.
    assert!(!rl.acquire("test", 200));
}

#[test]
fn test_acquire_allows_when_tpm_exactly_at_limit() {
    let mut rl = limiter(1000, 500);
    rl.record_usage("test", 400);
    // 400 + 100 == 500 — exactly at limit is allowed (Python: > not >=).
    assert!(rl.acquire("test", 100));
}

#[test]
fn test_acquire_ignores_zero_estimated_tokens() {
    let mut rl = limiter(1000, 10);
    rl.record_usage("test", 10); // tpm exhausted
    // estimated_tokens == 0 → token check is skipped entirely.
    assert!(rl.acquire("test", 0));
}

// ---------------------------------------------------------------------------
// record_usage
// ---------------------------------------------------------------------------

#[test]
fn test_record_usage_counts_requests() {
    let mut rl = limiter(3, 1_000_000);
    rl.record_usage("test", 0);
    rl.record_usage("test", 0);
    rl.record_usage("test", 0);
    assert!(!rl.acquire("test", 0)); // rpm=3 exhausted
}

#[test]
fn test_record_usage_zero_tokens_not_added_to_token_list() {
    let mut rl = limiter(1000, 50);
    rl.record_usage("test", 0); // should NOT count against tpm
    // Still have full 50 tokens available.
    assert!(rl.acquire("test", 50));
}

#[test]
fn test_record_usage_auto_creates_state_for_unconfigured_provider() {
    let mut rl = RustRateLimiter::new(vec![]);
    // Should not panic even if provider was never configured.
    rl.record_usage("ghost", 100);
}

// ---------------------------------------------------------------------------
// get_status
// ---------------------------------------------------------------------------

#[test]
fn test_get_status_smoke() {
    pyo3::prepare_freethreaded_python();
    let mut rl = limiter(60, 100_000);
    rl.record_usage("test", 500);

    pyo3::Python::with_gil(|py| {
        let status = rl.get_status(py, "test").unwrap();
        let _ = status; // just verify no panic
    });
}

#[test]
fn test_get_status_unknown_provider_not_limited() {
    pyo3::prepare_freethreaded_python();
    let mut rl = RustRateLimiter::new(vec![]);

    pyo3::Python::with_gil(|py| {
        use pyo3::types::PyDict;
        let obj = rl.get_status(py, "unknown").unwrap();
        let dict = obj.downcast_bound::<PyDict>(py).unwrap();
        let is_limited: bool = dict
            .get_item("is_limited")
            .unwrap()
            .unwrap()
            .extract()
            .unwrap();
        assert!(!is_limited);
    });
}

// ---------------------------------------------------------------------------
// reset
// ---------------------------------------------------------------------------

#[test]
fn test_reset_clears_usage() {
    let mut rl = limiter(2, 1_000_000);
    rl.record_usage("test", 100);
    rl.record_usage("test", 100);
    // Should be denied.
    assert!(!rl.acquire("test", 0));

    rl.reset("test");
    // After reset the window is empty again.
    assert!(rl.acquire("test", 0));
}

#[test]
fn test_reset_unknown_provider_does_not_panic() {
    let mut rl = RustRateLimiter::new(vec![]);
    rl.reset("nonexistent"); // must not panic
}

// ---------------------------------------------------------------------------
// Multiple providers are independent
// ---------------------------------------------------------------------------

#[test]
fn test_multiple_providers_are_independent() {
    let mut rl = RustRateLimiter::new(vec![
        ("a".to_string(), 1, 1_000_000),
        ("b".to_string(), 1_000, 1_000_000),
    ]);
    // Exhaust provider "a".
    rl.record_usage("a", 0);
    assert!(!rl.acquire("a", 0));
    // "b" should be unaffected.
    assert!(rl.acquire("b", 0));
}
