/// Unit tests for RustTaskQueue.
///
/// All behaviour mirrors the Python `TaskQueue` implementation.
use agent_orchestrator_rust::*;
use pyo3::prelude::*;

fn make_task(id: &str, priority: i64) -> RustQueuedTask {
    RustQueuedTask::new(
        id.to_string(),
        "test task".to_string(),
        priority,
        None,
        None,
        None,
        None,
        None,
    )
}

fn make_task_for_agent(id: &str, priority: i64, agent: &str) -> RustQueuedTask {
    RustQueuedTask::new(
        id.to_string(),
        "test task".to_string(),
        priority,
        None,
        Some(agent.to_string()),
        None,
        None,
        None,
    )
}

// ---------------------------------------------------------------------------
// enqueue
// ---------------------------------------------------------------------------

#[test]
fn test_enqueue_returns_task_id() {
    let mut q = RustTaskQueue::new();
    let task = make_task("t1", 1);
    let id = q.enqueue(task);
    assert_eq!(id, "t1");
}

#[test]
fn test_enqueue_forces_pending_status() {
    let mut q = RustTaskQueue::new();
    let mut task = make_task("t1", 1);
    task.status = "running".to_string(); // force wrong status
    q.enqueue(task);
    let stored = q.get_task("t1").unwrap();
    assert_eq!(stored.status, "pending");
}

#[test]
fn test_enqueue_multiple_tasks() {
    let mut q = RustTaskQueue::new();
    q.enqueue(make_task("a", 1));
    q.enqueue(make_task("b", 2));
    q.enqueue(make_task("c", 3));
    assert_eq!(q.get_pending().len(), 3);
}

// ---------------------------------------------------------------------------
// dequeue — priority ordering
// ---------------------------------------------------------------------------

#[test]
fn test_dequeue_returns_highest_priority() {
    let mut q = RustTaskQueue::new();
    q.enqueue(make_task("low", 1));
    q.enqueue(make_task("high", 10));
    q.enqueue(make_task("mid", 5));

    let task = q.dequeue(None).unwrap();
    assert_eq!(task.task_id, "high");
    assert_eq!(task.status, "running");
}

#[test]
fn test_dequeue_fifo_within_same_priority() {
    let mut q = RustTaskQueue::new();
    // Use explicit created_at to ensure ordering is deterministic.
    let mut t1 = make_task("first", 5);
    t1.created_at = 1000.0;
    let mut t2 = make_task("second", 5);
    t2.created_at = 2000.0;
    q.enqueue(t1);
    q.enqueue(t2);

    let task = q.dequeue(None).unwrap();
    assert_eq!(task.task_id, "first"); // lower created_at wins
}

#[test]
fn test_dequeue_empty_queue_returns_none() {
    let mut q = RustTaskQueue::new();
    assert!(q.dequeue(None).is_none());
}

#[test]
fn test_dequeue_sets_started_at() {
    let mut q = RustTaskQueue::new();
    q.enqueue(make_task("t1", 1));
    let task = q.dequeue(None).unwrap();
    assert!(task.started_at.is_some());
}

// ---------------------------------------------------------------------------
// dequeue — agent_name filtering
// ---------------------------------------------------------------------------

#[test]
fn test_dequeue_agent_name_filter_matches() {
    let mut q = RustTaskQueue::new();
    q.enqueue(make_task_for_agent("t1", 1, "backend"));
    let task = q.dequeue(Some("backend".to_string())).unwrap();
    assert_eq!(task.task_id, "t1");
}

#[test]
fn test_dequeue_agent_name_filter_skips_wrong_agent() {
    let mut q = RustTaskQueue::new();
    q.enqueue(make_task_for_agent("t1", 1, "backend"));
    let task = q.dequeue(Some("frontend".to_string()));
    assert!(task.is_none());
}

#[test]
fn test_dequeue_unassigned_task_matches_any_agent() {
    let mut q = RustTaskQueue::new();
    q.enqueue(make_task("t1", 1)); // no agent_name
    let task = q.dequeue(Some("backend".to_string())).unwrap();
    assert_eq!(task.task_id, "t1");
}

// ---------------------------------------------------------------------------
// complete
// ---------------------------------------------------------------------------

#[test]
fn test_complete_sets_status_and_result() {
    let mut q = RustTaskQueue::new();
    q.enqueue(make_task("t1", 1));
    q.dequeue(None);
    q.complete("t1", "done");

    let task = q.get_task("t1").unwrap();
    assert_eq!(task.status, "completed");
    assert_eq!(task.result.as_deref(), Some("done"));
    assert!(task.completed_at.is_some());
}

#[test]
fn test_complete_unknown_task_does_not_panic() {
    let mut q = RustTaskQueue::new();
    q.complete("nonexistent", "result"); // must not panic
}

// ---------------------------------------------------------------------------
// fail — retry logic
// ---------------------------------------------------------------------------

#[test]
fn test_fail_increments_retries() {
    let mut q = RustTaskQueue::new();
    q.enqueue(make_task("t1", 1));
    q.dequeue(None);
    q.fail("t1", "oops");

    let task = q.get_task("t1").unwrap();
    assert_eq!(task.retries, 1);
}

#[test]
fn test_fail_resets_to_pending_when_retries_remain() {
    let mut q = RustTaskQueue::new();
    q.enqueue(make_task("t1", 1));
    q.dequeue(None);
    q.fail("t1", "oops");

    let task = q.get_task("t1").unwrap();
    assert_eq!(task.status, "pending"); // max_retries=3, retries=1 → retry
    assert!(task.started_at.is_none());
}

#[test]
fn test_fail_marks_failed_when_max_retries_exhausted() {
    let mut q = RustTaskQueue::new();
    let mut task = make_task("t1", 1);
    task.max_retries = 2;
    q.enqueue(task);

    // Fail twice — second fail should mark it permanently failed.
    q.dequeue(None);
    q.fail("t1", "err1"); // retries=1 < 2 → pending
    q.dequeue(None);
    q.fail("t1", "err2"); // retries=2 == 2 → failed

    let t = q.get_task("t1").unwrap();
    assert_eq!(t.status, "failed");
    assert!(t.completed_at.is_some());
}

#[test]
fn test_fail_stores_error_in_result() {
    let mut q = RustTaskQueue::new();
    q.enqueue(make_task("t1", 1));
    q.dequeue(None);
    q.fail("t1", "something broke");

    let task = q.get_task("t1").unwrap();
    assert_eq!(task.result.as_deref(), Some("something broke"));
}

// ---------------------------------------------------------------------------
// retry
// ---------------------------------------------------------------------------

#[test]
fn test_retry_failed_task_returns_true() {
    let mut q = RustTaskQueue::new();
    let mut task = make_task("t1", 1);
    task.max_retries = 1;
    q.enqueue(task);
    q.dequeue(None);
    q.fail("t1", "err"); // permanently failed (max_retries=1)

    assert!(q.retry("t1"));
    let t = q.get_task("t1").unwrap();
    assert_eq!(t.status, "pending");
}

#[test]
fn test_retry_non_failed_task_returns_false() {
    let mut q = RustTaskQueue::new();
    q.enqueue(make_task("t1", 1));
    assert!(!q.retry("t1")); // pending, not failed
}

#[test]
fn test_retry_nonexistent_task_returns_false() {
    let mut q = RustTaskQueue::new();
    assert!(!q.retry("ghost"));
}

#[test]
fn test_retry_does_not_reset_retry_counter() {
    let mut q = RustTaskQueue::new();
    let mut task = make_task("t1", 1);
    task.max_retries = 1;
    q.enqueue(task);
    q.dequeue(None);
    q.fail("t1", "err"); // permanently failed

    q.retry("t1");
    let t = q.get_task("t1").unwrap();
    // retries should NOT be reset back to 0 (Python doesn't reset them either).
    assert_eq!(t.retries, 1);
}

// ---------------------------------------------------------------------------
// get_pending / get_running
// ---------------------------------------------------------------------------

#[test]
fn test_get_pending_returns_only_pending() {
    let mut q = RustTaskQueue::new();
    q.enqueue(make_task("a", 1));
    q.enqueue(make_task("b", 1));
    q.dequeue(None); // one goes to running
    assert_eq!(q.get_pending().len(), 1);
}

#[test]
fn test_get_running_returns_only_running() {
    let mut q = RustTaskQueue::new();
    q.enqueue(make_task("a", 1));
    q.enqueue(make_task("b", 1));
    q.dequeue(None);
    assert_eq!(q.get_running().len(), 1);
}

// ---------------------------------------------------------------------------
// get_stats (tested via Python FFI in the integration layer, here just smoke)
// ---------------------------------------------------------------------------

#[test]
fn test_get_stats_does_not_panic() {
    pyo3::prepare_freethreaded_python();
    let mut q = RustTaskQueue::new();
    q.enqueue(make_task("a", 1));
    q.enqueue(make_task("b", 1));
    q.dequeue(None); // running
    q.complete("a", "ok");

    pyo3::Python::with_gil(|py| {
        let stats = q.get_stats(py).unwrap();
        // Just verify the call succeeds; value inspection via Python dict.
        let _ = stats;
    });
}
