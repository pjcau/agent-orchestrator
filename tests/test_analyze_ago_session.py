"""Tests for the ago session-log analyzer (``scripts/analyze_ago_session.py``).

``scripts/`` is not a package, so the module is loaded by file path.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_MOD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "analyze_ago_session.py"
_spec = importlib.util.spec_from_file_location("analyze_ago_session", _MOD_PATH)
assert _spec and _spec.loader
analyzer = importlib.util.module_from_spec(_spec)
# Register before exec so @dataclass can resolve the module for type lookups.
sys.modules["analyze_ago_session"] = analyzer
_spec.loader.exec_module(analyzer)


def _ts(s: str) -> str:
    return f"2026-06-14T20:00:{s}Z DEBUG "


SAMPLE = "\n".join(
    [
        # Turn 1: writes a file, then a watch-mode test command times out, and
        # the step meter overshoots the planned budget.
        _ts("00.0") + "send prompt 1500B",
        _ts("01.0") + 'recv step idx=1 total=40 agent="team-lead" label=""',
        _ts("02.0") + "recv tool_call id=a1 name=file_write args=src/App.test.js",
        _ts("02.1") + "send tool_result id=a1 status=ok",
        _ts("03.0") + "recv tool_call id=a2 name=shell_exec args=npm test",
        _ts("04.0") + 'send tool_result id=a2 status=error reason="shell_timeout"',
        _ts("05.0") + 'recv step idx=70 total=40 agent="frontend" label="working"',
        _ts("06.0") + 'recv turn_end status="ok" steps=70 in=500000 out=8000 cost=0.5 error=""',
        # Turn 2: the same command fails three times — thrash.
        _ts("10.0") + "send prompt 100B",
        _ts("11.0") + "recv tool_call id=b1 name=shell_exec args=pytest -q",
        _ts("11.1") + 'send tool_result id=b1 status=error reason="shell_nonzero_exit"',
        _ts("12.0") + "recv tool_call id=b2 name=shell_exec args=pytest -q",
        _ts("12.1") + 'send tool_result id=b2 status=error reason="shell_nonzero_exit"',
        _ts("13.0") + "recv tool_call id=b3 name=shell_exec args=pytest -q",
        _ts("13.1") + 'send tool_result id=b3 status=error reason="shell_nonzero_exit"',
        _ts("14.0") + 'recv step idx=8 total=40 agent="backend" label="working"',
        _ts("15.0") + 'recv turn_end status="ok" steps=8 in=1000 out=10 cost=0.001 error=""',
    ]
)


def test_parse_splits_turns_and_counts_tools():
    turns = analyzer.parse_log(SAMPLE)
    assert len(turns) == 2
    t1, t2 = turns
    assert t1.tool_counts["file_write"] == 1
    assert t1.tool_counts["shell_exec"] == 1
    assert t1.file_writes == ["src/App.test.js"]
    assert t1.cost_usd == 0.5
    assert t1.max_step_idx == 70
    assert t2.tool_counts["shell_exec"] == 3
    assert t2.tool_errors == 3


def test_red_flags_catch_known_failure_modes():
    t1, t2 = analyzer.parse_log(SAMPLE)
    f1 = " | ".join(t1.red_flags())
    assert "overshoot" in f1  # 70 ≫ 40
    assert "shell_timeout" in f1  # watch-mode hang
    assert "cost" in f1  # 0.5 ≥ threshold

    f2 = " | ".join(t2.red_flags())
    assert "same command failed 3" in f2
    assert "pytest -q" in f2  # the actual command, thanks to args= logging


def test_zero_write_spinning_flag():
    log = "\n".join(
        [
            _ts("00.0") + "send prompt 200B",
            *[
                _ts(f"0{i}.0") + f'recv step idx={i} total=40 agent="backend" label="x"'
                for i in range(1, 7)
            ],
            _ts("09.0") + "recv tool_call id=c1 name=file_read args=README.md",
            _ts("09.1") + "send tool_result id=c1 status=ok",
            _ts("10.0") + 'recv turn_end status="ok" steps=6 in=10 out=2 cost=0.0 error=""',
        ]
    )
    (t,) = analyzer.parse_log(log)
    assert any("0 file_write" in flag for flag in t.red_flags())


def test_format_report_renders_table_and_flags():
    report = analyzer.format_report(analyzer.parse_log(SAMPLE))
    assert "turn(s)" in report
    assert "RED FLAGS" in report
    assert "MOST-FAILED COMMANDS" in report
    assert "pytest -q" in report
    assert "total cost: $0.50" in report
