use crate::cli::{JobsAction, JobsArgs, JobsListArgs};
use crate::error::{AgoError, Result};
use crate::runtime::Runtime;
use serde_json::Value;

pub async fn run(rt: &Runtime, args: JobsArgs) -> Result<()> {
    match args.action {
        JobsAction::List(a) => list(rt, a).await,
        JobsAction::Show { session_id, json } => show(rt, &session_id, json).await,
        JobsAction::Cancel { job_id } => cancel(rt, &job_id).await,
    }
}

async fn list(rt: &Runtime, args: JobsListArgs) -> Result<()> {
    let client = rt.api_client()?;
    let payload = client.jobs_list().await?;
    let sessions = payload
        .get("sessions")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let truncated: Vec<Value> = sessions.into_iter().take(args.limit).collect();

    if args.json {
        let out = serde_json::json!({"sessions": truncated});
        println!(
            "{}",
            serde_json::to_string(&out).map_err(|e| AgoError::Other(e.to_string()))?
        );
        return Ok(());
    }
    if truncated.is_empty() {
        println!("(no sessions)");
        return Ok(());
    }
    println!(
        "{:<22}  {:>6}  {:>5}  {:<24}  TASK",
        "SESSION", "RECS", "FILES", "LAST"
    );
    for s in &truncated {
        let id = s.get("session_id").and_then(|v| v.as_str()).unwrap_or("?");
        let records = s.get("records").and_then(|v| v.as_u64()).unwrap_or(0);
        let files = s.get("files").and_then(|v| v.as_u64()).unwrap_or(0);
        let last = s.get("last_type").and_then(|v| v.as_str()).unwrap_or("");
        let task = s.get("first_prompt").and_then(|v| v.as_str()).unwrap_or("");
        let task_trunc = truncate(task, 60);
        println!("{id:<22}  {records:>6}  {files:>5}  {last:<24}  {task_trunc}");
    }
    Ok(())
}

async fn show(rt: &Runtime, session_id: &str, json: bool) -> Result<()> {
    let client = rt.api_client()?;
    let payload = client.jobs_show(session_id).await?;
    if json {
        println!(
            "{}",
            serde_json::to_string(&payload).map_err(|e| AgoError::Other(e.to_string()))?
        );
        return Ok(());
    }
    let records = payload
        .get("records")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    if records.is_empty() {
        println!("(no records)");
        return Ok(());
    }
    for (i, rec) in records.iter().enumerate() {
        let job_type = rec.get("job_type").and_then(|v| v.as_str()).unwrap_or("?");
        let task = rec
            .get("task")
            .or_else(|| rec.get("prompt"))
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let task_trunc = truncate(task, 100);
        println!("[{:>3}] {:<14}  {}", i + 1, job_type, task_trunc);
    }
    Ok(())
}

async fn cancel(rt: &Runtime, job_id: &str) -> Result<()> {
    let client = rt.api_client()?;
    let payload = client.job_cancel(job_id).await?;
    let status = payload
        .get("status")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown");
    let cancelled = payload
        .get("cancelled")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    if cancelled {
        println!("{job_id}: cancelling (status was {status})");
    } else {
        println!("{job_id}: status={status} (no-op)");
    }
    Ok(())
}

fn truncate(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        s.to_string()
    } else {
        let mut t: String = s.chars().take(max - 1).collect();
        t.push('…');
        t
    }
}
