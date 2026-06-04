use crate::cli::{JobsAction, JobsArgs, JobsListArgs};
use crate::error::{AgoError, Result};
use crate::runtime::Runtime;
use serde_json::Value;
use std::path::{Path, PathBuf};

pub async fn run(rt: &Runtime, args: JobsArgs) -> Result<()> {
    match args.action {
        JobsAction::List(a) => list(rt, a).await,
        JobsAction::Show { session_id, json } => show(rt, &session_id, json).await,
        JobsAction::Cancel { job_id } => cancel(rt, &job_id).await,
        JobsAction::Download {
            session_id,
            dir,
            force,
        } => download(rt, &session_id, dir.as_deref(), force).await,
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

async fn download(
    rt: &Runtime,
    session_id: &str,
    dir_override: Option<&Path>,
    force: bool,
) -> Result<()> {
    let client = rt.api_client()?;
    let dest = match dir_override {
        Some(p) => p.to_path_buf(),
        None => PathBuf::from(".ago-sync").join(session_id),
    };
    // Refuse to clobber an existing non-empty directory without --force, so
    // a re-run of `ago jobs download` does not silently overwrite local
    // edits the user may have made in the extracted tree.
    if let Ok(read) = std::fs::read_dir(&dest) {
        let has_entries = read.flatten().next().is_some();
        if has_entries && !force {
            return Err(AgoError::Other(format!(
                "destination {} is not empty — pass --force to overwrite",
                dest.display()
            )));
        }
    }
    std::fs::create_dir_all(&dest).map_err(AgoError::from)?;

    eprintln!(
        "\x1b[2m· downloading session {session_id} from {}\x1b[0m",
        rt.server_url()?
    );
    let zip_bytes = client.download_session_zip(session_id).await?;
    let count = extract_zip_to(&zip_bytes, &dest)?;
    eprintln!(
        "\x1b[2m· extracted {count} file(s) to {}\x1b[0m",
        dest.display()
    );
    println!("{}", dest.display());
    Ok(())
}

/// Extract every entry of a ZIP byte-slice into `dest`. Returns the number
/// of files actually written. Defenses:
///   * paths are rejected if they escape `dest` (`..` zip-slip);
///   * absolute paths inside the ZIP are stripped to their tail;
///   * directories in the archive are created but do not count as written
///     files in the returned tally.
fn extract_zip_to(bytes: &[u8], dest: &Path) -> Result<usize> {
    use std::io::Read;
    let reader = std::io::Cursor::new(bytes);
    let mut archive = zip::ZipArchive::new(reader)
        .map_err(|e| AgoError::Other(format!("invalid ZIP from server: {e}")))?;
    let mut count = 0usize;
    let canonical_dest = dest.canonicalize().unwrap_or_else(|_| dest.to_path_buf());
    for i in 0..archive.len() {
        let mut entry = archive
            .by_index(i)
            .map_err(|e| AgoError::Other(format!("ZIP entry {i}: {e}")))?;
        // `enclosed_name` already rejects `..` traversal and absolute paths;
        // anything that fails it is silently skipped (would unzip to None).
        let Some(rel) = entry.enclosed_name() else {
            tracing::warn!("skipping unsafe ZIP entry: {}", entry.name());
            continue;
        };
        let target = dest.join(&rel);
        // Belt-and-braces zip-slip check: even after enclosed_name() we
        // verify the resolved path stays under dest. canonicalize on the
        // dest is enough; the target may not exist yet, so we resolve its
        // parent.
        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(parent).map_err(AgoError::from)?;
            if let Ok(canon_parent) = parent.canonicalize() {
                if !canon_parent.starts_with(&canonical_dest) {
                    return Err(AgoError::Other(format!(
                        "refusing to extract entry outside destination: {}",
                        rel.display()
                    )));
                }
            }
        }
        if entry.is_dir() {
            std::fs::create_dir_all(&target).map_err(AgoError::from)?;
            continue;
        }
        let mut buf = Vec::with_capacity(entry.size() as usize);
        entry.read_to_end(&mut buf).map_err(AgoError::from)?;
        std::fs::write(&target, &buf).map_err(AgoError::from)?;
        count += 1;
    }
    Ok(count)
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
