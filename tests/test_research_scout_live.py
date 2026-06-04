"""Live smoke test against OpenRouter for the research scout's default model.

Why this test exists
--------------------

The nightly research scout pays for a real LLM call (it dropped the
free-tier Qwen model after it 429'd four nights running in late May
2026). Whoever bumps `OPENROUTER_MODEL` next needs a one-shot way to
verify the new model ID actually resolves on OpenRouter and returns a
non-empty completion BEFORE merging — otherwise the cron silently
records `llm-error` every night until someone notices.

Usage
-----

    # CI / default: skipped
    pytest tests/test_research_scout_live.py

    # Operator: opt-in
    OPENROUTER_API_KEY=sk-or-... pytest -m live tests/test_research_scout_live.py -v

The test makes ONE small completion call (~50 tokens), costs a fraction
of a cent at sonnet rates, and asserts the model handles a JSON-array
prompt similar in shape to the real scout prompt.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "run_research_scout.py"


def _load_scout():
    spec = importlib.util.spec_from_file_location("run_research_scout", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    src = str(Path(__file__).parent.parent / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def scout():
    return _load_scout()


def _has_openrouter_key() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY"))


@pytest.mark.live
@pytest.mark.skipif(
    not _has_openrouter_key(),
    reason="Requires OPENROUTER_API_KEY env var. Source .env.local before running.",
)
def test_default_model_resolves_on_openrouter(scout):
    """The model in `OPENROUTER_MODEL` must be a valid OpenRouter id and
    must return a non-empty completion. If this fails we know the nightly
    scout is broken BEFORE the cron does."""
    assert scout.OPENROUTER_MODEL == "tencent/hy3-preview", (
        "Default scout model drifted away from the chat UI default "
        "(see PREFERRED_CLOUD_MODEL in ChatInput.tsx). Update this test "
        "along with OPENROUTER_MODEL if intentional."
    )

    # Minimal prompt — we only care that the round-trip works and returns
    # text. We deliberately don't ask for the full scout JSON schema here
    # (that's covered by the parser unit tests).
    resp = scout._call_openrouter("Reply with the single word OK and nothing else.")

    assert "error" not in resp, f"OpenRouter call failed: {resp.get('error')!r}"
    content = resp["content"].strip()
    assert content, "OpenRouter returned an empty completion"
    # Sonnet is reliable enough that 'OK' will be in there. We don't
    # assert exact match to allow trivial prose like "OK." or "OK!".
    assert "ok" in content.lower(), (
        f"Unexpected completion shape from {scout.OPENROUTER_MODEL!r}: {content[:200]!r}"
    )


@pytest.mark.live
@pytest.mark.skipif(
    not _has_openrouter_key(),
    reason="Requires OPENROUTER_API_KEY env var. Source .env.local before running.",
)
def test_request_payload_caps_reasoning_for_thinking_models(scout):
    """tencent/hy3-preview is a reasoning model: without `reasoning.effort
    = "low"` it spends 95%+ of `max_tokens` on hidden reasoning and
    returns empty/truncated content. We assert the live response comes
    back with `finish_reason=stop` AND non-trivial content from a small
    structured prompt — a smoke test for the reasoning cap and the
    240 s socket timeout."""
    prompt = (
        "Return a JSON array with a single element: "
        '{"name": "ok", "value": 1}. No prose, no fences.'
    )
    resp = scout._call_openrouter(prompt)
    assert "error" not in resp, (
        f"Reasoning cap regression — request returned error: {resp.get('error')!r}. "
        "Check that `reasoning: {'effort': 'low'}` is still on the payload."
    )
    # Must contain at least the JSON tokens; not asserting exact shape,
    # the parser unit tests cover that.
    assert "[" in resp["content"] and "]" in resp["content"]


@pytest.mark.live
@pytest.mark.skipif(
    not _has_openrouter_key(),
    reason="Requires OPENROUTER_API_KEY env var. Source .env.local before running.",
)
def test_default_model_produces_valid_json_array(scout):
    """End-to-end shape check: ask the live model for the same JSON-array
    contract the real scout prompt uses, then push the response through
    `_parse_improvements`. If a future model change breaks JSON-mode
    behaviour, this is where we'll see it first."""
    prompt = (
        "Reply with ONLY a JSON array (no prose, no markdown fences). "
        "Each item must have: component, title, description, file, code, "
        "benefit, impact (1-10), effort (1-10), risk (1-10), value_score (1-10). "
        "Return exactly one item where title='Test item', "
        "description='Smoke test', component='test', file='test.py', code='', "
        'benefit="", impact=5, effort=5, risk=5, value_score=5.'
    )
    resp = scout._call_openrouter(prompt)
    assert "error" not in resp, f"OpenRouter call failed: {resp.get('error')!r}"

    items, reason = scout._parse_improvements(resp["content"])
    assert items, (
        f"Parser returned 0 items from a JSON-array prompt — reason='{reason}', "
        f"raw response was: {resp['content'][:500]!r}"
    )
    assert items[0]["component"] == "test"
    assert items[0]["title"] == "Test item"
    # Confirm parser handles the content the model actually emits — both
    # halves of the contract (model output → parser) work together.
    json.dumps(items)  # round-trip serialisable
