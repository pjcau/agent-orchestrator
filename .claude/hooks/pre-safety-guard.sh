#!/bin/bash
# Pre-hook: safety guard for dangerous operations
INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

if [ "$TOOL" = "Bash" ]; then
  # Block dangerous commands
  if echo "$COMMAND" | grep -qE '(rm -rf /|dd if=|mkfs\.|format [A-Z]:|shutdown|reboot)'; then
    echo '{"decision": "block", "reason": "Dangerous system command blocked by safety guard"}'
    exit 0
  fi

  # Block secret exposure (cat .env but not cat <<EOF)
  if echo "$COMMAND" | grep -qE '(cat\s+\S*\.env\b|echo.*API_KEY|echo.*SECRET|echo.*PASSWORD)'; then
    echo '{"decision": "block", "reason": "Potential secret exposure blocked by safety guard"}'
    exit 0
  fi
fi

exit 0
