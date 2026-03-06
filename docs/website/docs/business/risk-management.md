---
sidebar_position: 4
title: Risk Management
---

# Risk Management

## Risk Matrix

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| OpenRouter price increase | Medium | Medium | Multi-provider routing, DashScope fallback |
| OpenRouter rate limits | Low | High | Aggressive caching, tier upgrade |
| Vast.ai interruption (Phase 4) | Medium | Medium | OpenRouter as automatic fallback |
| Token costs out of control | Medium | High | Hard budget cap, alerts, prompt caching |
| EC2 downtime | Low | High | CloudWatch auto-recovery + Lambda |
| Low user adoption | Medium | High | Iterate on use cases, pivot pricing, open-source core |
| Provider API breaking changes | Medium | Medium | Provider abstraction layer isolates impact |

## Break-Even Analysis

Self-hosted H200 becomes cheaper than OpenRouter when:

> Monthly OpenRouter spend > 545 EUR (Vast.ai GPU cost)

With Qwen3 30B at $0.08/$0.28 per 1M tokens, this means:

- ~7.8 billion input tokens/month at pure cost parity
- ~260 million tokens/day — enterprise-level usage

**Conclusion:** The switch to self-hosted is driven by **capabilities** (fine-tuning, privacy, latency) not by token cost savings alone.

## Financial Summary

```
Phase 1 (M1-2)      Phase 2 (M2-4)      Phase 3 (M4-6)      Phase 4 (M6+)
 42 EUR/mo            42-100 EUR/mo       100-300 EUR/mo       625+ EUR/mo

Break-even:          Break-even:          Break-even:          Break-even:
5 users @ 10 EUR     15 users or          Revenue > 300 EUR    Revenue > 1000 EUR
                     2-3 Pro @ 100 EUR
```
