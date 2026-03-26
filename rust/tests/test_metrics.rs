/// Unit tests for RustMetricsRegistry.
///
/// Covers Counter, Gauge, Histogram, percentile math, and Prometheus export.
use agent_orchestrator_rust::*;
use pyo3::prelude::*;

// ---------------------------------------------------------------------------
// Counter
// ---------------------------------------------------------------------------

#[test]
fn test_counter_starts_at_zero() {
    let reg = RustMetricsRegistry::new();
    assert_eq!(reg.counter_get("hits", None), 0.0);
}

#[test]
fn test_counter_increment() {
    let mut reg = RustMetricsRegistry::new();
    reg.counter_inc("hits", None, 1.0).unwrap();
    reg.counter_inc("hits", None, 2.0).unwrap();
    assert_eq!(reg.counter_get("hits", None), 3.0);
}

#[test]
fn test_counter_negative_increment_errors() {
    let mut reg = RustMetricsRegistry::new();
    let err = reg.counter_inc("hits", None, -1.0).unwrap_err();
    assert!(err.to_string().contains("non-negative"));
}

#[test]
fn test_counter_with_labels() {
    let mut reg = RustMetricsRegistry::new();
    let labels = Some([("agent".to_string(), "backend".to_string())].into_iter().collect());
    reg.counter_inc("tasks_total", labels.clone(), 5.0).unwrap();
    assert_eq!(reg.counter_get("tasks_total", labels), 5.0);
}

#[test]
fn test_counter_different_labels_are_independent() {
    let mut reg = RustMetricsRegistry::new();
    let la = Some([("env".to_string(), "prod".to_string())].into_iter().collect());
    let lb = Some([("env".to_string(), "dev".to_string())].into_iter().collect());
    reg.counter_inc("req", la.clone(), 10.0).unwrap();
    reg.counter_inc("req", lb.clone(), 3.0).unwrap();
    assert_eq!(reg.counter_get("req", la), 10.0);
    assert_eq!(reg.counter_get("req", lb), 3.0);
}

// ---------------------------------------------------------------------------
// Gauge
// ---------------------------------------------------------------------------

#[test]
fn test_gauge_starts_at_zero() {
    let reg = RustMetricsRegistry::new();
    assert_eq!(reg.gauge_get("mem", None), 0.0);
}

#[test]
fn test_gauge_set() {
    let mut reg = RustMetricsRegistry::new();
    reg.gauge_set("mem", None, 42.0);
    assert_eq!(reg.gauge_get("mem", None), 42.0);
}

#[test]
fn test_gauge_inc() {
    let mut reg = RustMetricsRegistry::new();
    reg.gauge_set("mem", None, 10.0);
    reg.gauge_inc("mem", None, 5.0);
    assert_eq!(reg.gauge_get("mem", None), 15.0);
}

#[test]
fn test_gauge_dec() {
    let mut reg = RustMetricsRegistry::new();
    reg.gauge_set("mem", None, 10.0);
    reg.gauge_dec("mem", None, 3.0);
    assert_eq!(reg.gauge_get("mem", None), 7.0);
}

#[test]
fn test_gauge_can_go_negative() {
    let mut reg = RustMetricsRegistry::new();
    reg.gauge_dec("temp", None, 5.0);
    assert_eq!(reg.gauge_get("temp", None), -5.0);
}

// ---------------------------------------------------------------------------
// Histogram — observe / percentile
// ---------------------------------------------------------------------------

#[test]
fn test_histogram_empty_percentile_returns_zero() {
    let reg = RustMetricsRegistry::new();
    assert_eq!(reg.histogram_get_percentile("lat", None, 50.0), 0.0);
}

#[test]
fn test_histogram_single_observation_all_percentiles() {
    let mut reg = RustMetricsRegistry::new();
    reg.histogram_observe("lat", None, 7.0);
    assert_eq!(reg.histogram_get_percentile("lat", None, 0.0), 7.0);
    assert_eq!(reg.histogram_get_percentile("lat", None, 50.0), 7.0);
    assert_eq!(reg.histogram_get_percentile("lat", None, 100.0), 7.0);
}

#[test]
fn test_histogram_p50_interpolation() {
    let mut reg = RustMetricsRegistry::new();
    for v in [1.0, 2.0, 3.0, 4.0, 5.0] {
        reg.histogram_observe("lat", None, v);
    }
    // p50: index = 0.5 * 4 = 2.0 → sorted[2] = 3.0 (exact)
    let p50 = reg.histogram_get_percentile("lat", None, 50.0);
    assert!((p50 - 3.0).abs() < 1e-9);
}

#[test]
fn test_histogram_p95_interpolation() {
    let mut reg = RustMetricsRegistry::new();
    for v in [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0] {
        reg.histogram_observe("lat", None, v);
    }
    // p95: index = 0.95 * 9 = 8.55 → lower=8, upper=9
    // fraction=0.55 → sorted[8]*(0.45) + sorted[9]*(0.55) = 9*0.45 + 10*0.55
    // = 4.05 + 5.5 = 9.55
    let p95 = reg.histogram_get_percentile("lat", None, 95.0);
    assert!((p95 - 9.55).abs() < 1e-9);
}

#[test]
fn test_histogram_p0_returns_min() {
    let mut reg = RustMetricsRegistry::new();
    for v in [5.0, 1.0, 3.0] {
        reg.histogram_observe("lat", None, v);
    }
    assert_eq!(reg.histogram_get_percentile("lat", None, 0.0), 1.0);
}

#[test]
fn test_histogram_p100_returns_max() {
    let mut reg = RustMetricsRegistry::new();
    for v in [5.0, 1.0, 3.0] {
        reg.histogram_observe("lat", None, v);
    }
    assert_eq!(reg.histogram_get_percentile("lat", None, 100.0), 5.0);
}

#[test]
fn test_histogram_rolling_window_evicts_oldest() {
    let mut reg = RustMetricsRegistry::new();
    // Fill 10_000 observations (window boundary).
    for i in 0..10_000 {
        reg.histogram_observe("lat", None, i as f64);
    }
    // Adding one more should evict the first (0.0).
    reg.histogram_observe("lat", None, 99999.0);
    // p0 should now be 1.0 (oldest remaining) not 0.0.
    let p0 = reg.histogram_get_percentile("lat", None, 0.0);
    assert!((p0 - 1.0).abs() < 1e-9);
}

// ---------------------------------------------------------------------------
// Prometheus export format
// ---------------------------------------------------------------------------

#[test]
fn test_export_prometheus_counter() {
    let mut reg = RustMetricsRegistry::new();
    reg.counter_inc("reqs", None, 5.0).unwrap();
    let output = reg.export_prometheus();
    assert!(output.contains("# TYPE reqs counter"));
    assert!(output.contains("reqs 5"));
    assert!(output.ends_with('\n'));
}

#[test]
fn test_export_prometheus_gauge() {
    let mut reg = RustMetricsRegistry::new();
    reg.gauge_set("mem_bytes", None, 1024.0);
    let output = reg.export_prometheus();
    assert!(output.contains("# TYPE mem_bytes gauge"));
    assert!(output.contains("mem_bytes 1024"));
}

#[test]
fn test_export_prometheus_histogram() {
    let mut reg = RustMetricsRegistry::new();
    reg.histogram_observe("latency", None, 0.1);
    reg.histogram_observe("latency", None, 0.2);
    let output = reg.export_prometheus();
    assert!(output.contains("# TYPE latency histogram"));
    assert!(output.contains("latency_count"));
    assert!(output.contains("latency_sum"));
}

#[test]
fn test_export_prometheus_label_format() {
    let mut reg = RustMetricsRegistry::new();
    let labels = Some(
        [
            ("agent".to_string(), "backend".to_string()),
            ("status".to_string(), "ok".to_string()),
        ]
        .into_iter()
        .collect(),
    );
    reg.counter_inc("tasks", labels, 1.0).unwrap();
    let output = reg.export_prometheus();
    // Labels must appear in sorted order with double-quoted values.
    assert!(output.contains(r#"agent="backend""#));
    assert!(output.contains(r#"status="ok""#));
}

#[test]
fn test_export_prometheus_empty_registry() {
    let reg = RustMetricsRegistry::new();
    let output = reg.export_prometheus();
    assert!(output.is_empty());
}

#[test]
fn test_export_prometheus_metrics_sorted_by_name() {
    let mut reg = RustMetricsRegistry::new();
    reg.counter_inc("z_metric", None, 1.0).unwrap();
    reg.counter_inc("a_metric", None, 1.0).unwrap();
    let output = reg.export_prometheus();
    let a_pos = output.find("a_metric").unwrap();
    let z_pos = output.find("z_metric").unwrap();
    assert!(a_pos < z_pos);
}

// ---------------------------------------------------------------------------
// get_all (smoke via PyO3 GIL)
// ---------------------------------------------------------------------------

#[test]
fn test_get_all_smoke() {
    pyo3::prepare_freethreaded_python();
    let mut reg = RustMetricsRegistry::new();
    reg.counter_inc("hits", None, 1.0).unwrap();
    reg.gauge_set("temp", None, 37.0);
    reg.histogram_observe("latency", None, 0.5);

    pyo3::Python::with_gil(|py| {
        let all = reg.get_all(py).unwrap();
        let _ = all;
    });
}
