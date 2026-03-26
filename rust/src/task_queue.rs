/// Port of `core/task_queue.py` — in-memory priority task queue.
///
/// Priority ordering: descending priority, then ascending created_at (FIFO).
use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use pyo3::prelude::*;
use pyo3::types::PyDict;

/// Return the current Unix timestamp as a float (seconds).
fn now_f64() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// A single queued task — mirrors Python `QueuedTask` dataclass.
#[pyclass]
#[derive(Clone, Debug)]
pub struct RustQueuedTask {
    #[pyo3(get, set)]
    pub task_id: String,
    #[pyo3(get, set)]
    pub description: String,
    #[pyo3(get, set)]
    pub priority: i64,
    #[pyo3(get, set)]
    pub status: String,
    #[pyo3(get, set)]
    pub agent_name: Option<String>,
    #[pyo3(get, set)]
    pub created_at: f64,
    #[pyo3(get, set)]
    pub started_at: Option<f64>,
    #[pyo3(get, set)]
    pub completed_at: Option<f64>,
    #[pyo3(get, set)]
    pub result: Option<String>,
    #[pyo3(get, set)]
    pub retries: i64,
    #[pyo3(get, set)]
    pub max_retries: i64,
}

#[pymethods]
impl RustQueuedTask {
    /// Construct a new task.  `created_at` defaults to the current time.
    #[new]
    #[pyo3(signature = (
        task_id,
        description,
        priority,
        status = None,
        agent_name = None,
        created_at = None,
        retries = None,
        max_retries = None,
    ))]
    pub fn new(
        task_id: String,
        description: String,
        priority: i64,
        status: Option<String>,
        agent_name: Option<String>,
        created_at: Option<f64>,
        retries: Option<i64>,
        max_retries: Option<i64>,
    ) -> Self {
        RustQueuedTask {
            task_id,
            description,
            priority,
            status: status.unwrap_or_else(|| "pending".to_string()),
            agent_name,
            created_at: created_at.unwrap_or_else(now_f64),
            started_at: None,
            completed_at: None,
            result: None,
            retries: retries.unwrap_or(0),
            max_retries: max_retries.unwrap_or(3),
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "RustQueuedTask(task_id={:?}, priority={}, status={:?})",
            self.task_id, self.priority, self.status
        )
    }
}

/// In-memory priority task queue — mirrors Python `TaskQueue`.
#[pyclass]
pub struct RustTaskQueue {
    tasks: HashMap<String, RustQueuedTask>,
}

#[pymethods]
impl RustTaskQueue {
    #[new]
    pub fn new() -> Self {
        RustTaskQueue {
            tasks: HashMap::new(),
        }
    }

    /// Add a task to the queue.  Forces `status = "pending"`.  Returns the task_id.
    pub fn enqueue(&mut self, mut task: RustQueuedTask) -> String {
        task.status = "pending".to_string();
        let id = task.task_id.clone();
        self.tasks.insert(id.clone(), task);
        id
    }

    /// Pop and return the highest-priority pending task.
    ///
    /// If `agent_name` is given, only tasks whose `agent_name` is `None` or
    /// matches are considered — replicates Python's `dequeue()` filter.
    ///
    /// Sorting: descending priority, then ascending `created_at` (FIFO within
    /// the same priority tier).
    pub fn dequeue(&mut self, agent_name: Option<String>) -> Option<RustQueuedTask> {
        // Collect task_ids of matching pending tasks.
        let mut candidates: Vec<(String, i64, f64)> = self
            .tasks
            .values()
            .filter(|t| {
                t.status == "pending"
                    && match (&agent_name, &t.agent_name) {
                        (None, _) => true,
                        (Some(_), None) => true,
                        (Some(a), Some(b)) => a == b,
                    }
            })
            .map(|t| (t.task_id.clone(), t.priority, t.created_at))
            .collect();

        if candidates.is_empty() {
            return None;
        }

        // Sort: priority descending, created_at ascending (stable).
        candidates.sort_by(|a, b| {
            b.1.cmp(&a.1) // higher priority first
                .then(a.2.partial_cmp(&b.2).unwrap_or(std::cmp::Ordering::Equal))
        });

        let task_id = candidates.into_iter().next()?.0;
        let task = self.tasks.get_mut(&task_id)?;
        task.status = "running".to_string();
        task.started_at = Some(now_f64());
        Some(task.clone())
    }

    /// Mark a running task as completed.
    pub fn complete(&mut self, task_id: &str, result: &str) {
        if let Some(task) = self.tasks.get_mut(task_id) {
            task.status = "completed".to_string();
            task.result = Some(result.to_string());
            task.completed_at = Some(now_f64());
        }
    }

    /// Record a failure.
    ///
    /// Increments `retries`.  If `retries < max_retries` the task is reset to
    /// pending so it will be retried.  Otherwise it is permanently failed.
    ///
    /// The last error message is always stored in `result` for inspection.
    pub fn fail(&mut self, task_id: &str, error: &str) {
        if let Some(task) = self.tasks.get_mut(task_id) {
            task.retries += 1;
            task.result = Some(error.to_string());
            if task.retries < task.max_retries {
                task.status = "pending".to_string();
                task.started_at = None;
            } else {
                task.status = "failed".to_string();
                task.completed_at = Some(now_f64());
            }
        }
    }

    /// Manually re-queue a failed task.
    ///
    /// Returns `True` on success, `False` if the task does not exist or is not
    /// in the `"failed"` state.  Does NOT reset `retries` — the caller
    /// explicitly requested the retry.
    pub fn retry(&mut self, task_id: &str) -> bool {
        match self.tasks.get_mut(task_id) {
            Some(task) if task.status == "failed" => {
                task.status = "pending".to_string();
                task.started_at = None;
                task.completed_at = None;
                true
            }
            _ => false,
        }
    }

    /// Look up a task by id without removing it.
    pub fn get_task(&self, task_id: &str) -> Option<RustQueuedTask> {
        self.tasks.get(task_id).cloned()
    }

    /// Return all pending tasks.
    pub fn get_pending(&self) -> Vec<RustQueuedTask> {
        self.tasks
            .values()
            .filter(|t| t.status == "pending")
            .cloned()
            .collect()
    }

    /// Return all running tasks.
    pub fn get_running(&self) -> Vec<RustQueuedTask> {
        self.tasks
            .values()
            .filter(|t| t.status == "running")
            .cloned()
            .collect()
    }

    /// Return queue statistics as a Python dict.
    ///
    /// Keys: `pending`, `running`, `completed`, `failed`, `total`.
    pub fn get_stats(&self, py: Python<'_>) -> PyResult<PyObject> {
        let pending = self.tasks.values().filter(|t| t.status == "pending").count();
        let running = self.tasks.values().filter(|t| t.status == "running").count();
        let completed = self.tasks.values().filter(|t| t.status == "completed").count();
        let failed = self.tasks.values().filter(|t| t.status == "failed").count();
        let total = self.tasks.len();

        let dict = PyDict::new_bound(py);
        dict.set_item("pending", pending)?;
        dict.set_item("running", running)?;
        dict.set_item("completed", completed)?;
        dict.set_item("failed", failed)?;
        dict.set_item("total", total)?;
        Ok(dict.into())
    }
}

impl Default for RustTaskQueue {
    fn default() -> Self {
        Self::new()
    }
}
