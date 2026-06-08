#!/usr/bin/env bash
# Static type-check gate (Pyright — the same engine as the editor's Pylance).
#
# Why this exists: pytest and ruff do NOT type-check. A type error (e.g. an
# attribute that doesn't exist on a class, or a BaseException slipping through
# an `except Exception` narrow) passes both and only shows up in the editor's
# Pylance — i.e. never in CI. This script closes that gap.
#
# Modes:
#   ./scripts/typecheck.sh            # --changed (default): only Python files
#                                     #   modified vs the base. A file you touch
#                                     #   must be type-clean (boy-scout rule).
#   ./scripts/typecheck.sh --all      # whole tree — tracks the pre-existing
#                                     #   backlog; not yet zero, see pyrightconfig.json
#   ./scripts/typecheck.sh --staged   # only git-staged Python files (pre-commit)
#
# Base resolution for --changed (first hit wins):
#   $TYPECHECK_BASE  →  $GITHUB_BASE_REF (PRs)  →  merge-base with origin/main
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${1:---changed}"

run_pyright() {
  # No files → success (nothing to check). Pyright on zero args would scan
  # the whole include set, which is NOT what --changed/--staged mean.
  if [ "$#" -eq 0 ]; then
    echo "type-check: no Python files in scope — skipping."
    return 0
  fi
  echo "type-check: pyright on $# file(s)…"
  printf '  %s\n' "$@"
  pyright "$@"
}

changed_base() {
  if [ -n "${TYPECHECK_BASE:-}" ]; then echo "$TYPECHECK_BASE"; return; fi
  if [ -n "${GITHUB_BASE_REF:-}" ]; then echo "origin/${GITHUB_BASE_REF}"; return; fi
  git merge-base HEAD origin/main 2>/dev/null || git rev-parse HEAD~1
}

py_filter() {  # keep existing .py files under src/ from stdin
  grep -E '\.py$' | { grep -E '^src/agent_orchestrator/' || true; } | while read -r f; do
    [ -f "$f" ] && echo "$f"
  done
}

case "$MODE" in
  --all)
    pyright
    ;;
  --staged)
    mapfile -t files < <(git diff --cached --name-only --diff-filter=ACMR | py_filter)
    run_pyright "${files[@]}"
    ;;
  --changed|"")
    base="$(changed_base)"
    echo "type-check: comparing against base '$base'"
    # Two-dot (base vs working tree): includes committed AND uncommitted
    # changes, so the gate works both as a local pre-commit check and in CI
    # (where the working tree is the pushed/merge checkout).
    mapfile -t files < <(git diff --name-only --diff-filter=ACMR "$base" | py_filter)
    run_pyright "${files[@]}"
    ;;
  *)
    echo "usage: $0 [--changed|--all|--staged]" >&2
    exit 2
    ;;
esac
