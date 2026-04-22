"""Modality detection for task inputs (PR #88).

Lightweight, deterministic rule-based classifier — no ML, no LLM. Chooses
the most specific label when multiple signals fire so callers can route
to vision, code, or structured-data specialists without sending every
input through a VLM.

Usage::

    from agent_orchestrator.core.modality import detect_modality, Modality

    mod = detect_modality("compute 2+2")                # Modality.TEXT
    mod = detect_modality({"image": "base64...", "text": "describe"})  # IMAGE
    mod = detect_modality("def f(x): return x * 2")     # CODE
    mod = detect_modality({"rows": [...], "cols": [...]})  # STRUCTURED

Metrics: when a ``MetricsRegistry`` is passed to ``record_detection``,
the counter ``modality_detected_total{modality=<label>}`` is incremented,
which the dashboard surfaces on the routing timeline.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any


class Modality(str, Enum):
    TEXT = "text"
    CODE = "code"
    IMAGE = "image"
    STRUCTURED = "structured"
    EQUATION = "equation"
    MIXED = "mixed"


# ── Code heuristics ──────────────────────────────────────────────────
# These are intentionally stricter than a generic "contains ()" check.
# Each hit earns one point; tasks reaching >= 2 points are CODE.
_CODE_PATTERNS = [
    re.compile(r"```[a-z0-9+#-]{0,20}\s*\n"),  # fenced code block
    re.compile(r"\b(def|class|function|const|let|var|import)\b\s+\w"),
    re.compile(r"=>\s*\{"),  # JS arrow body
    re.compile(r"\bpublic\s+(static\s+)?(void|class)\b"),  # Java-ish
    re.compile(r"\bpackage\s+\w+"),  # Go / Java
    re.compile(r"#include\s*<"),  # C / C++
    re.compile(r"^\s*#!\s*/"),  # Shebang
    re.compile(r"\(\s*[\w, ]*\)\s*(=>|\{|:)"),  # function signature
]

# Equation signals: LaTeX-style $$...$$ or $...$ containing digits/ops,
# or typical mathematical symbols.
_EQUATION_PATTERNS = [
    re.compile(r"\$\$[^$]+\$\$"),
    re.compile(r"\$[^$\n]{3,80}\$"),
    re.compile(r"[=≠≤≥]{1}.*[+\-*/].*[0-9]"),  # arithmetic with relation
    re.compile(r"\\(?:int|sum|prod|frac|sqrt)(?=[_^\s\\{(]|$)"),
]


def _is_image_bytes(data: Any) -> bool:
    """Detect common image magic bytes without importing Pillow."""
    if not isinstance(data, (bytes, bytearray)):
        return False
    if len(data) < 8:
        return False
    head = bytes(data[:12])
    return (
        head.startswith(b"\x89PNG\r\n\x1a\n")  # PNG
        or head.startswith(b"\xff\xd8\xff")  # JPEG
        or head.startswith(b"GIF87a")
        or head.startswith(b"GIF89a")
        or head.startswith(b"RIFF")
        and b"WEBP" in head  # WebP
    )


def _looks_structured(data: Any) -> bool:
    """Dicts/lists that represent tabular or structured data, not chat."""
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return True
    if isinstance(data, dict):
        # Heuristic: at least 3 keys and no "content" (which would look
        # like a chat message envelope) strongly suggests structured data.
        keys = set(data.keys())
        chat_like = {"role", "content", "message", "prompt"}
        if keys & chat_like:
            return False
        return len(keys) >= 3
    return False


def detect_modality(task_input: Any) -> Modality:
    """Classify the dominant modality of ``task_input``.

    Accepts strings, bytes, dicts, lists, or mixed compositions. Priority:
    IMAGE > MIXED > STRUCTURED > EQUATION > CODE > TEXT. The ranking
    reflects how sharply the detection narrows the set of capable
    providers — image content can only be handled by VLM-capable models.
    """
    # Image bytes directly
    if _is_image_bytes(task_input):
        return Modality.IMAGE

    # Dict with explicit image field (base64, url, bytes)
    if isinstance(task_input, dict):
        has_image = any(k in task_input for k in ("image", "image_url", "vision", "attachment"))
        has_text = bool(task_input.get("text") or task_input.get("prompt"))
        if has_image and has_text:
            return Modality.MIXED
        if has_image:
            return Modality.IMAGE
        if _looks_structured(task_input):
            return Modality.STRUCTURED

    if isinstance(task_input, list):
        if _looks_structured(task_input):
            return Modality.STRUCTURED

    text = str(task_input)

    # Equation signals are relatively rare and specific — check before code.
    eq_hits = sum(1 for p in _EQUATION_PATTERNS if p.search(text))
    if eq_hits >= 1:
        return Modality.EQUATION

    code_hits = sum(1 for p in _CODE_PATTERNS if p.search(text))
    if code_hits >= 2:
        return Modality.CODE

    return Modality.TEXT


def record_detection(modality: Modality, metrics: Any = None) -> None:
    """Increment the ``modality_detected_total`` counter when metrics given."""
    if metrics is None:
        return
    metrics.counter(
        "modality_detected_total",
        "Total task inputs classified by modality",
        labels={"modality": modality.value},
    ).inc()
