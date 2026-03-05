#!/bin/bash
# UserPromptSubmit hook: suggests relevant skills based on keyword matching
INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty' | tr '[:upper:]' '[:lower:]')

SUGGESTIONS=""

# Docker/container related
if echo "$PROMPT" | grep -qE '(docker|container|build|image|orbstack)'; then
  SUGGESTIONS="$SUGGESTIONS /docker-build"
fi

# Testing related
if echo "$PROMPT" | grep -qE '(test|pytest|coverage|verify)'; then
  SUGGESTIONS="$SUGGESTIONS /test-runner"
fi

# Lint/format related
if echo "$PROMPT" | grep -qE '(lint|format|style|ruff|quality)'; then
  SUGGESTIONS="$SUGGESTIONS /lint-check"
fi

# Deploy related
if echo "$PROMPT" | grep -qE '(deploy|release|ship|production)'; then
  SUGGESTIONS="$SUGGESTIONS /deploy"
fi

# Review related
if echo "$PROMPT" | grep -qE '(review|check|audit|security)'; then
  SUGGESTIONS="$SUGGESTIONS /code-review"
fi

# Scout related
if echo "$PROMPT" | grep -qE '(scout|search|discover|pattern|github)'; then
  SUGGESTIONS="$SUGGESTIONS /scout"
fi

# Website/docs related
if echo "$PROMPT" | grep -qE '(docs|website|documentation|readme)'; then
  SUGGESTIONS="$SUGGESTIONS /website-dev"
fi

if [ -n "$SUGGESTIONS" ]; then
  echo "{\"systemMessage\": \"Relevant skills:$SUGGESTIONS\"}"
fi

exit 0
