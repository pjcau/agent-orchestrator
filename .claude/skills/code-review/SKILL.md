---
name: code-review
description: Review code changes for quality, security, and correctness. Use this before merging PRs, after significant refactors, or when reviewing agent output.
disable-model-invocation: true
allowed-tools: Bash, Read, Grep, Glob
---

# Code Review — Automated Quality Check

Review staged or committed changes for common issues.

## Step 1: Get the diff

```bash
# For staged changes
git diff --cached --stat
git diff --cached

# For branch changes vs main
git diff main...HEAD --stat
git diff main...HEAD
```

## Step 2: Check each file for

1. **Security** — hardcoded secrets, SQL injection, command injection, SSRF
2. **Correctness** — logic errors, missing error handling at boundaries, race conditions
3. **Style** — naming conventions, dead code, overly complex functions
4. **Tests** — new code should have tests, changed code should update tests
5. **Provider agnosticism** — no vendor lock-in (must work with any LLM provider)

## Step 3: Report

Format findings as:

```
## Code Review Summary

### Critical
- [file:line] Description of critical issue

### Warnings
- [file:line] Description of warning

### Suggestions
- [file:line] Optional improvement

### Verdict: APPROVE / REQUEST_CHANGES
```
