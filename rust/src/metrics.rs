/// Port of `core/metrics.py` — Counter, Gauge, Histogram, and MetricsRegistry.
///
/// The Prometheus export format produced by `export_prometheus()` is byte-for-byte
/// identical to the Python implementation for the same set of metrics.
use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use pyo3::prelude::*;
use pyo3::types::PyDict;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Build the metric key used internally to distinguish label combinations.
fn metric_key_exact(name: &str, labels: &Option<HashMap<String, String>>) -> String {
    match labels {
        None => name.to_string(),
        Some(m) if m.is_empty() => name.to_string(),
        Some(m) => {
            let mut pairs: Vec<(&String, &String)> = m.iter().collect();
            pairs.sort_by_key(|(k, _)| k.as_str());
            let label_part: Vec<String> =
                pairs.iter().map(|(k, v)| format!("{}={}", k, v)).collect();
            format!("{}{{{}}}", name, label_part.join(","))
        }
    }
}

/// Format label set in Prometheus exposition syntax.
///
/// Mirrors Python `_format_labels(labels)`:
/// `{k1="v1", k2="v2"}` (sorted, space after comma).
fn format_labels(labels: &HashMap<String, String>) -> String {
    if labels.is_empty() {
        return String::new();
    }
    let mut pairs: Vec<(&String, &String)> = labels.iter().collect();
    pairs.sort_by_key(|(k, _)| k.as_str());
    let parts: Vec<String> = pairs
        .iter()
        .map(|(k, v)| format!("{}=\"{}\"", k, v))
        .collect();
    format!("{{{}}}", parts.join(", "))
}

/// Current Unix timestamp in milliseconds (integer).
fn timestamp_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

// ---------------------------------------------------------------------------
// Internal metric types
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
struct CounterData {
    name: String,
    description: String,
    labels: HashMap<String, String>,
    value: f64,
}

#[derive(Debug, Clone)]
struct GaugeData {
    name: String,
    description: String,
    labels: HashMap<String, String>,
    value: f64,
}

#[derive(Debug, Clone)]
struct HistogramData {
    name: String,
    description: String,
    labels: HashMap<String, String>,
    /// Rolling window of at most `max_observations` recent values.
    observations: Vec<f64>,
    max_observations: usize,
    /// Monotonically increasing sum of ALL observed values.
    sum: f64,
    /// Monotonically increasing count of ALL observed values.
    count: usize,
}

impl HistogramData {
    fn new(
        name: String,
        description: String,
        labels: HashMap<String, String>,
        max_observations: usize,
    ) -> Self {
        HistogramData {
            name,
            description,
            labels,
            observations: Vec::new(),
            max_observations,
            sum: 0.0,
            count: 0,
        }
    }

    fn observe(&mut self, value: f64) {
        if self.observations.len() >= self.max_observations {
            self.observations.remove(0); // mirrors Python list.pop(0)
        }
        self.observations.push(value);
        self.sum += value;
        self.count += 1;
    }

    /// Linear-interpolation percentile.  Returns 0.0 for empty windows.
    ///
    /// Mirrors Python `Histogram.get_percentile()`.
    fn get_percentile(&self, p: f64) -> f64 {
        if self.observations.is_empty() {
            return 0.0;
        }
        let mut sorted = self.observations.clone();
        sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        let len = sorted.len();

        if p <= 0.0 {
            return sorted[0];
        }
        if p >= 100.0 {
            return sorted[len - 1];
        }

        let index = (p / 100.0) * (len as f64 - 1.0);
        let lower = index.floor() as usize;
        let upper = index.ceil() as usize;

        if lower == upper {
            return sorted[lower];
        }
        let fraction = index - lower as f64;
        sorted[lower] * (1.0 - fraction) + sorted[upper] * fraction
    }
}

// ---------------------------------------------------------------------------
// MetricsRegistry
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
enum MetricEntry {
    Counter(CounterData),
    Gauge(GaugeData),
    Histogram(HistogramData),
}

/// Central registry for Counter, Gauge, and Histogram metrics.
///
/// Mirrors Python `MetricsRegistry`.  Metrics are identified by
/// `(name, sorted-label-set)`.
#[pyclass]
pub struct RustMetricsRegistry {
    metrics: HashMap<String, MetricEntry>,
}

#[pymethods]
impl RustMetricsRegistry {
    #[new]
    pub fn new() -> Self {
        RustMetricsRegistry {
            metrics: HashMap::new(),
        }
    }

    // -----------------------------------------------------------------------
    // Counter
    // -----------------------------------------------------------------------

    /// Increment a counter by `value` (must be >= 0).
    pub fn counter_inc(
        &mut self,
        name: &str,
        labels: Option<HashMap<String, String>>,
        value: f64,
    ) -> PyResult<()> {
        if value < 0.0 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "Counter can only be incremented by a non-negative value",
            ));
        }
        let key = metric_key_exact(name, &labels);
        let lbl = labels.unwrap_or_default();
        let entry = self.metrics.entry(key).or_insert_with(|| {
            MetricEntry::Counter(CounterData {
                name: name.to_string(),
                description: String::new(),
                labels: lbl.clone(),
                value: 0.0,
            })
        });
        match entry {
            MetricEntry::Counter(c) => {
                c.value += value;
                Ok(())
            }
            _ => Err(pyo3::exceptions::PyTypeError::new_err(format!(
                "Metric '{name}' already registered as a different type"
            ))),
        }
    }

    /// Return the current value of a counter (0.0 if not yet created).
    pub fn counter_get(
        &self,
        name: &str,
        labels: Option<HashMap<String, String>>,
    ) -> f64 {
        let key = metric_key_exact(name, &labels);
        match self.metrics.get(&key) {
            Some(MetricEntry::Counter(c)) => c.value,
            _ => 0.0,
        }
    }

    // -----------------------------------------------------------------------
    // Gauge
    // -----------------------------------------------------------------------

    pub fn gauge_set(
        &mut self,
        name: &str,
        labels: Option<HashMap<String, String>>,
        value: f64,
    ) {
        let key = metric_key_exact(name, &labels);
        let lbl = labels.unwrap_or_default();
        let entry = self.metrics.entry(key).or_insert_with(|| {
            MetricEntry::Gauge(GaugeData {
                name: name.to_string(),
                description: String::new(),
                labels: lbl.clone(),
                value: 0.0,
            })
        });
        if let MetricEntry::Gauge(g) = entry {
            g.value = value;
        }
    }

    pub fn gauge_inc(
        &mut self,
        name: &str,
        labels: Option<HashMap<String, String>>,
        value: f64,
    ) {
        let key = metric_key_exact(name, &labels);
        let lbl = labels.unwrap_or_default();
        let entry = self.metrics.entry(key).or_insert_with(|| {
            MetricEntry::Gauge(GaugeData {
                name: name.to_string(),
                description: String::new(),
                labels: lbl.clone(),
                value: 0.0,
            })
        });
        if let MetricEntry::Gauge(g) = entry {
            g.value += value;
        }
    }

    pub fn gauge_dec(
        &mut self,
        name: &str,
        labels: Option<HashMap<String, String>>,
        value: f64,
    ) {
        let key = metric_key_exact(name, &labels);
        let lbl = labels.unwrap_or_default();
        let entry = self.metrics.entry(key).or_insert_with(|| {
            MetricEntry::Gauge(GaugeData {
                name: name.to_string(),
                description: String::new(),
                labels: lbl.clone(),
                value: 0.0,
            })
        });
        if let MetricEntry::Gauge(g) = entry {
            g.value -= value;
        }
    }

    /// Return the current gauge value (0.0 if not yet created).
    pub fn gauge_get(
        &self,
        name: &str,
        labels: Option<HashMap<String, String>>,
    ) -> f64 {
        let key = metric_key_exact(name, &labels);
        match self.metrics.get(&key) {
            Some(MetricEntry::Gauge(g)) => g.value,
            _ => 0.0,
        }
    }

    // -----------------------------------------------------------------------
    // Histogram
    // -----------------------------------------------------------------------

    pub fn histogram_observe(
        &mut self,
        name: &str,
        labels: Option<HashMap<String, String>>,
        value: f64,
    ) {
        let key = metric_key_exact(name, &labels);
        let lbl = labels.unwrap_or_default();
        let entry = self.metrics.entry(key).or_insert_with(|| {
            MetricEntry::Histogram(HistogramData::new(
                name.to_string(),
                String::new(),
                lbl.clone(),
                10_000,
            ))
        });
        if let MetricEntry::Histogram(h) = entry {
            h.observe(value);
        }
    }

    /// Return the p-th percentile of the histogram window (0.0 if empty).
    pub fn histogram_get_percentile(
        &self,
        name: &str,
        labels: Option<HashMap<String, String>>,
        p: f64,
    ) -> f64 {
        let key = metric_key_exact(name, &labels);
        match self.metrics.get(&key) {
            Some(MetricEntry::Histogram(h)) => h.get_percentile(p),
            _ => 0.0,
        }
    }

    // -----------------------------------------------------------------------
    // Export
    // -----------------------------------------------------------------------

    /// Export all metrics in Prometheus text format.
    ///
    /// Format mirrors Python `MetricsRegistry.export_prometheus()`:
    /// ```text
    /// # HELP name description
    /// # TYPE name counter|gauge|histogram
    /// name{label="value"} value timestamp_ms
    /// ```
    pub fn export_prometheus(&self) -> String {
        // Group metric entries by base name (for HELP/TYPE header blocks).
        let mut by_name: HashMap<String, Vec<&MetricEntry>> = HashMap::new();
        for entry in self.metrics.values() {
            let name = match entry {
                MetricEntry::Counter(c) => &c.name,
                MetricEntry::Gauge(g) => &g.name,
                MetricEntry::Histogram(h) => &h.name,
            };
            by_name.entry(name.clone()).or_default().push(entry);
        }

        let ts = timestamp_ms();
        let mut lines: Vec<String> = Vec::new();

        let mut sorted_names: Vec<String> = by_name.keys().cloned().collect();
        sorted_names.sort();

        for name in &sorted_names {
            let entries = &by_name[name];
            let first = entries[0];

            match first {
                MetricEntry::Counter(fc) => {
                    if !fc.description.is_empty() {
                        lines.push(format!("# HELP {} {}", name, fc.description));
                    }
                    lines.push(format!("# TYPE {} counter", name));
                    for entry in entries {
                        if let MetricEntry::Counter(c) = entry {
                            let label_str = format_labels(&c.labels);
                            lines.push(format!("{}{} {} {}", name, label_str, c.value, ts));
                        }
                    }
                }
                MetricEntry::Gauge(fg) => {
                    if !fg.description.is_empty() {
                        lines.push(format!("# HELP {} {}", name, fg.description));
                    }
                    lines.push(format!("# TYPE {} gauge", name));
                    for entry in entries {
                        if let MetricEntry::Gauge(g) = entry {
                            let label_str = format_labels(&g.labels);
                            lines.push(format!("{}{} {} {}", name, label_str, g.value, ts));
                        }
                    }
                }
                MetricEntry::Histogram(fh) => {
                    if !fh.description.is_empty() {
                        lines.push(format!("# HELP {} {}", name, fh.description));
                    }
                    lines.push(format!("# TYPE {} histogram", name));
                    for entry in entries {
                        if let MetricEntry::Histogram(h) = entry {
                            let label_str = format_labels(&h.labels);
                            lines.push(format!(
                                "{}_count{} {} {}",
                                name, label_str, h.count, ts
                            ));
                            lines.push(format!(
                                "{}_sum{} {} {}",
                                name, label_str, h.sum, ts
                            ));
                        }
                    }
                }
            }
        }

        if lines.is_empty() {
            String::new()
        } else {
            lines.join("\n") + "\n"
        }
    }

    /// Return all metrics as a Python dict.
    ///
    /// Mirrors Python `MetricsRegistry.get_all()`.
    pub fn get_all(&self, py: Python<'_>) -> PyResult<PyObject> {
        let result = PyDict::new_bound(py);

        for (key, entry) in &self.metrics {
            let d = PyDict::new_bound(py);
            match entry {
                MetricEntry::Counter(c) => {
                    d.set_item("type", "counter")?;
                    d.set_item("value", c.value)?;
                    let lbl = PyDict::new_bound(py);
                    for (k, v) in &c.labels {
                        lbl.set_item(k, v)?;
                    }
                    d.set_item("labels", lbl)?;
                }
                MetricEntry::Gauge(g) => {
                    d.set_item("type", "gauge")?;
                    d.set_item("value", g.value)?;
                    let lbl = PyDict::new_bound(py);
                    for (k, v) in &g.labels {
                        lbl.set_item(k, v)?;
                    }
                    d.set_item("labels", lbl)?;
                }
                MetricEntry::Histogram(h) => {
                    d.set_item("type", "histogram")?;
                    d.set_item("count", h.count)?;
                    d.set_item("sum", h.sum)?;
                    let count = h.observations.len() as f64;
                    let avg = if count > 0.0 { h.sum / count } else { 0.0 };
                    d.set_item("avg", avg)?;
                    d.set_item("p50", h.get_percentile(50.0))?;
                    d.set_item("p95", h.get_percentile(95.0))?;
                    d.set_item("p99", h.get_percentile(99.0))?;
                    let lbl = PyDict::new_bound(py);
                    for (k, v) in &h.labels {
                        lbl.set_item(k, v)?;
                    }
                    d.set_item("labels", lbl)?;
                }
            }
            result.set_item(key, d)?;
        }

        Ok(result.into())
    }
}

impl Default for RustMetricsRegistry {
    fn default() -> Self {
        Self::new()
    }
}
