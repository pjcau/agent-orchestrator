---
sidebar_position: 0
title: v1.6 — Rust CLI (ago)
---

# v1.6 — `ago` Rust CLI (Q3 2026, in progress)

Provider-agnostic, single-binary Rust CLI that lets a local project talk to a
remote Agent Orchestrator the same way `gh` talks to GitHub or `vercel` to
Vercel. Lives in [`cli/`](https://github.com/pjcau/agent-orchestrator/tree/main/cli)
at the repo root and ships independently from the Python harness.

**Status**: Phase 1 shipped on branch `experiment/rust-cli` (2026-05-27).
Full design + security model: [docs/cli.md](https://github.com/pjcau/agent-orchestrator/blob/main/docs/cli.md).

## Why Rust

| Concern | Rust answer |
|---|---|
| Distribution | Single static binary per OS — no Python runtime on user machines |
| Cold start | ~50 ms vs ~300 ms for a Python CLI |
| Secrets handling | `secrecy::SecretString` + `zeroize` + OS keychain |
| TLS | `rustls` + vendored `webpki-roots` — no OpenSSL / no system libs |
| Cross-platform | macOS arm64+x64, Linux x64+arm64 (musl), Windows x64 |

## Phase plan

| Phase | Scope | Status | Files |
|---|---|---|---|
| 1 | Skeleton + auth: `login` / `logout` / `whoami` / `config`, OS-keychain token storage, rustls-only HTTP, `/api/cli/v1/whoami` server endpoint, full unit + integration test suite, CI workflow | ✅ Done (v0.1.0) | `cli/`, `src/agent_orchestrator/dashboard/cli_routes.py`, `tests/test_cli_routes.py`, `.github/workflows/cli-rust.yml` |
| 2 | Core execution: device-flow OAuth (RFC 8628), `ago run "<task>"` with SSE token streaming, agent/skill flags, `.ago.yaml` project preset, `--json` output | planned | — |
| 3 | Observability & UX: `ago jobs list/get/cancel`, `ago logs --follow`, indicatif progress bars, shell completions | planned | — |
| 4 | Hardening & release: `cargo audit`/`deny`/`vet` in CI, cross-compile matrix, signed releases via `cosign` + SBOM, Homebrew tap, GitHub Release **v0.1.0** | planned | — |
| 5 | Interactive chat + project context: `ago chat` REPL (v0.2.0), `@file` / `@dir/` references with safe defaults (v0.3.0), `.ago.yaml context:` overrides (v0.3.1), Windows path support (v0.3.2), `cache` subcommand + OpenRouter `cache_control` plumbing for `@file` context (v0.4.0–v0.4.1), recursive `@dir/**` content expansion (v0.4.2) | ✅ Done (v0.4.2) | `cli/src/commands/chat.rs`, `cli/src/context.rs`, `cli/src/project.rs`, `src/agent_orchestrator/core/cache_context.py`, `src/agent_orchestrator/providers/openrouter.py` |

## Security model (Phase 1)

- HTTPS-only by default; `http://` allowed only for `localhost` / `127.0.0.1`.
- Tokens stored in OS keychain (macOS Keychain, Linux Secret Service, Windows
  Credential Manager) via the `keyring` crate. `AGO_TOKEN` env var overrides
  the keychain for CI use.
- Tokens never written to disk in plaintext; never logged
  (`HeaderValue::set_sensitive`).
- Config files written 0600 on Unix.
- Strict TOML parsing (`deny_unknown_fields`).
- Fail-closed login: the token is sent to `/api/cli/v1/whoami` before being
  persisted — a wrong key is never stored.

## Phase 1 acknowledged limits

- Login uses API key paste (validated server-side), not the full device-flow
  promised in the design. Device-flow ships in Phase 2.
- No `--local` fallback (subprocess Python `client.py`) — Phase 2.
- No auto-update — rely on `brew upgrade` / `cargo install --force`.

## Quickstart

```bash
cd cli && cargo install --path . --locked

ago config set server https://orch.example.com
export AGO_API_KEY=ago_pat_xxxxx
ago login --key-env AGO_API_KEY
ago whoami
# alice@example.com (developer) — https://orch.example.com
```
