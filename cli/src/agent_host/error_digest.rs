//! Error-aware digest of a failing command's output (feature "A").
//!
//! When a `shell_exec` fails, its output is often large (a pytest run, a cargo
//! build, a `docker compose` log). The context cap that folds the result back
//! into the LLM keeps only a head+tail slice — which preserves the *summary*
//! (`34 errors`) but elides the **middle**, where the actual diagnosis lives
//! (the first traceback, the `OperationalError: …` line, the `error[E0599]`).
//! The agent then sees *that* it failed but not *why*, and cannot converge.
//!
//! The CLI holds the full output before sending, so it can do better than blind
//! head+tail: keep the head, the error-salient lines (with a line of context),
//! and the tail. It also extracts a normalised **signature** of the dominant
//! error so the thrash guard can detect "same failure, different command"
//! (feature "B").

/// Lower-cased substrings that mark a line as error-salient. Curated to catch
/// the common toolchains (pytest, cargo/rustc, tsc, gcc, node, docker, psql)
/// without matching every "warning". Only applied to *failed* commands.
const ERROR_MARKERS: &[&str] = &[
    "error",
    "exception",
    "traceback",
    "panic",
    "failed",
    "assert",
    "could not",
    "cannot",
    "no such",
    "not found",
    "refused",
    "denied",
    "fatal",
    "undefined",
    "unresolved",
    "expected",
];

fn is_error_line(line: &str) -> bool {
    let l = line.to_ascii_lowercase();
    // pytest marks error/failure lines with a leading "E   ".
    if line.trim_start().starts_with("E   ") || line.starts_with("E\t") {
        return true;
    }
    ERROR_MARKERS.iter().any(|m| l.contains(m))
}

/// Join stdout + stderr for scanning. stderr first: most toolchains put the
/// conclusive error there, and pytest's captured traceback still shows on stdout.
fn combined(stdout: &str, stderr: &str) -> String {
    match (stdout.is_empty(), stderr.is_empty()) {
        (false, false) => format!("{stdout}\n--- stderr ---\n{stderr}"),
        (false, true) => stdout.to_string(),
        (true, false) => stderr.to_string(),
        (true, true) => String::new(),
    }
}

/// Truncate `s` to ≤ `max` bytes on a char boundary, biased to keep `head_frac`
/// of the budget at the start and the rest at the end (errors cluster at both
/// the first failure and the final summary).
fn clamp_head_tail(s: &str, max: usize, head_frac: f64) -> String {
    if s.len() <= max {
        return s.to_string();
    }
    let marker = "\n…\n";
    let budget = max.saturating_sub(marker.len());
    let head_len = (budget as f64 * head_frac) as usize;
    let tail_len = budget - head_len;
    let mut h = head_len;
    while h > 0 && !s.is_char_boundary(h) {
        h -= 1;
    }
    let mut t = s.len() - tail_len;
    while t < s.len() && !s.is_char_boundary(t) {
        t += 1;
    }
    format!("{}{}{}", &s[..h], marker, &s[t..])
}

/// Build a compact, error-focused excerpt of a failing command's output:
/// head lines + error-salient lines (with one line of trailing context) + tail
/// lines, gaps marked with `…`, the whole thing clamped to `max_bytes`.
pub fn salient_excerpt(stdout: &str, stderr: &str, max_bytes: usize) -> String {
    const HEAD: usize = 6;
    const TAIL: usize = 12;
    let text = combined(stdout, stderr);
    let lines: Vec<&str> = text.lines().collect();
    let n = lines.len();
    if n == 0 {
        return String::new();
    }

    let mut keep = vec![false; n];
    for k in keep.iter_mut().take(HEAD.min(n)) {
        *k = true;
    }
    for k in keep.iter_mut().skip(n.saturating_sub(TAIL)) {
        *k = true;
    }
    for i in 0..n {
        if is_error_line(lines[i]) {
            keep[i] = true;
            if i + 1 < n {
                keep[i + 1] = true; // a line of context after the marker
            }
        }
    }

    let mut out = String::new();
    let mut last: Option<usize> = None;
    for (i, keep_it) in keep.iter().enumerate() {
        if *keep_it {
            if let Some(p) = last {
                if i > p + 1 {
                    out.push_str("…\n");
                }
            }
            out.push_str(lines[i]);
            out.push('\n');
            last = Some(i);
        }
    }
    clamp_head_tail(out.trim_end(), max_bytes, 0.4)
}

/// Substrings that mark a line as the *conclusive* error worth fingerprinting.
const SIGNATURE_MARKERS: &[&str] = &[
    "error:",
    "error :",
    "exception:",
    "panicked",
    "assertionerror",
    "could not",
    "no such",
    "not found",
    "refused",
    "denied",
    "fatal:",
    "failed:",
];

/// Extract a normalised fingerprint of the dominant error, for no-progress
/// detection. Returns `None` when nothing error-like is present. The *last*
/// matching line wins: in a Python traceback the root exception is last, and
/// compiler runs end on their summary error.
pub fn error_signature(stdout: &str, stderr: &str) -> Option<String> {
    let text = combined(stdout, stderr);
    let mut candidate: Option<&str> = None;
    for line in text.lines() {
        let t = line.trim();
        if t.is_empty() {
            continue;
        }
        let l = t.to_ascii_lowercase();
        if SIGNATURE_MARKERS.iter().any(|m| l.contains(m)) {
            candidate = Some(t);
        }
    }
    candidate.map(normalize_signature)
}

/// Normalise a salient error line so the same root cause fingerprints
/// identically across attempts: strip a leading pytest `E ` / `path:line:col:`
/// prefix, collapse whitespace, drop volatile hex addresses, lower-case, cap.
fn normalize_signature(line: &str) -> String {
    let mut s = line.trim();
    // Strip a leading pytest error prefix ("E   ").
    if let Some(rest) = s.strip_prefix("E ") {
        s = rest.trim_start();
    }
    // Strip a leading "path:line:col:" / "path:line:" location prefix.
    let s = strip_location_prefix(s);
    // Collapse whitespace, drop hex addresses, lower-case, cap length.
    let collapsed: String = s.split_whitespace().collect::<Vec<_>>().join(" ");
    let no_hex = drop_hex_addrs(&collapsed);
    let mut out: String = no_hex.to_ascii_lowercase();
    if out.len() > 160 {
        let mut end = 160;
        while end > 0 && !out.is_char_boundary(end) {
            end -= 1;
        }
        out.truncate(end);
    }
    out
}

/// Drop a leading `path:NN:` or `path:NN:CC:` location prefix if present.
fn strip_location_prefix(s: &str) -> &str {
    // Find " error" / ": " split heuristically: if the head before the first
    // space contains ":<digits>:", treat everything up to the last such colon
    // group as a location prefix and strip it.
    if let Some(first_space) = s.find(' ') {
        let head = &s[..first_space];
        if head.contains(':') && head.chars().any(|c| c.is_ascii_digit()) {
            // e.g. "tests/test_auth.py:42:" → keep the rest after the prefix.
            if let Some((_, rest)) = s.split_once(": ") {
                return rest;
            }
        }
    }
    s
}

/// Replace `0x…`-style hex addresses with a placeholder so they don't make two
/// otherwise-identical errors fingerprint differently.
fn drop_hex_addrs(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut chars = s.char_indices().peekable();
    while let Some((i, c)) = chars.next() {
        if c == '0' && s[i..].starts_with("0x") {
            out.push_str("0x");
            // skip the "x" and the following hex digits
            chars.next();
            while let Some(&(_, h)) = chars.peek() {
                if h.is_ascii_hexdigit() {
                    chars.next();
                } else {
                    break;
                }
            }
        } else {
            out.push(c);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    const PYTEST: &str = "\
============================= test session starts =====
collected 40 items

tests/test_api.py EEEEEEEE.....F
tests/test_auth.py EEEEEEEE

==================================== ERRORS ====================================
____ ERROR at setup of TestHealth.test_health_returns_ok ____
    self._dbapi_connection = engine.raw_connection()
E   sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) could not translate host name \"db\" to address: Temporary failure in name resolution
(many more identical errors)
============= 7 failed, 1 passed, 33 errors in 12.16s =============";

    #[test]
    fn excerpt_keeps_the_root_error_not_just_head_and_tail() {
        let out = salient_excerpt(PYTEST, "", 4000);
        // The middle error line — which blind head+tail would elide — survives.
        assert!(out.contains("could not translate host name"), "got:\n{out}");
        // And the summary tail survives too.
        assert!(out.contains("33 errors"));
    }

    #[test]
    fn excerpt_is_bounded_and_char_safe() {
        let big = format!(
            "head\n{}\nError: boom\n{}\ntail summary",
            "x".repeat(9000),
            "y".repeat(9000)
        );
        let out = salient_excerpt(&big, "", 1000);
        assert!(out.len() <= 1000, "len={}", out.len());
        assert!(out.contains("Error: boom") || out.contains("…"));
        // Multibyte safety.
        let uni = format!(
            "{}\nErrore: però — × fallito\n{}",
            "à".repeat(800),
            "ò".repeat(800)
        );
        let _ = salient_excerpt(&uni, "", 200); // must not panic
    }

    #[test]
    fn signature_is_stable_for_the_same_root_error() {
        let s1 = error_signature(PYTEST, "");
        let s2 = error_signature(&PYTEST.replace("12.16s", "9.02s"), ""); // timing differs
        assert!(s1.is_some());
        assert_eq!(s1, s2, "signature should ignore volatile timing");
        assert!(s1.unwrap().contains("could not translate host name"));
    }

    #[test]
    fn signature_differs_for_different_errors() {
        let a = error_signature("E   ValueError: bad input", "").unwrap();
        let b = error_signature("E   KeyError: 'missing'", "").unwrap();
        assert_ne!(a, b);
    }

    #[test]
    fn signature_none_when_no_error() {
        assert_eq!(error_signature("all good\n2 passed", ""), None);
    }

    #[test]
    fn signature_ignores_line_numbers_and_hex() {
        let a = error_signature("foo.rs:10:5: error: mismatched types at 0x7f1a", "").unwrap();
        let b = error_signature("foo.rs:88:9: error: mismatched types at 0x9c22", "").unwrap();
        assert_eq!(a, b);
    }
}
