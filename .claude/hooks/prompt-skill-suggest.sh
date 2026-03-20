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
if echo "$PROMPT" | grep -qE '(deploy|release|production)'; then
  SUGGESTIONS="$SUGGESTIONS /deploy"
fi

# Ship (test + doc + commit + push)
if echo "$PROMPT" | grep -qE '(ship|commit.*push|push.*commit|test.*commit|manda|pubblica|pusha)'; then
  SUGGESTIONS="$SUGGESTIONS /ship"
fi

# Review related
if echo "$PROMPT" | grep -qE '(review|check|audit|security)'; then
  SUGGESTIONS="$SUGGESTIONS /code-review"
fi

# Scout related
if echo "$PROMPT" | grep -qE '(scout|search|discover|pattern|github)'; then
  SUGGESTIONS="$SUGGESTIONS /scout"
fi

# Research scout (bookmark analysis)
if echo "$PROMPT" | grep -qE '(research|bookmark|analiz|url|migliora)'; then
  SUGGESTIONS="$SUGGESTIONS /research-scout"
fi

# Fetch bookmarks from Twitter/X
if echo "$PROMPT" | grep -qE '(fetch.*bookmark|import.*bookmark|twitter.*bookmark|scarica.*bookmark)'; then
  SUGGESTIONS="$SUGGESTIONS /fetch-bookmarks"
fi

# Website/docs related
if echo "$PROMPT" | grep -qE '(docs|website|documentation|readme)'; then
  SUGGESTIONS="$SUGGESTIONS /website-dev"
fi

# Doc-sync related
if echo "$PROMPT" | grep -qE '(doc-sync|sync.*doc|documentation.*sync|update.*doc|doc.*drift)'; then
  SUGGESTIONS="$SUGGESTIONS /doc-sync"
fi

# Fix (bug fix with mandatory tests)
if echo "$PROMPT" | grep -qE '(fix|bug|broken|error|crash|fail|fixa|correggi|rotto|errore)'; then
  SUGGESTIONS="$SUGGESTIONS /fix"
fi

# Analysis (deep repo analysis)
if echo "$PROMPT" | grep -qE '(analy[sz]|deep.?dive|repo.*explor|study.*repo|examine.*repo)'; then
  SUGGESTIONS="$SUGGESTIONS /analysis"
fi

if [ -n "$SUGGESTIONS" ]; then
  echo "{\"systemMessage\": \"Relevant skills:$SUGGESTIONS\"}"
fi

exit 0
