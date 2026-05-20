#!/usr/bin/env bash
# Lint guard against hardcoded responsive breakpoints in frontend code.
#
# Detects:
#   1. Literal `window.innerWidth` / `window.innerHeight` reads in .ts/.tsx
#   2. @media (max-width|min-width: <Npx>) with literal pixel values in CSS
#
# Compares against a baseline of pre-existing violations
# (frontend/.responsive-baseline.txt). New violations cause exit 1.
#
# To rebuild baseline after intentional migration:
#   scripts/check_responsive.sh --update-baseline

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="${REPO_ROOT}/frontend/src"
BASELINE_FILE="${REPO_ROOT}/frontend/.responsive-baseline.txt"

if [ ! -d "${SRC_DIR}" ]; then
  echo "check_responsive: frontend/src not found, skipping"
  exit 0
fi

current_violations() {
  # window.innerWidth / window.innerHeight in TS/TSX (excluding the hook itself)
  grep -rnE 'window\.(innerWidth|innerHeight)' \
    --include='*.ts' --include='*.tsx' \
    --exclude='useBreakpoint.ts' --exclude='breakpoints.ts' \
    "${SRC_DIR}" 2>/dev/null \
    | sed -E "s|^${REPO_ROOT}/||"

  # @media queries with literal pixel values in CSS (excluding the canonical
  # variable references — those use var(--bp-*) and won't match this regex).
  grep -rnE '@media[^{]*\((min|max)-width:\s*[0-9]+px' \
    --include='*.css' \
    "${SRC_DIR}" 2>/dev/null \
    | sed -E "s|^${REPO_ROOT}/||"

  # Inline matchMedia calls with literal pixel values
  grep -rnE 'matchMedia\(\s*"[^"]*\b[0-9]+px' \
    --include='*.ts' --include='*.tsx' \
    --exclude='useBreakpoint.ts' --exclude='breakpoints.ts' \
    "${SRC_DIR}" 2>/dev/null \
    | sed -E "s|^${REPO_ROOT}/||"
}

CURRENT=$(current_violations | sort -u)

if [ "${1:-}" = "--update-baseline" ]; then
  echo "# Pre-existing responsive violations — do not add new entries here." > "${BASELINE_FILE}"
  echo "# Migrate listed sites to BP.* / useBreakpoint() then re-run with --update-baseline." >> "${BASELINE_FILE}"
  echo "${CURRENT}" >> "${BASELINE_FILE}"
  echo "check_responsive: baseline updated ($(echo "${CURRENT}" | grep -c . || echo 0) entries)"
  exit 0
fi

if [ ! -f "${BASELINE_FILE}" ]; then
  echo "check_responsive: baseline file missing at ${BASELINE_FILE}"
  echo "Run: scripts/check_responsive.sh --update-baseline"
  exit 1
fi

BASELINE=$(grep -vE '^\s*(#|$)' "${BASELINE_FILE}" | sort -u)

NEW=$(comm -23 <(echo "${CURRENT}") <(echo "${BASELINE}"))

if [ -n "${NEW}" ]; then
  echo "check_responsive: new responsive violations detected"
  echo ""
  echo "${NEW}"
  echo ""
  echo "Use BP.* from frontend/src/lib/breakpoints.ts or useBreakpoint() from"
  echo "frontend/src/hooks/useBreakpoint.ts instead of literal pixel values."
  echo ""
  echo "If the violation is intentional and acceptable, update the baseline:"
  echo "  scripts/check_responsive.sh --update-baseline"
  exit 1
fi

REMOVED=$(comm -13 <(echo "${CURRENT}") <(echo "${BASELINE}"))
if [ -n "${REMOVED}" ]; then
  echo "check_responsive: baseline entries no longer present (good!)"
  echo "Re-run with --update-baseline to clean up."
fi

echo "check_responsive: OK"
