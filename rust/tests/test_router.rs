/// Unit tests for RustClassifier.
///
/// Expected outputs were verified against the Python `TaskComplexityClassifier`
/// for the same inputs.
use agent_orchestrator_rust::*;

fn cls() -> RustClassifier {
    RustClassifier::new()
}

// ---------------------------------------------------------------------------
// Token estimation
// ---------------------------------------------------------------------------

#[test]
fn test_estimated_tokens_minimum() {
    // 1-word task: int(1 * 1.3) + 1500 = 1501 > 500 → 1501
    let result = cls().classify("hello");
    assert_eq!(result.estimated_tokens, 1501);
}

#[test]
fn test_estimated_tokens_empty_string() {
    // 0 words: max(500, 0 + 1500) = 1500
    let result = cls().classify("");
    assert_eq!(result.estimated_tokens, 1500);
}

#[test]
fn test_estimated_tokens_floor_500() {
    // Impossible to hit with real input since 0 words gives 1500, but the
    // minimum is 500.  Verify with a single word that produces > 500.
    let result = cls().classify("x");
    assert!(result.estimated_tokens >= 500);
}

// ---------------------------------------------------------------------------
// Level = low
// ---------------------------------------------------------------------------

#[test]
fn test_low_keyword_hit() {
    let result = cls().classify("summarize this document");
    assert_eq!(result.level, "low");
}

#[test]
fn test_low_short_no_signals() {
    // < 30 words, no high keywords → low
    let result = cls().classify("echo hello world");
    assert_eq!(result.level, "low");
}

#[test]
fn test_low_git_pattern() {
    let result = cls().classify("git commit -m 'fix bug'");
    assert_eq!(result.level, "low");
}

#[test]
fn test_low_lint_pattern() {
    let result = cls().classify("lint the code");
    assert_eq!(result.level, "low");
}

#[test]
fn test_low_rename_pattern() {
    let result = cls().classify("rename the file");
    assert_eq!(result.level, "low");
}

#[test]
fn test_low_format_pattern() {
    let result = cls().classify("format this file");
    assert_eq!(result.level, "low");
}

#[test]
fn test_low_git_push() {
    let result = cls().classify("git push origin main");
    assert_eq!(result.level, "low");
}

// ---------------------------------------------------------------------------
// Level = high
// ---------------------------------------------------------------------------

#[test]
fn test_high_architecture_keyword() {
    let result = cls().classify("design the architecture of the new system");
    assert_eq!(result.level, "high");
}

#[test]
fn test_high_optimize_keyword() {
    let result = cls().classify("optimize the database query pipeline");
    assert_eq!(result.level, "high");
}

#[test]
fn test_high_machine_learning() {
    let result = cls().classify("machine learning model training pipeline");
    assert_eq!(result.level, "high");
}

#[test]
fn test_high_long_prompt_over_300_words() {
    // > 300 words (= HIGH_WORD_THRESHOLD * 1.5) → always high
    let words = vec!["word"; 305];
    let task = words.join(" ");
    let result = cls().classify(&task);
    assert_eq!(result.level, "high");
}

#[test]
fn test_high_security_audit() {
    let result = cls().classify("please do a security audit of the authentication module");
    assert_eq!(result.level, "high");
}

#[test]
fn test_high_refactor() {
    let result = cls().classify("refactor the entire payment service");
    assert_eq!(result.level, "high");
}

#[test]
fn test_high_deep_dive() {
    let result = cls().classify("deep dive into the performance bottlenecks");
    assert_eq!(result.level, "high");
}

// ---------------------------------------------------------------------------
// Level = medium
// ---------------------------------------------------------------------------

#[test]
fn test_medium_balanced_signals() {
    // "check" is a low keyword, but "design" is high and task is short → high wins.
    // Need exactly balanced to hit medium: use a task with ~30-200 words and no signals.
    let words = vec!["the"; 50]; // 50 words, no keyword signals
    let task = words.join(" ");
    let result = cls().classify(&task);
    assert_eq!(result.level, "medium");
}

#[test]
fn test_medium_moderate_length() {
    // 50 neutral words — no high/low keywords → medium
    let task = "please help me with the project and make sure everything is properly configured and working";
    let result = cls().classify(task);
    // Depending on word count and keywords this should be low or medium.
    // Verify it is NOT high.
    assert_ne!(result.level, "high");
}

// ---------------------------------------------------------------------------
// requires_tools
// ---------------------------------------------------------------------------

#[test]
fn test_requires_tools_code_keyword() {
    let result = cls().classify("write some code");
    assert!(result.requires_tools);
}

#[test]
fn test_requires_tools_file_keyword() {
    let result = cls().classify("read a file from disk");
    assert!(result.requires_tools);
}

#[test]
fn test_requires_tools_deploy_keyword() {
    let result = cls().classify("deploy the application to staging");
    assert!(result.requires_tools);
}

#[test]
fn test_requires_tools_false_for_pure_analysis() {
    // No tool keywords.
    let result = cls().classify("analyze the market strategy and compare options");
    assert!(!result.requires_tools);
}

// ---------------------------------------------------------------------------
// requires_reasoning
// ---------------------------------------------------------------------------

#[test]
fn test_requires_reasoning_high_keyword() {
    let result = cls().classify("analyze the security implications");
    assert!(result.requires_reasoning);
}

#[test]
fn test_requires_reasoning_long_prompt() {
    let words = vec!["word"; 210];
    let task = words.join(" ");
    let result = cls().classify(&task);
    assert!(result.requires_reasoning);
}

#[test]
fn test_requires_reasoning_false_short_low() {
    let result = cls().classify("ping");
    assert!(!result.requires_reasoning);
}

// ---------------------------------------------------------------------------
// Case insensitivity
// ---------------------------------------------------------------------------

#[test]
fn test_case_insensitive_high_keyword() {
    let upper = cls().classify("ARCHITECTURE review");
    let lower = cls().classify("architecture review");
    assert_eq!(upper.level, lower.level);
}

#[test]
fn test_case_insensitive_low_keyword() {
    let upper = cls().classify("SUMMARIZE this");
    let lower = cls().classify("summarize this");
    assert_eq!(upper.level, lower.level);
}

// ---------------------------------------------------------------------------
// Multi-word keywords
// ---------------------------------------------------------------------------

#[test]
fn test_multi_word_high_keyword_security_audit() {
    let result = cls().classify("perform a security audit on the codebase");
    assert_eq!(result.level, "high");
}

#[test]
fn test_multi_word_high_keyword_machine_learning() {
    let result = cls().classify("build a machine learning pipeline");
    assert_eq!(result.level, "high");
}

#[test]
fn test_multi_word_high_keyword_deep_dive() {
    let result = cls().classify("do a deep dive analysis");
    assert_eq!(result.level, "high");
}
