#!/usr/bin/env bash
#
# try-ago.sh — smoke-test the `ago` CLI end-to-end against a local dashboard.
#
# What it does, in order:
#   1. Verifies `ago` is on $PATH (or hints at the Release download).
#   2. Prepares an isolated config in /tmp/try-ago/ so your real
#      ~/.config/ago/config.toml is untouched.
#   3. Starts the dashboard via `docker compose up -d dashboard` if it
#      is not already reachable at http://localhost:5005.
#   4. Loads DASHBOARD_API_KEYS from .env (or generates one) and points
#      the CLI at the local dashboard.
#   5. Runs the smoke battery:
#        ago --version
#        ago completions zsh   (first 5 lines)
#        ago config show
#        ago login --key-env  (validates the key)
#        ago whoami
#        ago jobs list
#
# Optional flags:
#   --clean      Tear everything down (config + container) and exit.
#   --no-server  Skip docker, only run the no-infra smoke tests.
#
# Re-run safely as many times as you want.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRY_DIR="/tmp/try-ago"
CONFIG_PATH="${TRY_DIR}/config.toml"
# The docker-compose dashboard runs uvicorn with the bundled self-signed
# cert at certs/cert.pem (CN=localhost). The CLI normally validates TLS
# against vendored webpki-roots and would reject it, so we set
# AGO_INSECURE=1 for the duration of this script. This is a dev-only
# escape hatch — never persisted, never set in CI, and the CLI prints a
# stderr warning the first time the flag is used.
DASHBOARD_URL="https://localhost:5005"
ENV_FILE="${REPO_ROOT}/.env"
export AGO_INSECURE=1

mode="full"
case "${1:-}" in
  --clean)     mode="clean" ;;
  --no-server) mode="no-server" ;;
  --help|-h)
    sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'
    exit 0 ;;
  "") ;;
  *)
    echo "unknown flag: $1 (try --help)" >&2
    exit 64 ;;
esac

# ─── helpers ────────────────────────────────────────────────────────
log()  { printf '\033[36m▸\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

ago_run() { ago --config "$CONFIG_PATH" "$@"; }

# ─── clean mode ─────────────────────────────────────────────────────
if [[ "$mode" == "clean" ]]; then
  log "Tearing down try-ago state..."
  rm -rf "$TRY_DIR"
  (cd "$REPO_ROOT" && docker compose stop dashboard 2>/dev/null || true)
  ok "Cleaned up. (Container left in place — run 'docker compose down' to remove fully.)"
  exit 0
fi

mkdir -p "$TRY_DIR"

# ─── 1. ago on PATH ─────────────────────────────────────────────────
if ! command -v ago >/dev/null 2>&1; then
  cat >&2 <<EOF
✗ \`ago\` is not on \$PATH.

Install one of:
  - Prebuilt: download from
      https://github.com/pjcau/agent-orchestrator/releases/tag/ago-v0.1.0
    extract, copy to /usr/local/bin/
  - From source:
      cd $REPO_ROOT/cli && cargo install --path . --locked
EOF
  exit 1
fi
ok "ago on PATH: $(command -v ago) ($(ago --version))"

# ─── 2. no-infra smoke tests ────────────────────────────────────────
log "Smoke test: completions"
ago completions zsh | head -1 | grep -q '#compdef ago' \
  && ok "completions zsh emits #compdef ago" \
  || die "completions output unexpected"

log "Smoke test: config (isolated to $CONFIG_PATH)"
ago_run config set server "$DASHBOARD_URL"
ago_run config show

if [[ "$mode" == "no-server" ]]; then
  ok "no-server mode — stopping here. Run without --no-server for the auth/whoami round-trip."
  exit 0
fi

# ─── 3. ensure dashboard is up ──────────────────────────────────────
ping_dashboard() {
  # Use python so we do not need curl/wget with --insecure flags — the
  # bundled CN=localhost cert is self-signed by design.
  python3 - "$DASHBOARD_URL/health" <<'PY' 2>/dev/null
import ssl, sys, urllib.request
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
try:
    urllib.request.urlopen(sys.argv[1], context=ctx, timeout=2).read()
    sys.exit(0)
except Exception:
    sys.exit(1)
PY
}

if ping_dashboard; then
  ok "dashboard already reachable at $DASHBOARD_URL"
else
  log "dashboard not reachable — bringing it up via docker compose..."
  (cd "$REPO_ROOT" && docker compose up -d dashboard)
  log "waiting for /health to respond (up to 60s)..."
  for _ in $(seq 1 60); do
    if ping_dashboard; then ok "dashboard up"; break; fi
    sleep 1
  done
  ping_dashboard || die "dashboard never came up — check 'docker compose logs dashboard'"
fi

# ─── 4. obtain an API key for the CLI ───────────────────────────────
if [[ -f "$ENV_FILE" ]]; then
  api_key=$(grep -E '^DASHBOARD_API_KEYS=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"' | cut -d, -f1 || true)
fi
if [[ -z "${api_key:-}" ]]; then
  api_key="${AGO_TEST_KEY:-}"
fi
if [[ -z "$api_key" ]]; then
  warn "No DASHBOARD_API_KEYS in $ENV_FILE and no AGO_TEST_KEY env var."
  warn "Using a stub token — works only when the dashboard runs ALLOW_DEV_MODE=true."
  api_key="dev-mode-stub-token"
fi

# ─── 5. login + whoami + jobs ───────────────────────────────────────
log "ago login --key-env (validates the key against /api/cli/v1/whoami)"
AGO_TEST_KEY="$api_key" ago_run login --key-env AGO_TEST_KEY

log "ago whoami"
ago_run whoami

log "ago jobs list (top 5)"
ago_run jobs list --limit 5 || warn "jobs list failed — possibly no sessions yet"

ok "Smoke battery passed. Try-ago config lives at $CONFIG_PATH."
echo
echo "To run a real agent task (requires a configured provider):"
echo "  ago --config $CONFIG_PATH run --agent backend --model claude-sonnet-4-6 'say hello'"
echo
echo "To tear down:  $0 --clean"
