---
sidebar_position: 6
title: Financial Summary
---

# Financial Summary

## Cost Progression

```mermaid
graph LR
    P0["Phase 0 (NOW)<br/>AWS + Monitoring<br/><b>42 EUR/mo</b>"]
    P1["Phase 1 (M1)<br/>Agent Autonomy<br/><b>42 EUR/mo</b>"]
    P2["Phase 2 (M2-4)<br/>Optimization<br/><b>42-100 EUR/mo</b>"]
    P3["Phase 3 (M4-6)<br/>Platform Maturity<br/><b>100-300 EUR/mo</b>"]
    P4["Phase 4 (M6+)<br/>Hybrid Scaling<br/><b>625+ EUR/mo</b>"]

    P0 --> P1 --> P2 --> P3 --> P4

    style P0 fill:#7bc67e,color:#fff
    style P1 fill:#4a90d9,color:#fff
    style P2 fill:#e6a23c,color:#fff
    style P3 fill:#d94a4a,color:#fff
    style P4 fill:#9b59b6,color:#fff
```

## Phase 4 Cost Breakdown

| Item | EUR/month |
|------|-----------|
| AWS EC2 + S3 + networking | 80 |
| Vast.ai H200 interruptible (252h/month inference) | 305 |
| Vast.ai H200 on-demand (108h/month fine-tuning) | 241 |
| OpenRouter (overflow/fallback) | 30 est. |
| **Total** | **~656** |

## Break-Even Analysis

- **Phase 0-1:** Infrastructure investment, no revenue yet
- **Phase 2:** Profitable at ~5 paying users (10 EUR/month each)
- **Phase 3:** Profitable at ~15 paying users or 2-3 Pro users (100 EUR/month)
- **Phase 4:** Self-hosted GPU pays for itself when OpenRouter spend would exceed 545 EUR/month
