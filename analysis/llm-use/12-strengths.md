# 12 - Strengths

## Overview
What llm-use does well and why.

## 1. Zero-Config Local-First Design
- Only `requests` is required -- everything else is optional
- Works fully offline with Ollama
- No database server, no Docker, no cloud account needed
- `pip install -e .` and you're running

**Why it matters**: Reduces friction to zero for getting started. A developer can try it in under a minute.

## 2. Cost-Conscious Architecture
- Every call tracks tokens and cost
- Session history with full cost breakdowns
- Router system actively avoids expensive orchestrator calls for simple tasks
- Free Ollama usage for cost-sensitive workloads

**Why it matters**: LLM costs add up fast. Making cost visible at every level changes user behavior.

## 3. Smart Router with Learning
- 3-tier routing (LLM -> learned -> heuristic) with graceful degradation
- Self-improving: stores past routing decisions
- Configurable patterns without code changes
- Export/import for sharing routing knowledge

**Why it matters**: The learned router is genuinely novel. Most orchestrators use static rules.

## 4. Practical Scraping Integration
- Workers can fetch real web content mid-execution
- Two backends (static + dynamic JS rendering)
- Cached to avoid redundant fetches
- Lightweight RAG without vector stores

**Why it matters**: Many real tasks benefit from web grounding. Integrating it at the worker level is pragmatic.

## 5. Clean Data Models
- `@dataclass` for all models (Call, Session, ModelConfig)
- Serializable to JSON with `asdict()`
- Clear fields with sensible defaults

**Why it matters**: Simple, readable, and easy to extend.

## 6. Robust JSON Parsing
- Handles code-fenced JSON (` ```json ... ``` `)
- Handles embedded JSON in natural language text
- Brace-balanced extraction as fallback
- String-aware parsing (handles escaped quotes)

**Why it matters**: LLMs don't always return clean JSON. The multi-strategy parser handles real-world LLM output.

## 7. Event Callback System
- Simple `event_cb(name, payload)` callback
- Used by TUI for real-time status updates
- Non-intrusive: no-op if no callback registered

**Why it matters**: Simple but effective for real-time UI integration.

## Key Patterns
- Simplicity as a feature, not a limitation
- Graceful degradation at every level
- Local-first with optional cloud enhancement

## Relevance to Our Project
The learned router and JSON parsing robustness are the most adoptable patterns. Our JSON parsing in LLM responses could benefit from their brace-balanced extraction approach.
