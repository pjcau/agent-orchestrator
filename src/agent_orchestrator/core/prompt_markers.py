"""Marker-based prompt section injection.

Lets callers update specific named sections inside a base prompt without
rewriting the whole thing. Sections are delimited by HTML-style comments
(``<!-- NAME START --> ... <!-- NAME END -->``) so markers are preserved
across updates, preventing configuration drift.

Example::

    base = "You are a helpful assistant.\\n<!-- RULES START -->\\nno rules yet\\n<!-- RULES END -->"
    out = inject_marker_sections(base, {"RULES": "1. be concise\\n2. cite sources"})
    # RULES block inside the base prompt is replaced; everything else is untouched.

Markers that do not exist in the base prompt are *appended* at the end with
fresh start/end tags, so the section becomes updatable on subsequent calls.
"""

from __future__ import annotations

import re

# Compiled once — used to locate an existing section for a given marker.
_SECTION_RE_TEMPLATE = r"{start}.*?{end}"


def _tags(marker: str) -> tuple[str, str]:
    return f"<!-- {marker} START -->", f"<!-- {marker} END -->"


def inject_marker_sections(base_prompt: str, sections: dict[str, str]) -> str:
    """Apply ``sections`` to ``base_prompt`` using marker-delimited blocks.

    Each key in ``sections`` becomes a named section. If the section already
    exists in ``base_prompt`` (identified by its START/END comment tags),
    it is replaced in place. Otherwise a new section is appended.

    The function is pure (does not mutate inputs) and idempotent — calling
    it twice with the same inputs yields the same output.

    Args:
        base_prompt: The prompt to update.
        sections: Mapping of marker name to new section content.

    Returns:
        The updated prompt with all sections applied.
    """
    result = base_prompt
    for marker, content in sections.items():
        start, end = _tags(marker)
        pattern = _SECTION_RE_TEMPLATE.format(
            start=re.escape(start),
            end=re.escape(end),
        )
        replacement = f"{start}\n{content}\n{end}"
        if re.search(pattern, result, flags=re.DOTALL):
            result = re.sub(pattern, replacement, result, count=1, flags=re.DOTALL)
        else:
            sep = "" if result.endswith("\n") else "\n"
            result = f"{result}{sep}{replacement}"
    return result


def extract_marker_sections(prompt: str) -> dict[str, str]:
    """Return the current content of all marker-delimited sections in ``prompt``.

    Useful for diffing two prompts at the section level to detect drift.
    """
    sections: dict[str, str] = {}
    # Find every <!-- NAME START --> ... <!-- NAME END --> block.
    it = re.finditer(
        r"<!-- ([A-Z0-9_]+) START -->\n?(.*?)\n?<!-- \1 END -->",
        prompt,
        flags=re.DOTALL,
    )
    for m in it:
        sections[m.group(1)] = m.group(2)
    return sections


def diff_sections(a: str, b: str) -> dict[str, tuple[str, str]]:
    """Return sections that differ between two prompts.

    Keys are marker names; values are ``(a_content, b_content)`` tuples. Only
    markers present in at least one side are returned — identical sections are
    omitted.
    """
    sa = extract_marker_sections(a)
    sb = extract_marker_sections(b)
    out: dict[str, tuple[str, str]] = {}
    for marker in set(sa) | set(sb):
        av = sa.get(marker, "")
        bv = sb.get(marker, "")
        if av != bv:
            out[marker] = (av, bv)
    return out
