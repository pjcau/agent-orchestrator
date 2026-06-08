//! ANSI rendering for assistant output.
//!
//! Visually distinguishes fenced code blocks (``` ... ```) from prose
//! without paying the cost of a full syntax-highlighting engine like
//! syntect (~3 MB of bundled assets, big regex dependency tree).
//!
//! Two affordances:
//!   * code lines get a left bar (`│`) in dim cyan, so the block stands
//!     out at a glance;
//!   * the language tag on the opening fence (``` rust) becomes a small
//!     header line above the block.
//!
//! Disabled automatically when:
//!   * stdout is not a TTY (output is being piped to `wc`, a file, etc.),
//!   * the `NO_COLOR` env var is set (https://no-color.org),
//!   * `--no-color` is on the CLI (passed in via the `force_plain` arg).
//!
//! True per-token highlighting (Rust keywords coloured, strings green,
//! etc.) can be layered on top of this in a later release by swapping
//! the `color_code_line` body — the surrounding fence machinery is
//! engine-agnostic.

use std::io::IsTerminal;

const RESET: &str = "\x1b[0m";
const DIM: &str = "\x1b[2m";
const BOLD: &str = "\x1b[1m";
const CYAN: &str = "\x1b[36m";
const DIM_CYAN: &str = "\x1b[2;36m";
const YELLOW: &str = "\x1b[33m";

/// Decide whether colour should be emitted on this run.
/// Pure function so tests can pin it without touching env state.
pub fn should_colorize(force_plain: bool) -> bool {
    if force_plain {
        return false;
    }
    if std::env::var_os("NO_COLOR").is_some() {
        return false;
    }
    std::io::stdout().is_terminal()
}

/// Render `text` into ANSI-formatted output. When colour is disabled
/// returns `text` unchanged so the function is safe to use in pipes.
pub fn highlight(text: &str, force_plain: bool) -> String {
    if !should_colorize(force_plain) {
        return text.to_string();
    }
    highlight_always(text)
}

/// Render assuming colour is on. Used directly by unit tests so the
/// terminal-detection branch above does not interfere.
pub fn highlight_always(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 64);
    let mut in_code = false;
    let mut lang: String = String::new();
    for line in text.split_inclusive('\n') {
        // Detach trailing newline so we don't accidentally inject ANSI
        // between the bar and the `\n` (some terminals leak the colour
        // onto the next line otherwise).
        let (body, nl) = match line.strip_suffix('\n') {
            Some(b) => (b, "\n"),
            None => (line, ""),
        };
        if let Some(tag) = body.trim_start().strip_prefix("```") {
            if in_code {
                // Closing fence.
                in_code = false;
                lang.clear();
                out.push_str(&format!("{DIM}└─{RESET}{nl}"));
            } else {
                // Opening fence — capture the lang tag (may be empty).
                in_code = true;
                lang = tag.trim().to_string();
                if lang.is_empty() {
                    out.push_str(&format!("{DIM}┌─{RESET}{nl}"));
                } else {
                    out.push_str(&format!("{DIM_CYAN}┌─ {lang}{RESET}{nl}"));
                }
            }
            continue;
        }
        if in_code {
            out.push_str(&format!("{DIM_CYAN}│{RESET} {CYAN}{body}{RESET}{nl}"));
        } else {
            out.push_str(&format_prose_line(body));
            out.push_str(nl);
        }
    }
    out
}

/// Colour inline markdown in a single prose line (never a fenced-code line).
/// ATX headings (`#`..`######`) → bold cyan; `**bold**` → bold; `` `code` ``
/// → cyan; a leading list marker (`-`/`*`/`+`/`N.`) → yellow. Unmatched
/// markers are left literal so an unclosed `**` or `` ` `` never leaks an
/// ANSI sequence onto the rest of the output.
fn format_prose_line(body: &str) -> String {
    let indent_len = body.len() - body.trim_start().len();
    let (indent, rest) = body.split_at(indent_len);

    if is_heading(rest) {
        return format!("{indent}{BOLD}{CYAN}{rest}{RESET}");
    }
    if let Some(marker_len) = list_marker_len(rest) {
        let (marker, after) = rest.split_at(marker_len);
        return format!("{indent}{YELLOW}{marker}{RESET}{}", style_inline(after));
    }
    format!("{indent}{}", style_inline(rest))
}

/// 1–6 leading `#` followed by a space — a Markdown ATX heading.
fn is_heading(s: &str) -> bool {
    let hashes = s.bytes().take_while(|&b| b == b'#').count();
    (1..=6).contains(&hashes) && s.as_bytes().get(hashes) == Some(&b' ')
}

/// Length (incl. trailing space) of a leading list marker, if any.
/// Matches `- `, `* `, `+ ` and ordered `N. ` / `N) `.
fn list_marker_len(s: &str) -> Option<usize> {
    let b = s.as_bytes();
    if b.len() >= 2 && matches!(b[0], b'-' | b'*' | b'+') && b[1] == b' ' {
        return Some(2);
    }
    let digits = b.iter().take_while(|c| c.is_ascii_digit()).count();
    if digits > 0
        && matches!(b.get(digits), Some(b'.') | Some(b')'))
        && b.get(digits + 1) == Some(&b' ')
    {
        return Some(digits + 2);
    }
    None
}

/// Apply `**bold**` and `` `code` `` styling within a line. Only matched
/// pairs are styled; a lone marker is emitted verbatim.
fn style_inline(s: &str) -> String {
    let chars: Vec<char> = s.chars().collect();
    let mut out = String::with_capacity(s.len() + 16);
    let mut i = 0;
    while i < chars.len() {
        if chars[i] == '`' {
            if let Some(close) = (i + 1..chars.len()).find(|&j| chars[j] == '`') {
                out.push_str(CYAN);
                out.extend(&chars[i + 1..close]);
                out.push_str(RESET);
                i = close + 1;
                continue;
            }
        }
        if chars[i] == '*' && chars.get(i + 1) == Some(&'*') {
            if let Some(close) = find_double_star(&chars, i + 2) {
                out.push_str(BOLD);
                out.extend(&chars[i + 2..close]);
                out.push_str(RESET);
                i = close + 2;
                continue;
            }
        }
        out.push(chars[i]);
        i += 1;
    }
    out
}

/// Index of the next `**` at or after `from`, if any.
fn find_double_star(chars: &[char], from: usize) -> Option<usize> {
    let mut j = from;
    while j + 1 < chars.len() {
        if chars[j] == '*' && chars[j + 1] == '*' {
            return Some(j);
        }
        j += 1;
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plain_text_passes_through_unchanged() {
        let s = "just some words\nand another line\n";
        assert_eq!(highlight_always(s), s);
    }

    #[test]
    fn fenced_block_gets_bar_prefix() {
        let s = "before\n```\ncode line\n```\nafter\n";
        let out = highlight_always(s);
        assert!(out.contains("┌─"));
        assert!(out.contains("└─"));
        assert!(out.contains("│"));
        assert!(out.contains("code line"));
        assert!(out.contains("before"));
        assert!(out.contains("after"));
    }

    #[test]
    fn lang_tag_shown_in_header() {
        let s = "```rust\nfn main(){}\n```\n";
        let out = highlight_always(s);
        assert!(out.contains("rust"), "lang tag must appear: {out:?}");
    }

    #[test]
    fn nested_or_unclosed_fence_does_not_panic() {
        // Unclosed code block — function must still terminate.
        let s = "```\nhalf-open\n";
        let out = highlight_always(s);
        assert!(out.contains("half-open"));
    }

    #[test]
    fn force_plain_disables_color() {
        let s = "```\nfoo\n```\n";
        assert_eq!(highlight(s, true), s);
    }

    #[test]
    fn no_color_env_disables_color() {
        // Save / restore the env so test order doesn't matter.
        let prev = std::env::var_os("NO_COLOR");
        std::env::set_var("NO_COLOR", "1");
        let s = "```\nfoo\n```\n";
        // Bypass terminal detection by calling should_colorize directly.
        assert!(!should_colorize(false));
        match prev {
            Some(v) => std::env::set_var("NO_COLOR", v),
            None => std::env::remove_var("NO_COLOR"),
        }
        let _ = s;
    }

    #[test]
    fn fence_with_leading_whitespace_detected() {
        let s = "   ```\nindented\n   ```\n";
        let out = highlight_always(s);
        assert!(out.contains("┌─"));
        assert!(out.contains("└─"));
    }

    #[test]
    fn bold_span_is_styled() {
        let out = highlight_always("a **Project Status** b\n");
        assert!(out.contains(BOLD), "expected bold sequence: {out:?}");
        assert!(out.contains("Project Status"));
        // The markers themselves are consumed.
        assert!(!out.contains("**"));
    }

    #[test]
    fn inline_code_is_styled() {
        let out = highlight_always("run `README.md` now\n");
        assert!(out.contains(CYAN));
        assert!(out.contains("README.md"));
        assert!(!out.contains('`'));
    }

    #[test]
    fn atx_heading_is_bold() {
        let out = highlight_always("## Results\n");
        assert!(out.contains(BOLD));
        assert!(out.contains("## Results"));
    }

    #[test]
    fn list_markers_are_styled() {
        let bullet = highlight_always("- first item\n");
        assert!(bullet.contains(YELLOW), "bullet marker: {bullet:?}");
        assert!(bullet.contains("first item"));
        let ordered = highlight_always("1. step one\n");
        assert!(ordered.contains(YELLOW), "ordered marker: {ordered:?}");
        assert!(ordered.contains("step one"));
    }

    #[test]
    fn bullet_star_not_confused_with_bold() {
        // A leading "* " is a list bullet, not the start of **bold**.
        let out = highlight_always("* a bullet\n");
        assert!(out.contains(YELLOW));
        assert!(out.contains("a bullet"));
    }

    #[test]
    fn unclosed_bold_marker_left_literal() {
        // A lone ** must not leak an ANSI sequence.
        let out = highlight_always("price is **5 dollars\n");
        assert!(!out.contains(BOLD));
        assert!(out.contains("**5 dollars"));
    }

    #[test]
    fn inline_markup_skipped_inside_code_fence() {
        // **bold** and `code` inside a fence stay literal (code semantics win).
        let out = highlight_always("```\nx = **2** and `y`\n```\n");
        assert!(out.contains("**2**"));
        assert!(out.contains("`y`"));
    }

    #[test]
    fn force_plain_leaves_markdown_untouched() {
        let s = "## H\n**bold** and `code`\n- item\n";
        assert_eq!(highlight(s, true), s);
    }
}
