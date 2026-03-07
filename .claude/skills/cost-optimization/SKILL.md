---
name: cost-optimization
description: Review and optimize LLM API costs in the orchestrator. Analyze model routing, budget tracking, prompt caching, and retry logic for cost efficiency.
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Bash
---

# Cost Optimization — LLM Pipeline Efficiency

Patterns for controlling LLM API costs. Adapted from flatrick/everything-claude-code cost-aware-llm-pipeline.

## When to Use

- Reviewing or optimizing provider cost configuration
- Adding new models and need to set pricing correctly
- Investigating unexpected cost spikes
- Planning batch processing jobs

## Review Checklist

### 1. Model Pricing Accuracy
```bash
# Check all model pricing in provider catalogs
grep -n "input_cost\|output_cost" src/agent_orchestrator/providers/openrouter.py
```
Verify prices match current OpenRouter/provider rates.

### 2. Router Configuration
```bash
# Check complexity classifier thresholds
grep -n "THRESHOLD\|_KEYWORDS\|_PATTERNS" src/agent_orchestrator/core/router.py
```
Ensure:
- Low-complexity tasks route to cheap/free models
- Only high-complexity tasks use expensive models
- Medium tasks use mid-tier pricing

### 3. Budget Enforcement
```bash
# Check budget settings
grep -rn "budget\|cost_budget" src/agent_orchestrator/core/
```
Verify:
- Per-task budget limits are set
- Session budget limits are set
- Budget exceeded triggers graceful stop (not crash)

### 4. Retry Cost Control
```bash
# Check retry configuration
grep -rn "max_retries\|retry\|fallback" src/agent_orchestrator/
```
Ensure:
- Retries only on transient errors (429, 500, connection)
- No retries on 400, 401, 403 (wastes budget on permanent failures)
- Fallback chain prefers cheaper models

### 5. Token Usage
```bash
# Check max_tokens defaults
grep -rn "max_tokens" src/agent_orchestrator/
```
Ensure max_tokens is not unnecessarily high for simple tasks.

## Cost Tiers Reference

| Tier | Use Case | Target Cost |
|------|----------|-------------|
| Free | Simple lookups, formatting | $0/M tokens |
| Low  | Summaries, translations | < $1/M output |
| Mid  | Feature work, debugging | $1-5/M output |
| High | Architecture, deep analysis | $5+/M output |

## Anti-Patterns

- Using expensive models for all requests regardless of complexity
- Retrying on all errors (wastes budget on permanent failures)
- No budget limits on batch operations
- Hardcoded model names scattered through code (use catalog)
- Ignoring prompt caching for repetitive system prompts
