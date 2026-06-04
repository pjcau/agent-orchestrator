//! `AGO.md` discovery and inlining — the orchestrator equivalent of
//! `CLAUDE.md` / `CURSOR.md`.
//!
//! A project drops an `AGO.md` (or `.ago.md`) at its root with the kind of
//! persistent context that should ride along on every turn: coding style,
//! architectural invariants, "always run `pytest` after edits", links to
//! the issue tracker, the active sprint goal, etc. The CLI:
//!
//! 1. Walks up from `cwd` to find it (same algorithm as `.ago.yaml`).
//! 2. Loads it once at startup, applying the project's
//!    `context.max_file_bytes` cap so a runaway `AGO.md` cannot blow the
//!    budget.
//! 3. Each `ago chat` / `ago run` turn prepends the document to the
//!    `cache_context` body field — meaning the OpenRouter
//!    `cache_control: ephemeral` marker covers it. After the first turn it
//!    is essentially free (50–90% off on cached input on Anthropic-routed
//!    models).
//!
//! Pure client-side change: no server endpoint is touched. The server
//! sees one slightly-longer cacheable prefix; it does not need to know
//! the prefix came from a file with a special name.

use crate::error::{AgoError, Result};
use std::path::{Path, PathBuf};

pub const FILE_PRIMARY: &str = "AGO.md";
pub const FILE_FALLBACK: &str = ".ago.md";

/// In-memory representation of the discovered instructions document.
#[derive(Debug, Clone)]
pub struct Instructions {
    /// Absolute path to the file on disk (for diagnostics).
    pub path: PathBuf,
    /// Content as a UTF-8 string. Already capped at `max_bytes` if
    /// truncation was needed; check `truncated` to know.
    pub content: String,
    pub truncated: bool,
    /// Pre-truncation length, for the stderr report.
    pub original_bytes: usize,
}

impl Instructions {
    /// Walk up from `start_dir` looking for `AGO.md`, then `.ago.md`. Stops at
    /// `stop_at` (exclusive). Returns `None` if neither file exists in any
    /// ancestor.
    pub fn discover(start_dir: &Path, stop_at: Option<&Path>) -> Result<Option<PathBuf>> {
        let mut cursor = start_dir.to_path_buf();
        loop {
            for name in [FILE_PRIMARY, FILE_FALLBACK] {
                let candidate = cursor.join(name);
                if candidate.is_file() {
                    return Ok(Some(candidate));
                }
            }
            if let Some(stop) = stop_at {
                if cursor == stop {
                    return Ok(None);
                }
            }
            if !cursor.pop() {
                return Ok(None);
            }
        }
    }

    /// Load an `AGO.md` from a known path, applying `max_bytes` as a hard
    /// truncation cap. The CLI uses `ContextConfig.max_file_bytes` here so
    /// the doc obeys the same per-file ceiling as any `@file` ref.
    pub fn load(path: &Path, max_bytes: usize) -> Result<Self> {
        let raw = std::fs::read(path)
            .map_err(|e| AgoError::Config(format!("read {}: {e}", path.display())))?;
        let original_bytes = raw.len();
        let truncated = original_bytes > max_bytes;
        let slice = if truncated {
            &raw[..max_bytes]
        } else {
            &raw[..]
        };
        // Replace invalid UTF-8 with U+FFFD so weird files do not poison
        // the prompt; a binary AGO.md is user error but we keep going.
        let content = String::from_utf8_lossy(slice).into_owned();
        Ok(Self {
            path: path.to_path_buf(),
            content,
            truncated,
            original_bytes,
        })
    }

    /// Format the loaded instructions as a labeled block ready to prepend
    /// to the `cache_context` body field. The label lets the LLM cite
    /// "per AGO.md, ..." without having to be told which file the text
    /// came from.
    pub fn as_cache_block(&self) -> String {
        let mut s = String::with_capacity(self.content.len() + 64);
        s.push_str(&format!(
            "\n[AGO.md] project instructions: {}\n",
            self.path.display()
        ));
        s.push_str("```markdown\n");
        s.push_str(&self.content);
        if !self.content.ends_with('\n') {
            s.push('\n');
        }
        s.push_str("```\n");
        if self.truncated {
            s.push_str(&format!(
                "(truncated to {} bytes; original was {} bytes — raise context.max_file_bytes in .ago.yaml to send more)\n",
                self.content.len(),
                self.original_bytes
            ));
        }
        s
    }

    /// Combine an `AGO.md` block (if any) with a per-turn `@ref` cache
    /// context. Result keeps a stable byte prefix: `AGO.md` first (slow
    /// to change), then `@ref` content (changes per prompt). That ordering
    /// is the one the OpenRouter prompt cache can exploit best.
    pub fn merge_with_refs(instructions: Option<&Instructions>, refs_cache: &str) -> String {
        match (instructions, refs_cache.is_empty()) {
            (None, _) => refs_cache.to_string(),
            (Some(doc), true) => doc.as_cache_block().trim_end().to_string(),
            (Some(doc), false) => {
                let mut s = doc.as_cache_block();
                s.push_str(refs_cache);
                s
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    #[test]
    fn discover_prefers_ago_md_over_dotted() {
        let dir = tempdir().unwrap();
        fs::write(dir.path().join("AGO.md"), "primary").unwrap();
        fs::write(dir.path().join(".ago.md"), "fallback").unwrap();
        let p = Instructions::discover(dir.path(), None).unwrap().unwrap();
        assert!(p.ends_with("AGO.md"));
    }

    #[test]
    fn discover_falls_back_to_dotted() {
        let dir = tempdir().unwrap();
        fs::write(dir.path().join(".ago.md"), "fallback").unwrap();
        let p = Instructions::discover(dir.path(), None).unwrap().unwrap();
        assert!(p.ends_with(".ago.md"));
    }

    #[test]
    fn discover_walks_up_to_ancestor() {
        let dir = tempdir().unwrap();
        let nested = dir.path().join("a").join("b").join("c");
        fs::create_dir_all(&nested).unwrap();
        fs::write(dir.path().join("AGO.md"), "outer").unwrap();
        let p = Instructions::discover(&nested, None).unwrap().unwrap();
        assert_eq!(p, dir.path().join("AGO.md"));
    }

    #[test]
    fn discover_returns_none_when_absent() {
        let dir = tempdir().unwrap();
        let res = Instructions::discover(dir.path(), Some(dir.path())).unwrap();
        assert!(res.is_none());
    }

    #[test]
    fn load_truncates_above_cap() {
        let dir = tempdir().unwrap();
        let big = "x".repeat(5000);
        let path = dir.path().join("AGO.md");
        fs::write(&path, &big).unwrap();
        let doc = Instructions::load(&path, 100).unwrap();
        assert!(doc.truncated);
        assert_eq!(doc.content.len(), 100);
        assert_eq!(doc.original_bytes, 5000);
    }

    #[test]
    fn as_cache_block_labels_and_fences() {
        let doc = Instructions {
            path: PathBuf::from("/p/AGO.md"),
            content: "be brief".to_string(),
            truncated: false,
            original_bytes: 8,
        };
        let block = doc.as_cache_block();
        assert!(block.contains("[AGO.md]"));
        assert!(block.contains("```markdown"));
        assert!(block.contains("be brief"));
    }

    #[test]
    fn as_cache_block_includes_truncation_hint() {
        let doc = Instructions {
            path: PathBuf::from("/p/AGO.md"),
            content: "x".repeat(100),
            truncated: true,
            original_bytes: 9000,
        };
        let block = doc.as_cache_block();
        assert!(block.contains("truncated"));
        assert!(block.contains("9000"));
    }

    #[test]
    fn merge_with_refs_puts_instructions_first() {
        let doc = Instructions {
            path: PathBuf::from("/p/AGO.md"),
            content: "rule one".to_string(),
            truncated: false,
            original_bytes: 8,
        };
        let merged = Instructions::merge_with_refs(Some(&doc), "\nREFS_BODY\n");
        let i = merged.find("rule one").unwrap();
        let r = merged.find("REFS_BODY").unwrap();
        assert!(i < r, "AGO.md must come before @ref content");
    }

    #[test]
    fn merge_with_refs_passthrough_without_doc() {
        assert_eq!(
            Instructions::merge_with_refs(None, "REFS_ONLY"),
            "REFS_ONLY"
        );
    }

    #[test]
    fn merge_with_refs_doc_only_when_refs_empty() {
        let doc = Instructions {
            path: PathBuf::from("/p/AGO.md"),
            content: "instr".to_string(),
            truncated: false,
            original_bytes: 5,
        };
        let merged = Instructions::merge_with_refs(Some(&doc), "");
        assert!(merged.contains("instr"));
        assert!(!merged.ends_with('\n'), "trim_end applied");
    }
}
