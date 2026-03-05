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

  # Block HTTPS downloads — use package managers or local sources only
  if echo "$COMMAND" | grep -qE '(curl\s|wget\s|pip install\s+https://|pip install\s+http://|pip install\s+git\+|npx\s+https://|fetch\s+https://)'; then
    echo '{"decision": "block", "reason": "HTTPS download blocked. Use package managers (pip, npm, apt) or local sources only. Never download from URLs."}'
    exit 0
  fi
fi

# Block WebFetch tool
if [ "$TOOL" = "WebFetch" ]; then
  URL=$(echo "$INPUT" | jq -r '.tool_input.url // empty')
  # Allow API docs and known safe domains for research
  if echo "$URL" | grep -qE '(github\.com|docs\.|pypi\.org|openrouter\.ai|ollama\.com)'; then
    exit 0
  fi
  # Block download-like URLs (binaries, archives, scripts)
  if echo "$URL" | grep -qE '\.(sh|tar|gz|zip|whl|exe|bin|deb|rpm|pkg)(\?|$)'; then
    echo '{"decision": "block", "reason": "File download via WebFetch blocked. Use package managers or local sources only."}'
    exit 0
  fi
fi

exit 0
