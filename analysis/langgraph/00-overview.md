# LangGraph — Overview

## What It Is

LangGraph is a low-level orchestration framework for building stateful, multi-actor AI agents. Built by LangChain Inc, trusted by Klarna, Replit, Elastic. It provides durable execution, human-in-the-loop, and comprehensive memory.

## Repository Structure (Monorepo)

```
libs/
├── langgraph/          # Core framework (graph engine, Pregel runtime, channels)
├── prebuilt/           # High-level APIs (create_react_agent, ToolNode)
├── checkpoint/         # Base checkpoint interfaces + serialization
├── checkpoint-sqlite/  # SQLite checkpointer
├── checkpoint-postgres/# Postgres checkpointer (asyncpg/psycopg)
├── checkpoint-conformance/ # Conformance test suite for checkpointers
├── cli/                # CLI tool (up, build, dev, new)
├── sdk-py/             # Python SDK for LangGraph API Server
└── sdk-js/             # JS/TS SDK
```

## Dependency Map

```
checkpoint
├── checkpoint-postgres
├── checkpoint-sqlite
├── prebuilt
└── langgraph

prebuilt
└── langgraph

sdk-py
├── langgraph
└── cli

sdk-js (standalone)
```

## Key Versions (as of analysis)

| Package | Version | Python |
|---------|---------|--------|
| langgraph | 1.0.10 | >= 3.10 |
| langgraph-prebuilt | 1.0.8 | >= 3.10 |
| langgraph-checkpoint | >= 2.1.0 | >= 3.10 |

## Core Dependencies

- `langchain-core >= 0.1`
- `pydantic >= 2.7.4`
- `xxhash >= 3.5.0` (content hashing)
- `ormsgpack` (Rust-backed msgpack for serialization)

## Ecosystem Integration

- **LangSmith** — Observability, evals, debugging
- **LangSmith Deployment** — Production deployment platform
- **LangChain** — Integrations and composable components
- **LangGraph Studio** — Visual prototyping

## Inspiration

LangGraph is inspired by Google's [Pregel paper](https://research.google/pubs/pub37252/) (BSP model) and [Apache Beam](https://beam.apache.org/). The public API draws from [NetworkX](https://networkx.org/).
