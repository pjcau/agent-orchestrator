"""Dependency verifier — catches unresolvable pins in requirements files.

Without hitting the network (which would be flaky and expensive in tests),
this verifier parses every `requirements*.txt` and flags pins that match a
**known-bad pattern**: a package name that exists on PyPI **only** in major
versions that do NOT satisfy the requested pin. The seed list covers the
real failures we have observed in the field.

For deeper checks, a subclass can override `_resolve(pkg, specifier)` to
hit `pypi.org/pypi/{pkg}/json` and return the latest matching version.
The default `KNOWN_BAD_PINS` implementation runs offline and is fast.

Why not just shell out to `pip install --dry-run`? Because:
- The pip resolver hits the network → ~5 s per run, flaky in CI.
- We don't want to require `pip` on PATH for unit tests.
- The 2026-05-16 failure (`psycopg<3`) is deterministic and can be flagged
  from a static rule without a resolver call.

A subclass `LivePyPIDependencyVerifier` will be added in a later phase if
production usage demands more coverage.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent_orchestrator.core.verification_gate import VerifierFailure

# Known package names whose ONLY uploaded versions exceed the lower bound users
# typically request. Each entry: package name (lowercase) → message explaining
# what to use instead. The static check fires when a requirements pin
# constrains the package to a version below the listed `min_published_major`.
KNOWN_BAD_PINS: dict[str, tuple[int, str]] = {
    # `psycopg` (the modern package) only has 3.x releases — anyone asking for
    # `psycopg<3` is conflating it with the legacy `psycopg2-binary`.
    "psycopg": (3, "psycopg has no releases below 3.0 — use 'psycopg2-binary' for the v2 driver or 'psycopg>=3.0' for the v3 driver"),
}

_REQ_LINE = re.compile(
    r"""
    ^\s*
    (?P<pkg>[A-Za-z0-9][A-Za-z0-9._\-]*)     # package name
    \s*
    (?P<specs>(?:[<>=!~]=?\s*[^,\s]+\s*,?\s*)*)  # version specifiers
    \s*$
    """,
    re.VERBOSE,
)
_SPEC = re.compile(r"(?P<op>[<>=!~]=?)\s*(?P<ver>[0-9][0-9A-Za-z.\-]*)")


class DependencyVerifier:
    name = "dependency"
    cost_estimate_s = 2.0

    def __init__(self, *, known_bad_pins: dict[str, tuple[int, str]] | None = None) -> None:
        self._known_bad = known_bad_pins if known_bad_pins is not None else KNOWN_BAD_PINS

    async def verify(self, workdir: Path) -> list[VerifierFailure]:
        failures: list[VerifierFailure] = []
        for req_file in _find_requirements(workdir):
            rel = str(req_file.relative_to(workdir))
            try:
                lines = req_file.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, raw in enumerate(lines, 1):
                line = raw.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                # strip inline comments
                if "#" in line:
                    line = line.split("#", 1)[0].strip()
                m = _REQ_LINE.match(line)
                if not m:
                    continue
                pkg = m.group("pkg").lower()
                if pkg not in self._known_bad:
                    continue
                upper = _extract_upper_bound(m.group("specs"))
                min_major, hint = self._known_bad[pkg]
                # Pin is satisfiable iff the largest allowed major >= the smallest
                # published major. `None` means no upper bound → satisfiable.
                if upper is None or upper >= min_major:
                    continue
                # Found a pin that excludes every published version of pkg.
                failures.append(
                    VerifierFailure(
                        verifier=self.name,
                        severity="error",
                        category="pypi_resolve",
                        message=(
                            f"{rel}:{lineno}: pin '{line}' rejects every published "
                            f"release of '{pkg}' (smallest available major is "
                            f"{min_major})"
                        ),
                        detail=hint,
                        file=rel,
                        exit_code=1,
                    )
                )
        return failures


def _find_requirements(workdir: Path) -> list[Path]:
    out: list[Path] = []
    for path in workdir.rglob("requirements*.txt"):
        if any(part in {".git", "node_modules", ".venv", "venv"} for part in path.parts):
            continue
        out.append(path)
    return out


def _extract_upper_bound(specs: str) -> int | None:
    """Return the major-version upper bound of a pin like `>=2.9,<3` → 2.

    Only considers `<` and `<=` operators. Returns None if no upper bound.
    For `<3` (exclusive) the upper bound on the major is `2`; for `<=3.0`
    it's `3`. We approximate with the integer part minus one for `<`.
    """
    upper: int | None = None
    for sm in _SPEC.finditer(specs):
        op = sm.group("op")
        ver = sm.group("ver")
        try:
            major = int(ver.split(".", 1)[0])
        except ValueError:
            continue
        if op == "<":
            candidate = major - 1
        elif op == "<=":
            candidate = major
        else:
            continue
        if upper is None or candidate < upper:
            upper = candidate
    return upper
