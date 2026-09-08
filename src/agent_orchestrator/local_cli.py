"""Local subprocess entrypoint for `ago run --local`.

Receives a JSON request on stdin, instantiates the embedded
:class:`OrchestratorClient` against a single ad-hoc provider configured
from environment variables (no server, no Docker, no HTTP), runs the
agent once, and writes a single JSON response object to stdout.

This module is intentionally tiny — it is the smallest viable bridge
between the Rust CLI and the Python harness. It deliberately:

* lives in the harness layer (no ``dashboard`` imports) so installing
  ``agent-orchestrator`` without the ``[dashboard]`` extras is enough;
* does NOT support streaming — one-shot blocking only. Streaming would
  need a length-prefixed JSON-RPC framing on stdout that ``ago chat``
  could reuse, which is the v0.6.0 design effort.
* picks up provider credentials from the same env vars the dashboard
  uses (``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY`` …) so existing
  configurations work without a second copy.

Invocation contract (CLI ↔ subprocess):

    Input  (stdin, single JSON object):
        {
          "agent": "backend",
          "task":  "explain this codebase",
          "model": "claude-sonnet-4-6",
          "provider": "anthropic",
          "max_steps": 10,
          "role": "You are a senior backend engineer..."   (optional)
        }

    Output (stdout, single JSON object on success):
        {
          "success": true,
          "output":  "...",
          "elapsed_s": 12.3,
          "total_input_tokens": 1234,
          "total_output_tokens": 567,
          "total_cost_usd": 0.012
        }

    Output (stdout, single JSON object on failure):
        {"success": false, "error": "..."}

The process always exits 0 — failures are signalled in the JSON body,
not the exit code, so the Rust caller has uniform error handling.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback

from .client import OrchestratorClient
from .core.agent import AgentConfig


def _build_provider(provider_key: str, model: str):
    """Instantiate the provider class matching ``provider_key``.

    Each provider reads its own credential from a well-known env var. A
    missing key raises :class:`RuntimeError` with a hint of which var to
    set — that surfaces as ``{"success": false, "error": ...}`` to the
    caller, which is the right place for it (the CLI prints it on stderr
    and exits non-zero).
    """
    key = provider_key.lower()
    if key == "anthropic":
        from .providers.anthropic import AnthropicProvider

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set — `ago run --local --provider anthropic` "
                "needs the same env var the dashboard uses."
            )
        return AnthropicProvider(model=model, api_key=api_key)
    if key == "openai":
        from .providers.openai import OpenAIProvider

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        return OpenAIProvider(model=model, api_key=api_key)
    if key == "openrouter":
        from .providers.openrouter import OpenRouterProvider

        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        return OpenRouterProvider(model=model, api_key=api_key)
    if key == "google":
        from .providers.google import GoogleProvider

        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY (or GOOGLE_API_KEY) is not set")
        return GoogleProvider(model=model, api_key=api_key)
    if key in {"ollama", "local"}:
        from .providers.local import LocalProvider

        return LocalProvider(model=model)
    raise RuntimeError(
        f"unknown provider: {provider_key!r} — supported: "
        "anthropic, openai, openrouter, google, ollama"
    )


async def _run(req: dict) -> dict:
    import time

    from .core.agent import TaskStatus

    agent_name = req.get("agent") or ""
    task_desc = req.get("task") or ""
    model = req.get("model") or ""
    provider_key = req.get("provider") or "ollama"
    max_steps = int(req.get("max_steps") or 10)
    role = req.get("role") or f"You are {agent_name}."

    if not agent_name:
        raise ValueError("missing 'agent' in request")
    if not task_desc:
        raise ValueError("missing 'task' in request")
    if not model:
        raise ValueError("missing 'model' in request")

    provider = _build_provider(provider_key, model)
    agent_cfg = AgentConfig(
        name=agent_name,
        role=role,
        provider_key=provider_key,
        max_steps=max_steps,
    )
    client = OrchestratorClient(
        providers={provider_key: provider},
        agents={agent_name: agent_cfg},
    )
    start = time.monotonic()
    result = await client.run_agent(
        agent=agent_name, task=task_desc, model=model, max_steps=max_steps
    )
    elapsed = time.monotonic() - start
    success = result.status == TaskStatus.COMPLETED
    return {
        "success": success,
        "output": result.output if success else None,
        "error": result.error if not success else None,
        "elapsed_s": elapsed,
        # TaskResult does not split input/output tokens — the Rust side
        # treats `total_input_tokens` as the combined figure with zero
        # output tokens for now. A v0.5.x follow-up could surface the
        # split when the Provider's last-call usage is exposed.
        "total_input_tokens": getattr(result, "total_tokens", 0) or 0,
        "total_output_tokens": 0,
        "total_cost_usd": result.total_cost_usd or 0.0,
        "steps_taken": result.steps_taken,
        "status": result.status.value,
    }


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        json.dump({"success": False, "error": "empty stdin"}, sys.stdout)
        return 0
    try:
        req = json.loads(raw)
    except json.JSONDecodeError as exc:
        json.dump({"success": False, "error": f"invalid JSON on stdin: {exc}"}, sys.stdout)
        return 0
    try:
        out = asyncio.run(_run(req))
        json.dump(out, sys.stdout)
    except Exception as exc:  # noqa: BLE001 — surface every error to the caller as JSON
        json.dump(
            {
                "success": False,
                "error": f"{type(exc).__name__}: {exc}",
                "trace": traceback.format_exc(),
            },
            sys.stdout,
        )
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
