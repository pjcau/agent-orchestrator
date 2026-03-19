#!/usr/bin/env bash
# DeerFlow Roadmap — Sequential Feature Runner
# Runs /feature on each spec file, pings the user between each.
#
# Usage: bash features/deerflow/run_all.sh
#
# Each feature runs as a separate Claude Code session.
# After each completes, a macOS notification is sent and
# the script waits for Enter before starting the next one.

set -euo pipefail

FEATURES_DIR="$(cd "$(dirname "$0")" && pwd)"
PING_SOUND="/System/Library/Sounds/Glass.aiff"

features=(
  "01-loop-detection.md"
  "02-dangling-tool-recovery.md"
  "03-tool-description-param.md"
  "04-progressive-skill-loading.md"
  "05-context-summarization.md"
  "06-embedded-client.md"
  "07-yaml-config.md"
  "08-clarification-system.md"
  "09-sandbox-execution.md"
  "10-file-upload-conversion.md"
  "11-slack-integration.md"
  "12-telegram-integration.md"
  "13-harness-app-boundary.md"
  "14-memory-upload-filtering.md"
)

total=${#features[@]}
completed=0

echo "============================================"
echo "  DeerFlow Roadmap — Feature Runner"
echo "  $total features to implement"
echo "============================================"
echo ""

for feature_file in "${features[@]}"; do
  completed=$((completed + 1))
  feature_name="${feature_file%.md}"
  feature_path="${FEATURES_DIR}/${feature_file}"

  echo "--------------------------------------------"
  echo "  [$completed/$total] Starting: $feature_name"
  echo "--------------------------------------------"

  # Read the feature spec
  spec=$(cat "$feature_path")

  # Run claude with /feature and the spec content
  claude --print "/feature $spec" 2>&1 | tee "/tmp/deerflow-${feature_name}.log"

  exit_code=${PIPESTATUS[0]}

  if [ $exit_code -eq 0 ]; then
    echo ""
    echo "  [$completed/$total] DONE: $feature_name"

    # macOS notification ping
    osascript -e "display notification \"Feature $completed/$total completed: $feature_name\" with title \"DeerFlow Roadmap\" sound name \"Glass\"" 2>/dev/null || true
  else
    echo ""
    echo "  [$completed/$total] FAILED: $feature_name (exit code: $exit_code)"
    echo "  Log: /tmp/deerflow-${feature_name}.log"

    osascript -e "display notification \"Feature FAILED: $feature_name\" with title \"DeerFlow Roadmap\" sound name \"Basso\"" 2>/dev/null || true
  fi

  # Wait for user before next feature (unless it's the last one)
  if [ $completed -lt $total ]; then
    echo ""
    echo "  Press Enter to start the next feature, or Ctrl+C to stop..."
    read -r
  fi
done

echo ""
echo "============================================"
echo "  All $total features completed!"
echo "============================================"

# Final summary ping
osascript -e "display notification \"All $total DeerFlow features completed!\" with title \"DeerFlow Roadmap\" sound name \"Hero\"" 2>/dev/null || true
