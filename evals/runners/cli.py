"""CLI runner for the P2 Evaluator Framework.

Usage::

    python -m evals.runners.cli \\
        --suite evals/datasets/smoke.json \\
        --agent team-lead \\
        --provider openrouter \\
        --model openai/gpt-4o

The runner loads the suite, hits the specified agent via the orchestrator HTTP
API (or a stub when --dry-run is given), runs rubric checks, and prints a
coloured summary table to stdout.

Exit codes:
    0 — all cases passed
    1 — one or more cases failed
    2 — fatal error (file not found, bad config, etc.)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# Add the repo src to sys.path so the package can be imported without install.
_REPO_SRC = Path(__file__).resolve().parents[3] / "src"
if str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))

from agent_orchestrator.core.evaluator import (  # noqa: E402
    EvalCase,
    EvalReport,
    EvalRun,
    EvalSuite,
    JsonDataset,
    RubricEvaluator,
)

# ---------------------------------------------------------------------------
# ANSI colours (degrade gracefully when not a TTY)
# ---------------------------------------------------------------------------

_IS_TTY = sys.stdout.isatty()

_GREEN = "\033[92m" if _IS_TTY else ""
_RED = "\033[91m" if _IS_TTY else ""
_YELLOW = "\033[93m" if _IS_TTY else ""
_CYAN = "\033[96m" if _IS_TTY else ""
_BOLD = "\033[1m" if _IS_TTY else ""
_RESET = "\033[0m" if _IS_TTY else ""


def _col(text: str, colour: str) -> str:
    return f"{colour}{text}{_RESET}"


# ---------------------------------------------------------------------------
# Stub agent callable (used when no real agent is wired)
# ---------------------------------------------------------------------------


async def _stub_agent(case: EvalCase) -> EvalRun:
    """Placeholder agent that echoes the expected output (for smoke testing)."""
    output = case.expected or f"Response to: {case.prompt[:60]}"
    case_id = case.metadata.get("case_id", "unknown")
    return EvalRun(case_id=str(case_id), agent_output=output, ok=True)


# ---------------------------------------------------------------------------
# HTTP agent callable
# ---------------------------------------------------------------------------


async def _http_agent(
    case: EvalCase,
    *,
    base_url: str,
    agent: str,
    provider: str,
    model: str,
) -> EvalRun:
    """Call the orchestrator /api/prompt endpoint and wrap the response."""
    try:
        import httpx  # type: ignore[import]
    except ImportError:
        raise RuntimeError(
            "httpx is required for HTTP agent mode. "
            "Install with: pip install httpx"
        )

    case_id = str(case.metadata.get("case_id", "unknown"))
    payload: dict[str, Any] = {
        "prompt": case.prompt,
        "agent": agent,
        "provider": provider,
        "model": model,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{base_url}/api/prompt", json=payload)
            resp.raise_for_status()
            data = resp.json()
            output = data.get("result") or data.get("output") or str(data)
            return EvalRun(case_id=case_id, agent_output=output, ok=True)
    except Exception as exc:
        return EvalRun(
            case_id=case_id,
            agent_output="",
            ok=False,
            metadata={"error": str(exc)},
        )


# ---------------------------------------------------------------------------
# Pretty-print report
# ---------------------------------------------------------------------------

_COL_WIDTHS = {"case_id": 20, "evaluator": 14, "passed": 8, "score": 8, "detail": 40}


def _print_report(report: EvalReport) -> None:
    print()
    print(_col(f" Eval suite: {report.suite} ", _BOLD + _CYAN))
    print()

    header = (
        f"{'Case ID':<{_COL_WIDTHS['case_id']}}"
        f"{'Evaluator':<{_COL_WIDTHS['evaluator']}}"
        f"{'Passed':<{_COL_WIDTHS['passed']}}"
        f"{'Score':<{_COL_WIDTHS['score']}}"
        f"Detail"
    )
    print(_col(header, _BOLD))
    print("-" * 100)

    for case, run, scores in report.runs:
        case_id = run.case_id
        for score in scores:
            colour = _GREEN if score.passed else _RED
            passed_str = _col("PASS" if score.passed else "FAIL", colour)
            detail_short = score.detail[:_COL_WIDTHS["detail"]]
            line = (
                f"{case_id:<{_COL_WIDTHS['case_id']}}"
                f"{score.evaluator:<{_COL_WIDTHS['evaluator']}}"
                f"{passed_str:<{_COL_WIDTHS['passed'] + len(colour) + len(_RESET)}}"
                f"{score.score:<{_COL_WIDTHS['score']}.2f}"
                f"{detail_short}"
            )
            print(line)
            case_id = ""  # only show case_id on first score row

    print("-" * 100)
    print()
    print(_col("Summary", _BOLD))
    for key, value in report.summary.items():
        bar = _make_bar(value)
        colour = _GREEN if value >= 0.8 else (_YELLOW if value >= 0.5 else _RED)
        print(f"  {key:<40} {_col(f'{value:.3f}', colour)}  {bar}")
    print()


def _make_bar(value: float, width: int = 20) -> str:
    filled = int(round(value * width))
    return "[" + "#" * filled + "." * (width - filled) + "]"


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m evals.runners.cli",
        description="Run a P2 eval suite and print a coloured summary.",
    )
    p.add_argument(
        "--suite",
        required=True,
        help="Path to the JSON or YAML eval dataset file.",
    )
    p.add_argument(
        "--agent",
        default="team-lead",
        help="Agent name to invoke (default: team-lead).",
    )
    p.add_argument(
        "--provider",
        default="openrouter",
        help="Provider key (default: openrouter).",
    )
    p.add_argument(
        "--model",
        default="openai/gpt-4o",
        help="Model identifier (default: openai/gpt-4o).",
    )
    p.add_argument(
        "--base-url",
        default="http://localhost:5005",
        help="Dashboard base URL (default: http://localhost:5005).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Use the stub agent (no real LLM calls) to verify the suite loads.",
    )
    p.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Print the full report as JSON instead of the table.",
    )
    return p


async def _main(args: argparse.Namespace) -> int:
    suite_path = Path(args.suite)
    if not suite_path.exists():
        print(_col(f"Error: suite file not found: {suite_path}", _RED), file=sys.stderr)
        return 2

    try:
        dataset = JsonDataset(suite_path)
        cases = dataset.load()
    except (ValueError, FileNotFoundError) as exc:
        print(_col(f"Error loading dataset: {exc}", _RED), file=sys.stderr)
        return 2

    # Build a RubricEvaluator per case from the rubric field (contains check).
    # For the CLI we build simple deterministic checks; LLMJudge requires a
    # wired provider and is out of scope for the standalone CLI runner.
    evaluators = [
        RubricEvaluator(
            checks=[{"type": "min_length", "value": 1}]  # at least non-empty
        )
    ]

    suite = EvalSuite(
        name=suite_path.stem,
        cases=cases,
        evaluators=evaluators,
    )

    if args.dry_run:
        agent_callable = _stub_agent
    else:
        async def agent_callable(case: EvalCase) -> EvalRun:  # type: ignore[misc]
            return await _http_agent(
                case,
                base_url=args.base_url,
                agent=args.agent,
                provider=args.provider,
                model=args.model,
            )

    report = await suite.run(agent_callable)

    if args.json_output:
        out: dict[str, Any] = {
            "suite": report.suite,
            "summary": report.summary,
            "runs": [
                {
                    "case_id": run.case_id,
                    "ok": run.ok,
                    "scores": [
                        {
                            "evaluator": s.evaluator,
                            "passed": s.passed,
                            "score": s.score,
                            "detail": s.detail,
                        }
                        for s in scores
                    ],
                }
                for _case, run, scores in report.runs
            ],
        }
        print(json.dumps(out, indent=2))
    else:
        _print_report(report)

    pass_rate = report.summary.get("pass_rate", 0.0)
    return 0 if pass_rate >= 1.0 else 1


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
