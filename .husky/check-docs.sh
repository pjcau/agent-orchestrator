#!/bin/sh
# Pre-commit doc check: verify that CLAUDE.md and docs/ reflect the actual codebase.
# Fails if documentation is stale — forces the developer to update docs with code changes.

set -e

ERRORS=""

# 1. Check that every Python module directory under src/ is mentioned in CLAUDE.md
for dir in src/agent_orchestrator/*/; do
  dirname=$(basename "$dir")
  if [ "$dirname" = "__pycache__" ]; then continue; fi
  if ! grep -q "$dirname" CLAUDE.md 2>/dev/null; then
    ERRORS="${ERRORS}\n  - Module '$dirname' exists in src/ but is not documented in CLAUDE.md"
  fi
done

# 2. Check that every docker-compose service (under 'services:' block only) is mentioned in CLAUDE.md
in_services=0
while IFS= read -r line; do
  case "$line" in
    "services:"*) in_services=1; continue ;;
    "volumes:"*|"networks:"*) in_services=0; continue ;;
  esac
  if [ "$in_services" -eq 1 ]; then
    svc=$(echo "$line" | grep -oE '^  [a-z][a-z0-9_-]+:' | sed 's/://;s/^[[:space:]]*//')
    if [ -n "$svc" ] && ! grep -qi "$svc" CLAUDE.md 2>/dev/null; then
      ERRORS="${ERRORS}\n  - Docker service '$svc' in docker-compose.yml but not documented in CLAUDE.md"
    fi
  fi
done < docker-compose.yml

# 3. Check that every hook script referenced in settings.json exists
if [ -f .claude/settings.json ]; then
  for hook_path in $(grep -oE '\./\.claude/hooks/[a-zA-Z0-9_-]+\.sh' .claude/settings.json); do
    if [ ! -f "$hook_path" ]; then
      ERRORS="${ERRORS}\n  - Hook '$hook_path' referenced in settings.json but file does not exist"
    fi
  done
fi

# 4. Check that CLAUDE.md Project Structure tree mentions key directories
for key_dir in core providers skills dashboard; do
  if [ -d "src/agent_orchestrator/$key_dir" ]; then
    if ! grep -q "$key_dir" CLAUDE.md 2>/dev/null; then
      ERRORS="${ERRORS}\n  - Directory 'src/agent_orchestrator/$key_dir' not in CLAUDE.md Project Structure"
    fi
  fi
done

# 5. Check that test files exist for every module directory (except __pycache__, providers, skills)
for dir in src/agent_orchestrator/*/; do
  dirname=$(basename "$dir")
  case "$dirname" in __pycache__|providers|skills) continue;; esac
  test_file="tests/test_${dirname}.py"
  if [ ! -f "$test_file" ]; then
    # Also check without underscore
    test_file2="tests/test_$(echo "$dirname" | tr '-' '_').py"
    if [ ! -f "$test_file2" ]; then
      ERRORS="${ERRORS}\n  - No test file found for module '$dirname' (expected $test_file)"
    fi
  fi
done

if [ -n "$ERRORS" ]; then
  echo "DOCUMENTATION CHECK FAILED — docs are out of sync with code:"
  printf "$ERRORS\n"
  echo ""
  echo "Update CLAUDE.md and/or docs/ to reflect the current codebase."
  exit 1
fi

echo "Documentation check passed"
exit 0
