"""P2 Evaluator Framework — LLM-judge + rubric-based quality measurement.

SOLID design rationale:
  S — Single responsibility: each class has one job (case, run, score, report, suite).
  O — Open/closed: add new Evaluator subtypes without modifying existing ones.
  L — Liskov substitution: any Evaluator subtype works wherever Evaluator is accepted.
  I — Interface segregation: Evaluator declares only the evaluate() method; loaders,
      runners, and reporters are separate concerns in separate classes.
  D — Dependency inversion: LLMJudge accepts the abstract Provider (core.provider),
      never a concrete vendor class.
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml

from .provider import Message, Provider, Role

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvalCase:
    """A single evaluation scenario: what to ask, what to expect, and how to grade."""

    prompt: str
    expected: str | None = None
    rubric: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvalRun:
    """The agent's response to one EvalCase."""

    case_id: str
    agent_output: str
    ok: bool
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvalScore:
    """The result of a single evaluator judging one EvalRun."""

    passed: bool
    score: float = 0.0
    detail: str = ""
    evaluator: str = ""


@dataclass(frozen=True)
class EvalReport:
    """Aggregated results for an entire EvalSuite run."""

    suite: str
    runs: list[tuple[EvalCase, EvalRun, list[EvalScore]]]
    summary: dict[str, float]


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class Evaluator(ABC):
    """Abstract evaluator — one pluggable grading strategy."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable identifier for this evaluator."""
        ...

    @abstractmethod
    async def evaluate(self, case: EvalCase, run: EvalRun) -> EvalScore:
        """Score the agent's run against the case specification.

        Args:
            case: The eval scenario (prompt, expected output, rubric).
            run: The agent's actual response and metadata.

        Returns:
            An EvalScore with passed, numeric score, and a detail explanation.
        """
        ...


# ---------------------------------------------------------------------------
# RubricEvaluator — deterministic checks
# ---------------------------------------------------------------------------

_SUPPORTED_CHECK_TYPES = frozenset(
    {"contains", "not_contains", "regex", "json_schema", "max_length", "min_length"}
)


def _validate_check(check: dict[str, Any]) -> None:
    """Raise ValueError for malformed check dicts."""
    if "type" not in check:
        raise ValueError(f"Check dict missing 'type' key: {check!r}")
    if check["type"] not in _SUPPORTED_CHECK_TYPES:
        raise ValueError(
            f"Unknown check type {check['type']!r}. "
            f"Supported: {sorted(_SUPPORTED_CHECK_TYPES)}"
        )
    if check["type"] in {"contains", "not_contains", "regex"} and "value" not in check:
        raise ValueError(f"Check type {check['type']!r} requires a 'value' key: {check!r}")
    if check["type"] in {"max_length", "min_length"} and "value" not in check:
        raise ValueError(f"Check type {check['type']!r} requires a 'value' key (int): {check!r}")


class RubricEvaluator(Evaluator):
    """Deterministic rubric evaluator based on a list of check dicts.

    Each check has a ``type``, an optional ``value``, and an optional ``weight``
    (default 1.0). The final score is the weighted fraction of checks that pass.

    Supported check types:
    - ``contains``    — output must contain the given string (case-insensitive).
    - ``not_contains``— output must NOT contain the given string.
    - ``regex``       — output must match the given regex pattern.
    - ``json_schema`` — output must be valid JSON that satisfies a JSON Schema dict.
    - ``max_length``  — output length must be <= value (characters).
    - ``min_length``  — output length must be >= value (characters).

    Example::

        ev = RubricEvaluator(checks=[
            {"type": "contains", "value": "hello", "weight": 2},
            {"type": "max_length", "value": 500},
        ])
    """

    def __init__(self, checks: list[dict[str, Any]]) -> None:
        for check in checks:
            _validate_check(check)
        self._checks = checks

    @property
    def name(self) -> str:
        return "rubric"

    async def evaluate(self, case: EvalCase, run: EvalRun) -> EvalScore:  # noqa: ARG002
        output = run.agent_output or ""
        total_weight = 0.0
        passed_weight = 0.0
        details: list[str] = []

        for check in self._checks:
            weight = float(check.get("weight", 1.0))
            total_weight += weight
            ok, reason = self._run_check(check, output)
            if ok:
                passed_weight += weight
                details.append(f"[PASS] {reason}")
            else:
                details.append(f"[FAIL] {reason}")

        if total_weight == 0.0:
            score = 1.0
            passed = True
        else:
            score = passed_weight / total_weight
            passed = score >= 1.0

        return EvalScore(
            passed=passed,
            score=score,
            detail="; ".join(details),
            evaluator=self.name,
        )

    # ------------------------------------------------------------------
    def _run_check(self, check: dict[str, Any], output: str) -> tuple[bool, str]:
        kind = check["type"]
        value = check.get("value")

        if kind == "contains":
            ok = str(value).lower() in output.lower()
            return ok, f"contains({value!r})"

        if kind == "not_contains":
            ok = str(value).lower() not in output.lower()
            return ok, f"not_contains({value!r})"

        if kind == "regex":
            try:
                ok = bool(re.search(str(value), output))
            except re.error as exc:
                ok = False
                return ok, f"regex({value!r}) error: {exc}"
            return ok, f"regex({value!r})"

        if kind == "json_schema":
            try:
                parsed = json.loads(output)
                ok = self._validate_json_schema(parsed, value)
                return ok, "json_schema"
            except (json.JSONDecodeError, Exception) as exc:
                return False, f"json_schema error: {exc}"

        if kind == "max_length":
            limit = int(value)
            ok = len(output) <= limit
            return ok, f"max_length({limit}): got {len(output)}"

        if kind == "min_length":
            limit = int(value)
            ok = len(output) >= limit
            return ok, f"min_length({limit}): got {len(output)}"

        return False, f"unknown check type {kind!r}"

    @staticmethod
    def _validate_json_schema(data: Any, schema: Any) -> bool:
        """Minimal structural JSON Schema validation (no external deps).

        Supports: type, required, properties (type checks), minLength,
        maxLength, minimum, maximum.
        """
        if not isinstance(schema, dict):
            return True  # no schema constraints

        schema_type = schema.get("type")
        if schema_type:
            type_map = {
                "object": dict,
                "array": list,
                "string": str,
                "number": (int, float),
                "integer": int,
                "boolean": bool,
                "null": type(None),
            }
            expected = type_map.get(schema_type)
            if expected and not isinstance(data, expected):
                return False

        required = schema.get("required", [])
        if required and isinstance(data, dict):
            for key in required:
                if key not in data:
                    return False

        properties = schema.get("properties", {})
        if properties and isinstance(data, dict):
            for prop, prop_schema in properties.items():
                if prop in data:
                    if not RubricEvaluator._validate_json_schema(data[prop], prop_schema):
                        return False

        if "minLength" in schema and isinstance(data, str):
            if len(data) < schema["minLength"]:
                return False

        if "maxLength" in schema and isinstance(data, str):
            if len(data) > schema["maxLength"]:
                return False

        if "minimum" in schema and isinstance(data, (int, float)):
            if data < schema["minimum"]:
                return False

        if "maximum" in schema and isinstance(data, (int, float)):
            if data > schema["maximum"]:
                return False

        return True


# ---------------------------------------------------------------------------
# LLMJudge — LLM-based evaluator
# ---------------------------------------------------------------------------

_JUDGE_SYSTEM = """\
You are an evaluation judge for AI agent outputs. Given a prompt, optional expected answer,
a rubric, and the agent's actual output, assess the quality.

Respond with ONLY a valid JSON object in this exact format (no markdown, no prose):
{"passed": <bool>, "score": <float 0.0-1.0>, "detail": "<explanation under 200 chars>"}
"""

_JUDGE_TEMPLATE = """\
=== PROMPT ===
{prompt}

=== EXPECTED ===
{expected}

=== RUBRIC ===
{rubric}

=== AGENT OUTPUT ===
{agent_output}

Judge the output and respond with JSON only.
"""

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_JSON_BRACE_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any]:
    """Robustly extract a JSON dict from LLM output.

    Strategy (in order):
    1. Try direct parse.
    2. Strip code fences (```json ... ```) and re-parse.
    3. Extract first {...} block with regex and re-parse.
    4. Return a default failure payload.
    """
    # 1 — direct parse
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # 2 — strip code fences
    fence_match = _JSON_FENCE_RE.search(stripped)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 3 — extract first brace group
    brace_match = _JSON_BRACE_RE.search(stripped)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    # 4 — give up gracefully
    logger.warning("LLMJudge: could not extract JSON from response: %r", text[:200])
    return {"passed": False, "score": 0.0, "detail": "malformed judge response"}


class LLMJudge(Evaluator):
    """LLM-based evaluator — asks a strong model to score agent output.

    The judge receives the prompt, expected output, rubric, and actual agent
    output, then returns a structured JSON verdict.  Malformed responses are
    recovered gracefully: a default failed EvalScore is returned so the suite
    run never crashes due to a judge error.

    Args:
        provider: An abstract Provider (dependency inversion — no concrete import).
        max_tokens: Token budget for the judge response.
    """

    def __init__(self, provider: Provider, max_tokens: int = 512) -> None:
        self._provider = provider
        self._max_tokens = max_tokens

    @property
    def name(self) -> str:
        return f"llm_judge({self._provider.model_id})"

    async def evaluate(self, case: EvalCase, run: EvalRun) -> EvalScore:
        user_text = _JUDGE_TEMPLATE.format(
            prompt=case.prompt,
            expected=case.expected or "(none)",
            rubric=case.rubric or "(none)",
            agent_output=run.agent_output,
        )
        messages = [Message(role=Role.USER, content=user_text)]
        try:
            completion = await self._provider.complete(
                messages,
                system=_JUDGE_SYSTEM,
                max_tokens=self._max_tokens,
                temperature=0.0,
            )
            raw = completion.content
        except Exception as exc:
            logger.warning("LLMJudge: provider error: %s", exc)
            return EvalScore(
                passed=False,
                score=0.0,
                detail=f"judge provider error: {exc}",
                evaluator=self.name,
            )

        payload = _extract_json(raw)

        passed = bool(payload.get("passed", False))
        try:
            score = float(payload.get("score", 0.0))
            score = max(0.0, min(1.0, score))
        except (TypeError, ValueError):
            score = 0.0
        detail = str(payload.get("detail", ""))[:400]

        return EvalScore(passed=passed, score=score, detail=detail, evaluator=self.name)


# ---------------------------------------------------------------------------
# EvalSuite — orchestrates cases and evaluators
# ---------------------------------------------------------------------------


class EvalSuite:
    """Run a set of EvalCases through one or more Evaluators using an agent callable.

    Args:
        name: Human-readable name for this suite.
        cases: List of EvalCase objects to evaluate.
        evaluators: List of Evaluator instances to apply to every run.
    """

    def __init__(
        self,
        name: str,
        cases: list[EvalCase],
        evaluators: list[Evaluator],
    ) -> None:
        self.name = name
        self.cases = cases
        self.evaluators = evaluators

    async def run(
        self,
        agent_callable: Callable[[EvalCase], Awaitable[EvalRun]],
    ) -> EvalReport:
        """Execute all cases, gather evaluator scores, and compute summary metrics.

        Args:
            agent_callable: An async function (EvalCase) -> EvalRun.

        Returns:
            An EvalReport with per-case results and aggregate summary statistics.
        """
        all_runs: list[tuple[EvalCase, EvalRun, list[EvalScore]]] = []

        for case in self.cases:
            run = await agent_callable(case)
            scores: list[EvalScore] = []
            for evaluator in self.evaluators:
                try:
                    score = await evaluator.evaluate(case, run)
                except Exception as exc:
                    logger.warning(
                        "Evaluator %r raised during case %r: %s",
                        evaluator.name,
                        run.case_id,
                        exc,
                    )
                    score = EvalScore(
                        passed=False,
                        score=0.0,
                        detail=f"evaluator error: {exc}",
                        evaluator=evaluator.name,
                    )
                scores.append(score)
            all_runs.append((case, run, scores))

        summary = self._compute_summary(all_runs)
        return EvalReport(suite=self.name, runs=all_runs, summary=summary)

    def _compute_summary(
        self,
        runs: list[tuple[EvalCase, EvalRun, list[EvalScore]]],
    ) -> dict[str, float]:
        if not runs:
            return {}

        all_scores = [s for _, _, scores in runs for s in scores]
        total = len(all_scores)
        passed = sum(1 for s in all_scores if s.passed)

        per_evaluator_pass: dict[str, list[bool]] = {}
        per_evaluator_score: dict[str, list[float]] = {}
        for _, _, scores in runs:
            for s in scores:
                per_evaluator_pass.setdefault(s.evaluator, []).append(s.passed)
                per_evaluator_score.setdefault(s.evaluator, []).append(s.score)

        summary: dict[str, float] = {
            "pass_rate": passed / total if total else 0.0,
            "mean_score": sum(s.score for s in all_scores) / total if total else 0.0,
        }
        for ev_name, bools in per_evaluator_pass.items():
            safe = ev_name.replace(".", "_").replace("(", "_").replace(")", "")
            summary[f"pass_rate_{safe}"] = sum(bools) / len(bools) if bools else 0.0
        for ev_name, sc_list in per_evaluator_score.items():
            safe = ev_name.replace(".", "_").replace("(", "_").replace(")", "")
            summary[f"mean_score_{safe}"] = sum(sc_list) / len(sc_list) if sc_list else 0.0

        return summary


# ---------------------------------------------------------------------------
# JsonDataset — load cases from JSON or YAML
# ---------------------------------------------------------------------------


class JsonDataset:
    """Load EvalCases from a JSON or YAML file.

    Expected format::

        {
            "cases": [
                {
                    "prompt": "...",           // required
                    "expected": "...",          // optional
                    "rubric": "...",            // optional
                    "metadata": {}              // optional
                }
            ]
        }

    ``metadata`` may include a ``"case_id"`` key; otherwise the index is used.

    Args:
        path: Path to a ``.json`` or ``.yaml``/``.yml`` file.

    Raises:
        ValueError: If the file is missing required fields or cannot be parsed.
        FileNotFoundError: If the path does not exist.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> list[EvalCase]:
        """Parse the file and return a list of EvalCase objects."""
        raw = self._read()
        if not isinstance(raw, dict):
            raise ValueError(f"Dataset root must be a JSON/YAML object, got {type(raw).__name__}")
        if "cases" not in raw:
            raise ValueError("Dataset file must have a top-level 'cases' key")
        cases_raw = raw["cases"]
        if not isinstance(cases_raw, list):
            raise ValueError(f"'cases' must be a list, got {type(cases_raw).__name__}")

        cases: list[EvalCase] = []
        for idx, item in enumerate(cases_raw):
            if not isinstance(item, dict):
                raise ValueError(f"Case at index {idx} must be a dict, got {type(item).__name__}")
            if "prompt" not in item:
                raise ValueError(f"Case at index {idx} is missing required field 'prompt'")
            meta = dict(item.get("metadata") or {})
            if "case_id" not in meta:
                meta["case_id"] = str(idx)
            cases.append(
                EvalCase(
                    prompt=str(item["prompt"]),
                    expected=item.get("expected"),
                    rubric=item.get("rubric"),
                    metadata=meta,
                )
            )
        return cases

    def _read(self) -> Any:
        if not self._path.exists():
            raise FileNotFoundError(f"Dataset file not found: {self._path}")
        text = self._path.read_text(encoding="utf-8")
        suffix = self._path.suffix.lower()
        if suffix in {".yaml", ".yml"}:
            try:
                return yaml.safe_load(text)
            except yaml.YAMLError as exc:
                raise ValueError(f"YAML parse error in {self._path}: {exc}") from exc
        # Default: JSON
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON parse error in {self._path}: {exc}") from exc
