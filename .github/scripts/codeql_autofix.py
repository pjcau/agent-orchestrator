#!/usr/bin/env python3
"""Generate a minimal patch for one code-scanning issue using an LLM.

Invoked by `.github/workflows/codeql-autofix.yml`. Reads the issue
metadata, pulls the FULL file content, asks an OpenRouter model for a
tiny edit expressed as one or more SEARCH/REPLACE blocks, applies them
to the working tree via exact string replacement, and lets the calling
workflow create the branch + draft PR.

Why SEARCH/REPLACE blocks (Aider-style) and not unified diffs?
Unified diffs depend on exact line numbers and exact context-line
whitespace. LLMs drift on both — even a single trailing-whitespace
difference makes `git apply` reject the patch. SEARCH/REPLACE blocks
are validated by *finding the search text verbatim* in the file and
replacing it in place: zero drift, zero hunk-header confusion. Same
technique used by aider, Cline, and Claude Code.

Block format:
    <<<<<<< SEARCH
    exact contiguous text from the file
    =======
    replacement text
    >>>>>>> REPLACE

Inputs (via argv):
    --issue NUMBER   the GitHub issue number to address
    --repo OWNER/REPO

Reads:
    OPENROUTER_API_KEY  required for the LLM call
    GH_TOKEN            for `gh` API calls

Writes:
    a `Fixes-#<N>.patch` file at repo root containing the resulting
    `git diff` of all applied changes. The workflow attaches it to the
    PR for audit.

Safety constraints:
* Each SEARCH block must appear EXACTLY ONCE in the file (otherwise we
  cannot prove which occurrence the model meant).
* All blocks together must touch at most ONE file and produce at most
  80 changed lines. Bigger suggestions get a comment on the issue and
  are dropped — manual review territory.
* No tests are run here; CI on the PR is the ground truth.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

MODEL = "deepseek/deepseek-v4-flash"
CONTEXT_LINES = 25  # lines around the flagged line we feed the LLM (display)
MAX_DIFF_LINES = 80  # safety cap on the resulting `git diff`
TIMEOUT_S = 60
MAX_FILE_BYTES = 80_000  # truncate huge files before sending to the LLM


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------


def gh(*args: str, capture: bool = True) -> str:
    """Thin `gh` CLI wrapper.

    The workflow already has `gh` authenticated via GH_TOKEN; we use it
    rather than the bare HTTP API so behaviour matches the rest of the
    automation in `auto-heal.yml`.
    """
    res = subprocess.run(
        ["gh", *args],
        check=True,
        capture_output=capture,
        text=True,
    )
    return res.stdout if capture else ""


def fetch_issue(repo: str, number: int) -> dict:
    raw = gh(
        "issue",
        "view",
        str(number),
        "--repo",
        repo,
        "--json",
        "number,title,body,labels",
    )
    return json.loads(raw)


def comment_on_issue(repo: str, number: int, message: str) -> None:
    """Best-effort issue comment for failures."""
    try:
        gh(
            "issue",
            "comment",
            str(number),
            "--repo",
            repo,
            "--body",
            message,
        )
    except subprocess.CalledProcessError as exc:
        print(f"warning: failed to comment on issue #{number}: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Issue → (rule, file, line)
# ---------------------------------------------------------------------------


TITLE_RE = re.compile(
    r"^code-scanning:\s+(?P<rule>[\w./-]+)\s+in\s+(?P<file>[^:]+):(?P<line>\d+)\s*$"
)


def parse_title(title: str) -> tuple[str, str, int]:
    m = TITLE_RE.match(title.strip())
    if not m:
        raise ValueError(f"unrecognised issue title: {title!r}")
    return m["rule"], m["file"], int(m["line"])


# ---------------------------------------------------------------------------
# Source context extraction
# ---------------------------------------------------------------------------


def read_context(file_path: Path, line: int, span: int) -> tuple[str, int]:
    """Return the snippet (with line numbers) and the offset of the first line.

    We deliberately include line numbers in the snippet to make it
    obvious to the LLM which line is at fault — most CodeQL alerts are
    very localised (single line or small block).
    """
    lines = file_path.read_text().splitlines()
    start = max(0, line - span - 1)
    end = min(len(lines), line + span)
    out = []
    for i, content in enumerate(lines[start:end], start=start + 1):
        marker = " >>> " if i == line else "     "
        out.append(f"{i:>5}{marker}{content}")
    return "\n".join(out), start + 1


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


def build_prompt(
    rule: str,
    file: str,
    line: int,
    snippet: str,
    file_full: str,
    issue_body: str,
) -> str:
    return textwrap.dedent(f"""\
        You are a senior security engineer fixing a CodeQL alert in a Python
        codebase. Produce a MINIMAL fix expressed as one or more
        SEARCH/REPLACE blocks.

        ## Output format (STRICT)

        Each block looks exactly like this, with the exact markers and
        no surrounding text:

        <<<<<<< SEARCH
        <exact contiguous text from the file>
        =======
        <replacement text>
        >>>>>>> REPLACE

        Rules for the blocks:
        * SEARCH content must appear LITERALLY in the file (matching
          indentation, whitespace, comments) — no paraphrasing.
        * Each SEARCH must be unique within the file (pick enough
          surrounding context lines to be unambiguous, but no more).
        * REPLACE must keep the same indentation level as SEARCH.
        * You may emit multiple blocks if needed, but all of them MUST
          edit the SAME file ({file}).
        * Keep behaviour intact; only close the security weakness.
        * Total changed lines across all blocks: ≤ 30 (we cap at 80).

        If you cannot fix it with confidence, reply with the single
        literal token: NOFIX

        Output ONLY the blocks (or `NOFIX`). No prose, no fences, no
        commentary.

        ## Alert

        Rule: {rule}
        Location: {file}:{line}

        ## Issue body

        {issue_body.strip() or '(empty)'}

        ## Source context (line-numbered, `>>>` marks the alerted line)

        ```
        {snippet}
        ```

        ## Full file content (for unambiguous SEARCH matching)

        ```
        {file_full}
        ```
        """)


def call_openrouter(prompt: str) -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY missing in env")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(
            {
                "model": MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You output ONLY a unified diff or the literal word NOFIX."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 1500,
            }
        ).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/pjcau/agent-orchestrator",
            "X-Title": "auto-heal / codeql-autofix",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        payload = json.loads(resp.read())
    return payload["choices"][0]["message"]["content"].strip()


# ---------------------------------------------------------------------------
# Diff hygiene + application
# ---------------------------------------------------------------------------


FENCE_RE = re.compile(r"^```\w*\s*$", re.MULTILINE)

# Greedy across the SEARCH-marker so we tolerate the model emitting
# accidental blank lines inside a block.
BLOCK_RE = re.compile(
    r"<<<<<<<\s*SEARCH\s*\n"
    r"(?P<search>.*?)"
    r"\n=======\s*\n"
    r"(?P<replace>.*?)"
    r"\n>>>>>>>\s*REPLACE\s*$",
    re.MULTILINE | re.DOTALL,
)


def strip_fences(text: str) -> str:
    """Remove markdown code fences the model added despite instructions."""
    return FENCE_RE.sub("", text).strip() + "\n"


def parse_blocks(raw: str) -> list[tuple[str, str]]:
    """Return a list of (search, replace) pairs from the LLM output."""
    return [(m["search"], m["replace"]) for m in BLOCK_RE.finditer(raw)]


class BlockApplyError(Exception):
    """Raised by :func:`apply_blocks` when a block cannot be applied."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def apply_blocks(file_path: Path, blocks: list[tuple[str, str]]) -> int:
    """Apply each SEARCH/REPLACE block to ``file_path``. Returns lines changed.

    Each block is applied in sequence on the *current* (possibly
    already-modified) file content, mirroring how aider and Claude Code
    handle multi-block edits. Failures raise :class:`BlockApplyError`
    with a stable code we map to an issue comment.
    """
    if not blocks:
        raise BlockApplyError("no_blocks", "no SEARCH/REPLACE blocks parsed from LLM output")
    text = file_path.read_text()
    original = text
    for idx, (search, replace) in enumerate(blocks, start=1):
        occurrences = text.count(search)
        if occurrences == 0:
            raise BlockApplyError(
                "search_not_found",
                f"block #{idx} SEARCH text not found verbatim in {file_path}",
            )
        if occurrences > 1:
            raise BlockApplyError(
                "search_ambiguous",
                f"block #{idx} SEARCH text appears {occurrences} times "
                f"in {file_path} — refuse to guess which to replace",
            )
        text = text.replace(search, replace, 1)
    file_path.write_text(text)

    # Estimate "lines changed" as the symmetric diff length so the
    # MAX_DIFF_LINES cap is honoured. Cheap and conservative.
    changed = len(set(original.splitlines()) ^ set(text.splitlines()))
    return changed


def git_diff() -> str:
    """Return the current `git diff` of unstaged changes."""
    return subprocess.run(
        ["git", "diff"], check=True, capture_output=True, text=True
    ).stdout


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()

    issue = fetch_issue(args.repo, args.issue)
    rule, file, line = parse_title(issue["title"])
    src = Path(file)
    if not src.is_file():
        comment_on_issue(
            args.repo,
            args.issue,
            f"codeql-autofix: source file `{file}` no longer exists; skipping.",
        )
        return 2

    snippet, _start = read_context(src, line, CONTEXT_LINES)
    full = src.read_text()
    if len(full) > MAX_FILE_BYTES:
        # Truncate around the alerted line so the SEARCH text still has
        # enough surrounding context to be unique.
        lines = full.splitlines()
        keep = MAX_FILE_BYTES // 80  # ~rough line cap
        lo = max(0, line - keep // 2)
        hi = min(len(lines), line + keep // 2)
        full = "\n".join(lines[lo:hi])

    prompt = build_prompt(rule, file, line, snippet, full, issue.get("body") or "")
    try:
        raw = call_openrouter(prompt)
    except Exception as exc:
        comment_on_issue(
            args.repo,
            args.issue,
            f"codeql-autofix: LLM call failed — `{exc}`. Will retry tomorrow.",
        )
        return 3

    if raw.strip().upper() == "NOFIX":
        comment_on_issue(
            args.repo,
            args.issue,
            "codeql-autofix: the LLM declined to suggest a fix "
            "(rule + context too ambiguous). Will retry tomorrow with newer context.",
        )
        return 4

    cleaned = strip_fences(raw)
    blocks = parse_blocks(cleaned)
    if not blocks:
        comment_on_issue(
            args.repo,
            args.issue,
            "codeql-autofix: LLM output did not contain a valid "
            "SEARCH/REPLACE block. Will retry on the next sweep.\n\n"
            f"<details><summary>raw output</summary>\n\n```\n{cleaned[:1500]}\n```\n</details>",
        )
        return 5

    try:
        changed = apply_blocks(src, blocks)
    except BlockApplyError as e:
        comment_on_issue(
            args.repo,
            args.issue,
            f"codeql-autofix: could not apply LLM blocks (`{e.code}`): {e.detail}. "
            "Will retry on the next sweep with fresh context.",
        )
        return 6

    if changed > MAX_DIFF_LINES:
        # Revert and bail.
        subprocess.run(["git", "checkout", "--", str(src)], check=True)
        comment_on_issue(
            args.repo,
            args.issue,
            f"codeql-autofix: suggested edit changed {changed} lines (cap {MAX_DIFF_LINES}). "
            "Reverted; manual review recommended.",
        )
        return 7

    diff = git_diff()
    Path(f"Fixes-#{args.issue}.patch").write_text(diff)
    print(
        f"OK: applied {len(blocks)} block(s), ~{changed} lines changed "
        f"for issue #{args.issue}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
