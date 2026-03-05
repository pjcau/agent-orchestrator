#!/bin/bash
# Post-hook: after editing source files, remind to run tests
# Customize SOURCE_DIRS for your project
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

SOURCE_DIRS="src/|lib/|scripts/|software/"

if echo "$FILE_PATH" | grep -qE "$SOURCE_DIRS"; then
  FILENAME=$(basename "$FILE_PATH")
  echo "{\"systemMessage\": \"You edited ${FILENAME}. Remember to run tests to verify your changes.\"}"
fi

exit 0
