# DeerFlow Adoption Roadmap — Feature Specs

14 feature specs derived from the [DeerFlow 2.0 deep analysis](../../analysis/deepflow/).
Each file is a self-contained spec ready to be passed to `/feature`.

## Execution Order

Run each feature in order. Each builds on the previous.

### Phase 1: Safety & Resilience (Critical — 2.5 days)
| # | File | Feature | Effort |
|---|------|---------|--------|
| 01 | `01-loop-detection.md` | Loop detection middleware | 1 day |
| 02 | `02-dangling-tool-recovery.md` | Dangling tool call recovery | 0.5 day |
| 03 | `03-tool-description-param.md` | Tool description parameter | 1 day |

### Phase 2: Context Efficiency (High — 4 days)
| # | File | Feature | Effort |
|---|------|---------|--------|
| 04 | `04-progressive-skill-loading.md` | Progressive skill loading | 2 days |
| 05 | `05-context-summarization.md` | Configurable context summarization | 2 days |

### Phase 3: Developer Experience (High — 6 days)
| # | File | Feature | Effort |
|---|------|---------|--------|
| 06 | `06-embedded-client.md` | OrchestratorClient (programmatic API) | 3 days |
| 07 | `07-yaml-config.md` | YAML config with reflection | 3 days |

### Phase 4: Agent Capabilities (Medium — 10 days)
| # | File | Feature | Effort |
|---|------|---------|--------|
| 08 | `08-clarification-system.md` | Structured clarification (CLARIFY→PLAN→ACT) | 2 days |
| 09 | `09-sandbox-execution.md` | Docker sandbox for code execution | 5 days |
| 10 | `10-file-upload-conversion.md` | File upload & document conversion | 3 days |

### Phase 5: Integrations (Medium — 5 days)
| # | File | Feature | Effort |
|---|------|---------|--------|
| 11 | `11-slack-integration.md` | Slack bot (Socket Mode) | 3 days |
| 12 | `12-telegram-integration.md` | Telegram bot (long-polling) | 2 days |

### Phase 6: Architecture (Low — 1.5 days)
| # | File | Feature | Effort |
|---|------|---------|--------|
| 13 | `13-harness-app-boundary.md` | Harness/app import boundary | 1 day |
| 14 | `14-memory-upload-filtering.md` | Memory upload filtering | 0.5 day |

**Total: ~36 days**

## How to Run

```bash
# Run each feature with /feature, reading the spec file:
/feature "$(cat features/deerflow/01-loop-detection.md)"
```

Or use the automated runner:
```bash
# Runs all features sequentially, pings between each
python features/deerflow/run_all.py
```
