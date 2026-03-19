"""Test configuration — ensure imports resolve to this worktree's source."""

import sys
from pathlib import Path

# Insert this worktree's src/ at the front of sys.path so that
# agent_orchestrator imports come from the correct location.
_src = str(Path(__file__).resolve().parent.parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)
