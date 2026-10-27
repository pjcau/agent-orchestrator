"""Tests for the P2 Evaluator Framework (core/evaluator.py).

Coverage:
- RubricEvaluator: each check type, weights, edge cases.
- LLMJudge with a mocked Provider: happy path, malformed JSON, missing fields.
- EvalSuite end-to-end with a stub agent callable.
- JsonDataset loader: valid file, missing fields, malformed JSON.
- EvalReport summary math.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from agent_orchestrator.core.evaluator import (
    EvalCase,
    EvalReport,
    EvalRun,
    EvalScore,
    EvalSuite,
    JsonDataset,
    LLMJudge,
    RubricEvaluator,
    _extract_json,
)
from agent_orchestrator.core.provider import (
    Completion,
    ModelCapabilities,
    Provider,
    StreamChunk,
    Usage,
)

# ---------------------------------------------------------------------------
# Shared helpers / fixtures
# ---------------------------------------------------------------------------


def _case(prompt: str = "Say hello", expected: str | None = "hello", rubric: str | None = None):
    return EvalCase(prompt=prompt, expected=expected, rubric=rubric)


def _run(output: str, case_id: str = "c1", ok: bool = True):
    return EvalRun(case_id=case_id, agent_output=output, ok=ok)


class _MockProvider(Provider):
    """Provider stub that returns a configurable completion."""

    def __init__(self, response: str = '{"passed": true, "score": 0.9, "detail": "looks good"}'):
        self._response = response

    @property
    def model_id(self) -> str:
        return "mock-judge"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(max_context=4096)

    @property
    def input_cost_per_million(self) -> float:
        return 0.0

    @property
    def output_cost_per_million(self) -> float:
        return 0.0

    async def complete(self, messages, tools=None, system=None, **kwargs) -> Completion:
        return Completion(
            content=self._response,
            usage=Usage(input_tokens=10, output_tokens=20, cost_usd=0.0),
        )

    async def stream(
        self, messages, tools=None, system=None, **kwargs
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content=self._response, is_final=True)


# ---------------------------------------------------------------------------
# RubricEvaluator tests
# ---------------------------------------------------------------------------


class TestRubricEvaluatorContains:
    @pytest.mark.asyncio
    async def test_contains_passes(self):
        ev = RubricEvaluator(checks=[{"type": "contains", "value": "hello"}])
        score = await ev.evaluate(_case(), _run("Hello, world!"))
        assert score.passed is True
        assert score.score == 1.0

    @pytest.mark.asyncio
    async def test_contains_fails(self):
        ev = RubricEvaluator(checks=[{"type": "contains", "value": "goodbye"}])
        score = await ev.evaluate(_case(), _run("Hello, world!"))
        assert score.passed is False
        assert score.score == 0.0

    @pytest.mark.asyncio
    async def test_not_contains_passes(self):
        ev = RubricEvaluator(checks=[{"type": "not_contains", "value": "forbidden"}])
        score = await ev.evaluate(_case(), _run("This is fine"))
        assert score.passed is True

    @pytest.mark.asyncio
    async def test_not_contains_fails(self):
        ev = RubricEvaluator(checks=[{"type": "not_contains", "value": "forbidden"}])
        score = await ev.evaluate(_case(), _run("This contains forbidden word"))
        assert score.passed is False


class TestRubricEvaluatorRegex:
    @pytest.mark.asyncio
    async def test_regex_match(self):
        ev = RubricEvaluator(checks=[{"type": "regex", "value": r"\d{3}-\d{4}"}])
        score = await ev.evaluate(_case(), _run("Call 555-1234 now"))
        assert score.passed is True

    @pytest.mark.asyncio
    async def test_regex_no_match(self):
        ev = RubricEvaluator(checks=[{"type": "regex", "value": r"\d{3}-\d{4}"}])
        score = await ev.evaluate(_case(), _run("No phone number here"))
        assert score.passed is False

    @pytest.mark.asyncio
    async def test_regex_invalid_pattern(self):
        ev = RubricEvaluator(checks=[{"type": "regex", "value": "[invalid("}])
        score = await ev.evaluate(_case(), _run("some output"))
        assert score.passed is False
        assert "error" in score.detail


class TestRubricEvaluatorJsonSchema:
    @pytest.mark.asyncio
    async def test_json_schema_passes(self):
        schema = {"type": "object", "required": ["name", "age"]}
        ev = RubricEvaluator(checks=[{"type": "json_schema", "value": schema}])
        output = json.dumps({"name": "Alice", "age": 30})
        score = await ev.evaluate(_case(), _run(output))
        assert score.passed is True

    @pytest.mark.asyncio
    async def test_json_schema_missing_required(self):
        schema = {"type": "object", "required": ["name", "age"]}
        ev = RubricEvaluator(checks=[{"type": "json_schema", "value": schema}])
        output = json.dumps({"name": "Alice"})
        score = await ev.evaluate(_case(), _run(output))
        assert score.passed is False

    @pytest.mark.asyncio
    async def test_json_schema_invalid_json(self):
        schema = {"type": "object"}
        ev = RubricEvaluator(checks=[{"type": "json_schema", "value": schema}])
        score = await ev.evaluate(_case(), _run("not json at all"))
        assert score.passed is False


class TestRubricEvaluatorLength:
    @pytest.mark.asyncio
    async def test_max_length_passes(self):
        ev = RubricEvaluator(checks=[{"type": "max_length", "value": 100}])
        score = await ev.evaluate(_case(), _run("short"))
        assert score.passed is True

    @pytest.mark.asyncio
    async def test_max_length_fails(self):
        ev = RubricEvaluator(checks=[{"type": "max_length", "value": 5}])
        score = await ev.evaluate(_case(), _run("this is too long"))
        assert score.passed is False

    @pytest.mark.asyncio
    async def test_min_length_passes(self):
        ev = RubricEvaluator(checks=[{"type": "min_length", "value": 3}])
        score = await ev.evaluate(_case(), _run("hello"))
        assert score.passed is True

    @pytest.mark.asyncio
    async def test_min_length_fails(self):
        ev = RubricEvaluator(checks=[{"type": "min_length", "value": 100}])
        score = await ev.evaluate(_case(), _run("hi"))
        assert score.passed is False


class TestRubricEvaluatorWeights:
    @pytest.mark.asyncio
    async def test_weighted_partial_pass(self):
        # contains "hello" (weight 2) passes, max_length 3 fails (weight 1)
        ev = RubricEvaluator(
            checks=[
                {"type": "contains", "value": "hello", "weight": 2},
                {"type": "max_length", "value": 3, "weight": 1},
            ]
        )
        score = await ev.evaluate(_case(), _run("hello world"))
        # 2/(2+1) ≈ 0.666 — not fully passing
        assert score.passed is False
        assert abs(score.score - 2 / 3) < 0.01

    @pytest.mark.asyncio
    async def test_empty_output_min_length_fails(self):
        ev = RubricEvaluator(checks=[{"type": "min_length", "value": 1}])
        score = await ev.evaluate(_case(), _run(""))
        assert score.passed is False

    @pytest.mark.asyncio
    async def test_no_checks_returns_perfect(self):
        ev = RubricEvaluator(checks=[])
        score = await ev.evaluate(_case(), _run("anything"))
        assert score.passed is True
        assert score.score == 1.0


class TestRubricEvaluatorValidation:
    def test_unknown_check_type_raises(self):
        with pytest.raises(ValueError, match="Unknown check type"):
            RubricEvaluator(checks=[{"type": "nonexistent", "value": "x"}])

    def test_missing_type_raises(self):
        with pytest.raises(ValueError, match="missing 'type' key"):
            RubricEvaluator(checks=[{"value": "x"}])

    def test_contains_missing_value_raises(self):
        with pytest.raises(ValueError, match="requires a 'value' key"):
            RubricEvaluator(checks=[{"type": "contains"}])

    def test_evaluator_name(self):
        ev = RubricEvaluator(checks=[])
        assert ev.name == "rubric"


# ---------------------------------------------------------------------------
# LLMJudge tests
# ---------------------------------------------------------------------------


class TestLLMJudgeHappyPath:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        provider = _MockProvider('{"passed": true, "score": 0.95, "detail": "great output"}')
        judge = LLMJudge(provider)
        score = await judge.evaluate(_case(), _run("hello world"))
        assert score.passed is True
        assert abs(score.score - 0.95) < 0.001
        assert "great output" in score.detail
        assert "mock-judge" in score.evaluator

    @pytest.mark.asyncio
    async def test_failed_verdict(self):
        provider = _MockProvider('{"passed": false, "score": 0.1, "detail": "wrong answer"}')
        judge = LLMJudge(provider)
        score = await judge.evaluate(_case(), _run("bye"))
        assert score.passed is False
        assert score.score == pytest.approx(0.1)

    @pytest.mark.asyncio
    async def test_score_clamped_to_range(self):
        # score > 1 should be clamped to 1.0
        provider = _MockProvider('{"passed": true, "score": 99.0, "detail": "ok"}')
        judge = LLMJudge(provider)
        score = await judge.evaluate(_case(), _run("hi"))
        assert score.score == 1.0


class TestLLMJudgeMalformedResponse:
    @pytest.mark.asyncio
    async def test_malformed_json_returns_failed_score(self):
        provider = _MockProvider("not json at all, sorry")
        judge = LLMJudge(provider)
        score = await judge.evaluate(_case(), _run("hi"))
        assert score.passed is False
        assert score.score == 0.0

    @pytest.mark.asyncio
    async def test_code_fenced_json_extracted(self):
        provider = _MockProvider(
            'Here is the verdict:\n```json\n{"passed": true, "score": 0.8, "detail": "ok"}\n```'
        )
        judge = LLMJudge(provider)
        score = await judge.evaluate(_case(), _run("answer"))
        assert score.passed is True
        assert score.score == pytest.approx(0.8)

    @pytest.mark.asyncio
    async def test_prose_wrapped_json_extracted(self):
        provider = _MockProvider(
            'The answer is good. {"passed": false, "score": 0.3, "detail": "partial"} End.'
        )
        judge = LLMJudge(provider)
        score = await judge.evaluate(_case(), _run("answer"))
        assert score.passed is False
        assert score.score == pytest.approx(0.3)

    @pytest.mark.asyncio
    async def test_missing_fields_graceful(self):
        # 'score' missing — should default to 0.0
        provider = _MockProvider('{"passed": true}')
        judge = LLMJudge(provider)
        score = await judge.evaluate(_case(), _run("answer"))
        assert score.passed is True
        assert score.score == 0.0

    @pytest.mark.asyncio
    async def test_provider_error_graceful(self):
        class _ErrorProvider(_MockProvider):
            async def complete(self, *args, **kwargs):
                raise RuntimeError("network error")

        judge = LLMJudge(_ErrorProvider())
        score = await judge.evaluate(_case(), _run("answer"))
        assert score.passed is False
        assert "network error" in score.detail


# ---------------------------------------------------------------------------
# _extract_json helper tests
# ---------------------------------------------------------------------------


class TestExtractJson:
    def test_direct_parse(self):
        result = _extract_json('{"a": 1}')
        assert result == {"a": 1}

    def test_strips_code_fence(self):
        result = _extract_json('```json\n{"x": 2}\n```')
        assert result == {"x": 2}

    def test_extracts_from_prose(self):
        result = _extract_json('Some text {"y": 3} more text')
        assert result == {"y": 3}

    def test_fallback_on_garbage(self):
        result = _extract_json("totally unparseable ###")
        assert result["passed"] is False
        assert result["score"] == 0.0


# ---------------------------------------------------------------------------
# EvalSuite end-to-end tests
# ---------------------------------------------------------------------------


class TestEvalSuiteEndToEnd:
    @staticmethod
    def _make_suite(n_cases: int = 3, checks=None):
        cases = [
            EvalCase(
                prompt=f"Case {i}",
                expected=f"answer {i}",
                metadata={"case_id": str(i)},
            )
            for i in range(n_cases)
        ]
        if checks is None:
            checks = [{"type": "min_length", "value": 1}]
        evaluators = [RubricEvaluator(checks=checks)]
        return EvalSuite(name="test-suite", cases=cases, evaluators=evaluators)

    @pytest.mark.asyncio
    async def test_all_pass(self):
        suite = self._make_suite()

        async def agent(case: EvalCase) -> EvalRun:
            cid = str(case.metadata.get("case_id", "0"))
            return EvalRun(case_id=cid, agent_output="non-empty answer", ok=True)

        report = await suite.run(agent)
        assert report.summary["pass_rate"] == 1.0
        assert len(report.runs) == 3

    @pytest.mark.asyncio
    async def test_all_fail(self):
        suite = self._make_suite(checks=[{"type": "min_length", "value": 1000}])

        async def agent(case: EvalCase) -> EvalRun:
            cid = str(case.metadata.get("case_id", "0"))
            return EvalRun(case_id=cid, agent_output="short", ok=True)

        report = await suite.run(agent)
        assert report.summary["pass_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_partial_pass_rate(self):
        """2 of 3 cases pass → pass_rate ≈ 0.666."""
        suite = self._make_suite(n_cases=3, checks=[{"type": "contains", "value": "pass"}])

        async def agent(case: EvalCase) -> EvalRun:
            cid = str(case.metadata.get("case_id", "0"))
            output = "I pass" if cid in ("0", "1") else "I fail"
            return EvalRun(case_id=cid, agent_output=output, ok=True)

        report = await suite.run(agent)
        assert abs(report.summary["pass_rate"] - 2 / 3) < 0.01

    @pytest.mark.asyncio
    async def test_multiple_evaluators_per_case(self):
        cases = [EvalCase(prompt="p", expected="e", metadata={"case_id": "0"})]
        evaluators = [
            RubricEvaluator(checks=[{"type": "min_length", "value": 1}]),
            RubricEvaluator(checks=[{"type": "max_length", "value": 100}]),
        ]
        suite = EvalSuite(name="multi", cases=cases, evaluators=evaluators)

        async def agent(case: EvalCase) -> EvalRun:
            return EvalRun(case_id="0", agent_output="hello", ok=True)

        report = await suite.run(agent)
        _, _, scores = report.runs[0]
        assert len(scores) == 2
        assert all(s.passed for s in scores)

    @pytest.mark.asyncio
    async def test_summary_keys_present(self):
        suite = self._make_suite()

        async def agent(case: EvalCase) -> EvalRun:
            return EvalRun(case_id=str(case.metadata.get("case_id")), agent_output="ok", ok=True)

        report = await suite.run(agent)
        assert "pass_rate" in report.summary
        assert "mean_score" in report.summary

    @pytest.mark.asyncio
    async def test_evaluator_exception_is_recovered(self):
        """An evaluator that raises should not crash the suite."""

        class _BrokenEvaluator(RubricEvaluator):
            async def evaluate(self, case, run):
                raise RuntimeError("evaluator broke")

        cases = [EvalCase(prompt="p", metadata={"case_id": "0"})]
        suite = EvalSuite(name="broken", cases=cases, evaluators=[_BrokenEvaluator(checks=[])])

        async def agent(case: EvalCase) -> EvalRun:
            return EvalRun(case_id="0", agent_output="hi", ok=True)

        report = await suite.run(agent)
        _, _, scores = report.runs[0]
        assert len(scores) == 1
        assert scores[0].passed is False
        assert "evaluator error" in scores[0].detail

    @pytest.mark.asyncio
    async def test_empty_case_list_produces_empty_summary(self):
        suite = EvalSuite(name="empty", cases=[], evaluators=[])

        async def agent(case: EvalCase) -> EvalRun:  # pragma: no cover
            return EvalRun(case_id="x", agent_output="y", ok=True)

        report = await suite.run(agent)
        assert report.runs == []
        assert report.summary == {}


# ---------------------------------------------------------------------------
# JsonDataset tests
# ---------------------------------------------------------------------------


class TestJsonDataset:
    def _write(self, data: str, suffix: str = ".json") -> Path:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False, encoding="utf-8")
        tmp.write(data)
        tmp.close()
        return Path(tmp.name)

    def test_valid_json_file(self):
        data = json.dumps(
            {
                "cases": [
                    {"prompt": "Hello?", "expected": "Hi!", "rubric": "must say hi"},
                    {"prompt": "Bye?"},
                ]
            }
        )
        path = self._write(data)
        ds = JsonDataset(path)
        cases = ds.load()
        assert len(cases) == 2
        assert cases[0].prompt == "Hello?"
        assert cases[0].expected == "Hi!"
        assert cases[1].expected is None

    def test_valid_yaml_file(self):
        yaml_text = "cases:\n  - prompt: 'Say hi'\n    expected: 'hi'\n"
        path = self._write(yaml_text, suffix=".yaml")
        ds = JsonDataset(path)
        cases = ds.load()
        assert len(cases) == 1
        assert cases[0].prompt == "Say hi"

    def test_missing_prompt_raises(self):
        data = json.dumps({"cases": [{"expected": "hi"}]})
        path = self._write(data)
        with pytest.raises(ValueError, match="missing required field 'prompt'"):
            JsonDataset(path).load()

    def test_missing_cases_key_raises(self):
        data = json.dumps({"items": []})
        path = self._write(data)
        with pytest.raises(ValueError, match="'cases' key"):
            JsonDataset(path).load()

    def test_malformed_json_raises(self):
        path = self._write("{not: valid json}")
        with pytest.raises(ValueError, match="JSON parse error"):
            JsonDataset(path).load()

    def test_file_not_found_raises(self):
        with pytest.raises(FileNotFoundError):
            JsonDataset("/nonexistent/path/data.json").load()

    def test_metadata_case_id_auto_set(self):
        data = json.dumps({"cases": [{"prompt": "q1"}, {"prompt": "q2"}]})
        path = self._write(data)
        cases = JsonDataset(path).load()
        assert cases[0].metadata["case_id"] == "0"
        assert cases[1].metadata["case_id"] == "1"

    def test_metadata_case_id_preserved_if_given(self):
        data = json.dumps({"cases": [{"prompt": "q", "metadata": {"case_id": "my-id"}}]})
        path = self._write(data)
        cases = JsonDataset(path).load()
        assert cases[0].metadata["case_id"] == "my-id"


# ---------------------------------------------------------------------------
# EvalReport summary math tests
# ---------------------------------------------------------------------------


class TestEvalReportSummaryMath:
    def _build_report(self, scores: list[list[EvalScore]]) -> EvalReport:
        cases = [_case(f"p{i}") for i in range(len(scores))]
        runs = [_run(f"out{i}", case_id=str(i)) for i in range(len(scores))]
        run_tuples = list(zip(cases, runs, scores))
        # Use EvalSuite._compute_summary directly
        suite = EvalSuite(name="math-test", cases=cases, evaluators=[])
        summary = suite._compute_summary(run_tuples)
        return EvalReport(suite="math-test", runs=run_tuples, summary=summary)

    def test_all_pass_rate_1(self):
        scores = [[EvalScore(passed=True, score=1.0, evaluator="rubric")] for _ in range(5)]
        report = self._build_report(scores)
        assert report.summary["pass_rate"] == 1.0

    def test_half_pass_rate(self):
        scores = [
            [EvalScore(passed=True, score=1.0, evaluator="rubric")],
            [EvalScore(passed=False, score=0.0, evaluator="rubric")],
        ]
        report = self._build_report(scores)
        assert report.summary["pass_rate"] == 0.5

    def test_mean_score(self):
        scores = [
            [EvalScore(passed=True, score=0.8, evaluator="rubric")],
            [EvalScore(passed=True, score=0.6, evaluator="rubric")],
        ]
        report = self._build_report(scores)
        assert abs(report.summary["mean_score"] - 0.7) < 0.001
