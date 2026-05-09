"""HTTP API for the P2 Evaluator Framework.

Endpoints:
- ``POST /api/evals/run``           — trigger an async eval run; returns {run_id}.
- ``GET  /api/evals/runs``          — list summaries of recent runs (last 50).
- ``GET  /api/evals/runs/{run_id}`` — full report for a single run.
- ``GET  /api/evals/compare``       — side-by-side delta between two runs.

Runs are executed as FastAPI BackgroundTask instances. Reports are stored in-memory
on ``app.state.eval_reports`` (a dict keyed by run_id, capped at 50 entries).
No LLM calls happen in tests — the agent_callable is resolved at request time and
can be substituted via ``app.state.eval_agent_factory``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from ..core.evaluator import (
    EvalCase,
    EvalReport,
    EvalRun,
    EvalSuite,
    JsonDataset,
    RubricEvaluator,
)

logger = logging.getLogger(__name__)

evals_router = APIRouter(prefix="/api/evals")

# In-memory cap for stored reports.
_MAX_REPORTS = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_store(app_state: Any) -> OrderedDict:
    """Return (and lazily create) the eval_reports OrderedDict on app.state."""
    if not hasattr(app_state, "eval_reports"):
        app_state.eval_reports = OrderedDict()
    return app_state.eval_reports  # type: ignore[return-value]


def _report_to_dict(run_id: str, report: EvalReport) -> dict[str, Any]:
    """Serialise an EvalReport to a JSON-safe dict."""
    return {
        "run_id": run_id,
        "suite": report.suite,
        "summary": report.summary,
        "runs": [
            {
                "case_id": run.case_id,
                "ok": run.ok,
                "agent_output": run.agent_output,
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


def _summary_dict(run_id: str, report: EvalReport) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "suite": report.suite,
        "summary": report.summary,
    }


async def _default_agent(case: EvalCase) -> EvalRun:
    """Fallback no-op agent used when no factory is configured."""
    await asyncio.sleep(0)  # yield to event loop
    case_id = str(case.metadata.get("case_id", "unknown"))
    return EvalRun(
        case_id=case_id,
        agent_output=case.expected or "",
        ok=True,
    )


async def _execute_run(
    run_id: str,
    suite_path: str,
    agent_name: str,
    model: str,
    provider: str,
    app_state: Any,
) -> None:
    """Background task that loads the suite, runs it, and stores the report."""
    store = _ensure_store(app_state)

    try:
        dataset = JsonDataset(suite_path)
        cases = dataset.load()
    except (ValueError, FileNotFoundError) as exc:
        logger.error("eval run %s: failed to load dataset %r: %s", run_id, suite_path, exc)
        # Store a placeholder report with error info.
        error_report = EvalReport(
            suite=Path(suite_path).stem,
            runs=[],
            summary={"pass_rate": 0.0, "mean_score": 0.0, "error": 1.0},
        )
        store[run_id] = error_report
        return

    evaluators = [RubricEvaluator(checks=[{"type": "min_length", "value": 0}])]
    suite = EvalSuite(
        name=Path(suite_path).stem,
        cases=cases,
        evaluators=evaluators,
    )

    # Resolve the agent callable: prefer factory on app.state, fall back to no-op.
    factory = getattr(app_state, "eval_agent_factory", None)
    if factory is not None:
        agent_callable = factory(agent_name=agent_name, model=model, provider=provider)
    else:
        agent_callable = _default_agent

    try:
        report = await suite.run(agent_callable)
    except Exception as exc:
        logger.error("eval run %s: suite.run() raised: %s", run_id, exc, exc_info=True)
        report = EvalReport(
            suite=suite.name,
            runs=[],
            summary={"pass_rate": 0.0, "mean_score": 0.0},
        )

    # Cap the store at _MAX_REPORTS (evict oldest first).
    while len(store) >= _MAX_REPORTS:
        store.popitem(last=False)
    store[run_id] = report
    logger.info("eval run %s complete: %s", run_id, report.summary)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


@evals_router.post("/run")
async def start_run(body: dict, request: Request, background_tasks: BackgroundTasks):
    """Trigger an async eval run.

    Body fields:
    - ``suite_path`` (str, required) — path to the JSON/YAML dataset.
    - ``agent`` (str, default "team-lead") — agent name.
    - ``model`` (str, default "openai/gpt-4o") — model identifier.
    - ``provider`` (str, default "openrouter") — provider key.

    Returns:
        ``{"run_id": "<uuid>"}``
    """
    suite_path = str(body.get("suite_path", "")).strip()
    if not suite_path:
        return JSONResponse(content={"error": "suite_path is required"}, status_code=400)

    agent = str(body.get("agent", "team-lead"))
    model = str(body.get("model", "openai/gpt-4o"))
    provider = str(body.get("provider", "openrouter"))

    run_id = str(uuid.uuid4())
    store = _ensure_store(request.app.state)
    # Reserve the slot immediately so /runs shows a pending entry.
    store[run_id] = None  # type: ignore[assignment]  # will be replaced when done

    background_tasks.add_task(
        _execute_run,
        run_id=run_id,
        suite_path=suite_path,
        agent_name=agent,
        model=model,
        provider=provider,
        app_state=request.app.state,
    )
    return JSONResponse(content={"run_id": run_id}, status_code=202)


@evals_router.get("/runs")
async def list_runs(request: Request):
    """Return summaries of all stored runs (newest first)."""
    store = _ensure_store(request.app.state)
    summaries = []
    for run_id, report in reversed(store.items()):
        if report is None:
            summaries.append({"run_id": run_id, "status": "pending"})
        else:
            summaries.append(_summary_dict(run_id, report))
    return JSONResponse(content={"runs": summaries})


@evals_router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request):
    """Return the full report for a single run."""
    store = _ensure_store(request.app.state)
    if run_id not in store:
        return JSONResponse(content={"error": "run not found"}, status_code=404)
    report = store[run_id]
    if report is None:
        return JSONResponse(content={"run_id": run_id, "status": "pending"})
    return JSONResponse(content=_report_to_dict(run_id, report))


@evals_router.get("/compare")
async def compare_runs(request: Request):
    """Compare two runs side-by-side.

    Query params: ``a=<run_id>&b=<run_id>``

    Returns delta (b - a) for pass_rate and mean_score, plus both summaries.
    """
    a_id = request.query_params.get("a", "")
    b_id = request.query_params.get("b", "")
    if not a_id or not b_id:
        return JSONResponse(
            content={"error": "query params 'a' and 'b' are required"}, status_code=400
        )

    store = _ensure_store(request.app.state)
    missing = [rid for rid in (a_id, b_id) if rid not in store]
    if missing:
        return JSONResponse(
            content={"error": f"run(s) not found: {missing}"}, status_code=404
        )

    report_a = store[a_id]
    report_b = store[b_id]

    if report_a is None or report_b is None:
        return JSONResponse(
            content={"error": "one or both runs are still pending"}, status_code=409
        )

    def _delta(key: str) -> float:
        a_val = float(report_a.summary.get(key, 0.0))
        b_val = float(report_b.summary.get(key, 0.0))
        return round(b_val - a_val, 4)

    return JSONResponse(
        content={
            "a": _summary_dict(a_id, report_a),
            "b": _summary_dict(b_id, report_b),
            "delta": {
                "pass_rate": _delta("pass_rate"),
                "mean_score": _delta("mean_score"),
            },
        }
    )
