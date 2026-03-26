/// PyO3 module entry point — registers all Rust-backed Python classes.
///
/// Importable as `import _agent_orchestrator_rust` from Python.
///
/// The `pub use` re-exports below make the types available to integration tests
/// in `tests/` which link against this crate as a library.
use pyo3::prelude::*;

pub mod graph_engine;
pub mod metrics;
pub mod rate_limiter;
pub mod router;
pub mod task_queue;
pub mod types;

pub use graph_engine::GraphTopology;
pub use metrics::RustMetricsRegistry;
pub use rate_limiter::RustRateLimiter;
pub use router::{RustClassifier, RustTaskComplexity};
pub use task_queue::{RustQueuedTask, RustTaskQueue};
pub use types::{END, START};

/// The native extension module `_agent_orchestrator_rust`.
///
/// All public Python-visible classes are registered here.
#[pymodule]
fn _agent_orchestrator_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Graph engine — topology validation and traversal
    m.add_class::<GraphTopology>()?;

    // Router — task complexity classifier
    m.add_class::<RustClassifier>()?;
    m.add_class::<RustTaskComplexity>()?;

    // Task queue
    m.add_class::<RustQueuedTask>()?;
    m.add_class::<RustTaskQueue>()?;

    // Rate limiter
    m.add_class::<RustRateLimiter>()?;

    // Metrics registry
    m.add_class::<RustMetricsRegistry>()?;

    // Sentinel constants used by the graph engine
    m.add("START", types::START)?;
    m.add("END", types::END)?;

    Ok(())
}
