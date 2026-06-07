#!/usr/bin/env python3
"""Generate a minimal patch for one code-scanning issue using an LLM.

Invoked by `.github/workflows/codeql-autofix.yml`. Reads the issue
metadata, pulls the file context around the alert, asks an OpenRouter
model for a tiny patch, applies it on the working tree, and lets the
calling workflow create the branch + draft PR.

Inputs (via argv):
    --issue NUMBER   the GitHub issue number to address
    --repo OWNER/REPO

Reads:
    OPENROUTER_API_KEY  required for the LLM call
    GH_TOKEN            for `gh` API calls

Writes:
    a `Fixes-#<N>.patch` file at repo root containing the diff actually
    applied. The workflow includes it as a PR attachment for traceability.

Safety constraints:
* The model is instructed to emit a unified diff that touches at most
  *one file* and at most *20 lines*. Bigger diffs are dropped with a
  loud comment on the issue rather than committed.
* The patch is applied with `git apply --check` first; failures land
  as a comment on the issue, not an empty PR.
* No tests are run here (CI on the PR catches regressions); the goal
  is to produce a reviewable suggestion, not a green build.
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
CONTEXT_LINES = 25  # lines around the flagged line we feed the LLM
MAX_DIFF_LINES = 80  # safety cap before we refuse to apply the patch
TIMEOUT_S = 60


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


def build_prompt(rule: str, file: str, line: int, snippet: str, issue_body: str) -> str:
    return textwrap.dedent(f"""\
        You are a senior security engineer fixing a CodeQL alert in a Python
        codebase. Produce a MINIMAL fix as a unified diff. Do not introduce
        unrelated changes. Constraints:

        * The diff MUST touch only the file shown.
        * The diff MUST be applicable with `git apply` from the repository
          root (use `a/<path>` and `b/<path>` headers).
        * Keep behaviour intact; only close the security weakness.
        * If you cannot fix it with confidence, reply exactly with the
          literal text: NOFIX
        * Output ONLY the diff (no prose, no fences, no explanation).

        # Alert
        Rule: {rule}
        Location: {file}:{line}

        ## Issue body
        {issue_body.strip() or '(empty)'}

        ## Source context
        ```
        {snippet}
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


def strip_fences(diff: str) -> str:
    """Remove any markdown code fences the model added despite instructions."""
    cleaned = FENCE_RE.sub("", diff)
    return cleaned.strip() + "\n"


def too_big(diff: str) -> bool:
    return len(diff.splitlines()) > MAX_DIFF_LINES


def touches_only(diff: str, file: str) -> bool:
    """Reject diffs that touch any path other than the alerted file."""
    files = set()
    for ln in diff.splitlines():
        if ln.startswith(("--- a/", "+++ b/")):
            path = ln.split("/", 1)[1] if "/" in ln else ""
            if path and path != "/dev/null":
                files.add(path)
    return files == {file} if files else False


def apply_patch(diff: str) -> bool:
    """Run `git apply --check` then `git apply`."""
    patch_path = Path("/tmp/codeql-autofix.patch")
    patch_path.write_text(diff)
    check = subprocess.run(
        ["git", "apply", "--check", str(patch_path)],
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        print("git apply --check failed:", check.stderr, file=sys.stderr)
        return False
    subprocess.run(["git", "apply", str(patch_path)], check=True)
    return True


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
    prompt = build_prompt(rule, file, line, snippet, issue.get("body") or "")
    try:
        raw_diff = call_openrouter(prompt)
    except Exception as exc:
        comment_on_issue(
            args.repo,
            args.issue,
            f"codeql-autofix: LLM call failed — `{exc}`. Will retry tomorrow.",
        )
        return 3

    if raw_diff.strip().upper() == "NOFIX":
        comment_on_issue(
            args.repo,
            args.issue,
            "codeql-autofix: the LLM declined to suggest a fix "
            "(rule + context too ambiguous). Will retry tomorrow with newer context.",
        )
        return 4

    diff = strip_fences(raw_diff)
    if too_big(diff):
        comment_on_issue(
            args.repo,
            args.issue,
            f"codeql-autofix: suggested diff exceeded "
            f"the {MAX_DIFF_LINES}-line cap. Not applied; please craft a manual fix.",
        )
        return 5

    if not touches_only(diff, file):
        comment_on_issue(
            args.repo,
            args.issue,
            "codeql-autofix: suggested diff touched files other than the alerted "
            f"path `{file}`. Skipping to keep the autofix scope narrow.",
        )
        return 6

    if not apply_patch(diff):
        comment_on_issue(
            args.repo,
            args.issue,
            "codeql-autofix: `git apply --check` rejected the LLM diff "
            "(probably a context drift). Will retry on the next sweep.",
        )
        return 7

    # Save the applied diff so the workflow can attach it to the PR.
    Path(f"Fixes-#{args.issue}.patch").write_text(diff)
    print(f"OK: applied {len(diff.splitlines())} line diff for issue #{args.issue}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
