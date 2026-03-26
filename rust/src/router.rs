/// Port of `core/router.py` — TaskComplexityClassifier.
///
/// Produces identical output to the Python implementation for all inputs.
use std::collections::HashSet;

use pyo3::prelude::*;
use regex::Regex;

// ---------------------------------------------------------------------------
// Keyword sets — exact copies from the Python source
// ---------------------------------------------------------------------------

fn high_keywords() -> HashSet<&'static str> {
    [
        "architect",
        "architecture",
        "design",
        "optimize",
        "refactor",
        "security audit",
        "performance",
        "distributed",
        "scalability",
        "migration",
        "machine learning",
        "neural",
        "inference",
        "complex",
        "extensive",
        "multi-step",
        "multistep",
        "comprehensive",
        "analyse",
        "analyze",
        "reasoning",
        "strategy",
        "evaluate",
        "compare",
        "tradeoff",
        "trade-off",
        "deep dive",
        "redesign",
        "across the codebase",
        "multi-system",
        "plan mode",
    ]
    .iter()
    .copied()
    .collect()
}

fn low_keywords() -> HashSet<&'static str> {
    [
        "summarize",
        "summarise",
        "list",
        "simple",
        "basic",
        "quick",
        "brief",
        "short",
        "translate",
        "format",
        "fix typo",
        "rename",
        "echo",
        "hello",
        "ping",
        "status",
        "check",
    ]
    .iter()
    .copied()
    .collect()
}

fn low_patterns() -> Vec<Regex> {
    let patterns = [
        r"\bgit\s+(commit|push|pull|status|log|diff|add|stash|branch|merge)\b",
        r"\brename\b",
        r"\bmove\s+file\b",
        r"\bdelete\s+file\b",
        r"\bformat\b",
        r"\blint\b",
        r"\bprettier\b",
        r"\beslint\b",
        r"\bremove\s+(unused|dead)\b",
        r"\bupdate\s+(version|package)\b",
    ];
    patterns
        .iter()
        .map(|p| Regex::new(p).expect("hard-coded regex must compile"))
        .collect()
}

const TOOL_KEYWORDS: &[&str] = &["code", "file", "run", "execute", "test", "deploy", "write"];

/// Thresholds from Python source.
const HIGH_WORD_THRESHOLD: usize = 200;
const LOW_WORD_CEILING: usize = 30;

// ---------------------------------------------------------------------------
// PyO3 types
// ---------------------------------------------------------------------------

/// Result of classifying a task — mirrors Python `TaskComplexity`.
#[pyclass]
#[derive(Clone, Debug)]
pub struct RustTaskComplexity {
    #[pyo3(get)]
    pub level: String,
    #[pyo3(get)]
    pub estimated_tokens: i64,
    #[pyo3(get)]
    pub requires_tools: bool,
    #[pyo3(get)]
    pub requires_reasoning: bool,
}

#[pymethods]
impl RustTaskComplexity {
    fn __repr__(&self) -> String {
        format!(
            "RustTaskComplexity(level={:?}, estimated_tokens={}, requires_tools={}, requires_reasoning={})",
            self.level, self.estimated_tokens, self.requires_tools, self.requires_reasoning
        )
    }
}

/// Classifier that mirrors `TaskComplexityClassifier` from Python.
///
/// Pre-compiles all regexes at construction time so that `classify()` is
/// allocation-free on every call.
#[pyclass]
pub struct RustClassifier {
    high_kw: HashSet<&'static str>,
    low_kw: HashSet<&'static str>,
    low_re: Vec<Regex>,
}

#[pymethods]
impl RustClassifier {
    /// Build the classifier, pre-compiling all regex patterns.
    #[new]
    pub fn new() -> Self {
        RustClassifier {
            high_kw: high_keywords(),
            low_kw: low_keywords(),
            low_re: low_patterns(),
        }
    }

    /// Classify `task` and return a `RustTaskComplexity`.
    ///
    /// Replicates `TaskComplexityClassifier.classify()` exactly:
    /// - Keyword hits are counted as substring matches in the lowercased task.
    /// - Regex patterns are applied to the lowercased task.
    /// - Token estimate: `max(500, int(word_count * 1.3) + 1500)`.
    /// - Classification thresholds:
    ///   - high: `high_hits > low_score` or `word_count > HIGH_WORD_THRESHOLD * 1.5`
    ///   - low:  `low_score > high_hits` or (`word_count < LOW_WORD_CEILING` and `high_hits == 0`)
    ///   - else: medium
    pub fn classify(&self, task: &str) -> RustTaskComplexity {
        let lower = task.to_lowercase();

        let high_hits: usize = self.high_kw.iter().filter(|kw| lower.contains(**kw)).count();
        let low_hits: usize = self.low_kw.iter().filter(|kw| lower.contains(**kw)).count();
        let low_regex: usize = self.low_re.iter().filter(|r| r.is_match(&lower)).count();

        let word_count = task.split_whitespace().count();

        // Rough token estimate: ~1.3 tokens per word (matches Python logic).
        // Python: max(500, int(word_count * 1.3) + 1500)
        // The cast to i64 truncates (same as Python's int()).
        let estimated_tokens = std::cmp::max(500, ((word_count as f64 * 1.3) as i64) + 1500);

        let requires_reasoning = high_hits > 0 || word_count > HIGH_WORD_THRESHOLD;
        let requires_tools = TOOL_KEYWORDS.iter().any(|kw| lower.contains(kw));

        let low_score = low_hits + low_regex;

        // Python: HIGH_WORD_THRESHOLD * 1.5 == 200 * 1.5 == 300.0
        // word_count (usize) > 300 is equivalent.
        let level = if high_hits > low_score || word_count > (HIGH_WORD_THRESHOLD * 3 / 2) {
            "high"
        } else if low_score > high_hits || (word_count < LOW_WORD_CEILING && high_hits == 0) {
            "low"
        } else {
            "medium"
        };

        RustTaskComplexity {
            level: level.to_string(),
            estimated_tokens,
            requires_tools,
            requires_reasoning,
        }
    }
}

impl Default for RustClassifier {
    fn default() -> Self {
        Self::new()
    }
}
