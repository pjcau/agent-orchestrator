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
const CYAN: &str = "\x1b[36m";
const DIM_CYAN: &str = "\x1b[2;36m";

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
            out.push_str(body);
            out.push_str(nl);
        }
    }
    out
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
}
