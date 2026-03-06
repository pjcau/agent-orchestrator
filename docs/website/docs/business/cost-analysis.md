---
sidebar_position: 2
title: Cost Analysis
---

# Cost Analysis

## Phase 1 Costs (Conservative)

| Item | $/month | EUR/month |
|---|---|---|
| EC2 t3.medium (orchestrator 24/7) | $30 | 28 |
| EBS 50GB + Elastic IP + transfer | $12 | 11 |
| AWS S3 storage | $0 | 0 (free tier) |
| OpenRouter Qwen3 30B inference | ~$3 | ~3 |
| **Total** | **~$45** | **~42** |

*Based on ~18M input tokens + 4.5M output tokens/month (agents 12h/day)*

## OpenRouter Pricing (Qwen3 30B A3B)

- Input: **$0.08 / 1M tokens**
- Output: **$0.28 / 1M tokens**
- No fixed cost, no restrictive rate limits

## Provider Pricing Comparison

| Provider | Model | Input $/1M | Output $/1M | Context |
|----------|-------|------------|-------------|---------|
| Anthropic | Claude Opus 4 | $15.00 | $75.00 | 200K |
| Anthropic | Claude Sonnet 4 | $3.00 | $15.00 | 200K |
| OpenAI | GPT-4o | $2.50 | $10.00 | 128K |
| Google | Gemini 2.0 Flash | $0.075 | $0.30 | 1M |
| DeepSeek | V3 | $0.27 | $1.10 | 128K |

## Hybrid Routing Savings

| Task Type | % of traffic | Provider | Cost |
|-----------|-------------|----------|------|
| Simple (lint, format) | 70% | Gemini Flash | $0.30/1M out |
| Medium (features) | 20% | Sonnet / GPT-4o | $10-15/1M out |
| Complex (architecture) | 10% | Opus / o3 | $40-75/1M out |

**Result:** 60-80% cost reduction vs all-cloud frontier models.

## Token Optimization

1. **Context pruning** — only relevant snippets, not full files
2. **Caching** — reuse completions for identical prompts
3. **Prompt compression** — strip comments for simple tasks
4. **Batching** — group related simple tasks
5. **Streaming** — cancel early on wrong approach
6. **RTK-style filtering** — filter tool output before context (60-90% savings)
