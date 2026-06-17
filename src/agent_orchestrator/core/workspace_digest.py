"""Workspace digest — bounded, cross-turn memory of durable workspace facts.

The orchestrator sits between two failure modes when running an agent across
several iterations on the same goal:

* **Full transcript carried forward** — context grows unbounded every turn, which
  raises cost and degrades the model.
* **Nothing carried forward** (the current default: only the final chat messages
  survive) — the agent re-explores the workspace every turn: it re-reads the same
  files, re-discovers the project layout, and re-learns which commands fail. This
  is exactly the thrashing seen in real runs (three turns, each starting again
  with ``ls -la`` / ``find setupTests.js`` and writing the file in the wrong
  place).

The **workspace digest** is the middle ground. It captures only DURABLE facts —
project layout / known files, commands that worked, commands that FAILED and why
— in a small, capped, deduplicated structure that is rendered into the next
turn's prompt. It is carried only while iterations stay CONSECUTIVE on the same
goal (see :func:`is_followup_goal`); when the user pivots to a new goal it
resets. That mirrors the consecutive-failure circuit breaker, but for context:
keep the digest while hammering the same thing, drop it on a pivot so we don't
re-introduce task inertia.

Precedent — this recombines four established patterns: MemGPT tiered memory
(in-context vs out-of-context), LangChain summary-buffer (recent verbatim + older
condensed), Reflexion episodic memory (a compact lesson kept across consecutive
attempts at the same task), and LangGraph procedural memory (the known-good
commands). See ``docs/cache-strategy.md``.

HARNESS layer — this module MUST NOT import from ``dashboard/`` or
``integrations/``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Pure-exploration commands whose success carries no durable knowledge worth
# re-injecting next turn (their output is transient). Failures of these are
# equally uninformative, so they are skipped on both sides.
_EXPLORE_PREFIXES = (
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "find",
    "grep",
    "pwd",
    "echo",
    "which",
    "diff",
    "tree",
    "stat",
)

# Phrases that strongly signal the new prompt continues the previous goal (a
# follow-up / refinement / "still broken"), so the digest should be kept. Kept
# deliberately to continuation-of-work signals (multilingual EN/IT) — weak words
# like "now"/"ora" are excluded to avoid keeping a stale digest across a pivot.
_FOLLOWUP_PHRASES = (
    "still",
    "again",
    "doesn't work",
    "does not work",
    "not working",
    "same error",
    "same issue",
    "keep",
    "retry",
    "fix it",
    "fix this",
    "non va",
    "non funziona",
    "non converge",
    "stesso errore",
    "stesso problema",
    "rifai",
    "rifallo",
    "riprova",
    "ancora",
)

# Stopwords stripped before measuring topical overlap between two goals.
_STOPWORDS = {
    "the",
    "a",
    "an",
    "to",
    "of",
    "in",
    "on",
    "and",
    "or",
    "for",
    "with",
    "is",
    "are",
    "be",
    "this",
    "that",
    "it",
    "make",
    "do",
    "fix",
    "add",
    "please",
    "il",
    "lo",
    "la",
    "le",
    "i",
    "gli",
    "un",
    "una",
    "di",
    "da",
    "che",
    "e",
    "per",
    "con",
    "non",
    "fai",
    "fammi",
    "mi",
    "su",
    "del",
    "della",
}

_SESSION_PATH_RE = re.compile(r"(?:^|/)(?:jobs/job_|tmp/|uploads/)[a-f0-9\-]")


def _significant_words(text: str) -> set[str]:
    """Lowercased word set with stopwords and 1-char tokens removed."""
    words = re.findall(r"[a-zA-Z0-9_]+", text.lower())
    return {w for w in words if len(w) > 1 and w not in _STOPWORDS}


def is_followup_goal(previous_goal: str | None, new_goal: str | None) -> bool:
    """Decide whether ``new_goal`` continues ``previous_goal``.

    Returns True when the digest should be KEPT (consecutive iteration on the
    same goal), False when it should be reset (the user pivoted to a new goal).

    Heuristic, deterministic (no LLM call):
      1. an explicit follow-up phrase ("still", "non va", "again", …) → keep;
      2. otherwise, topical word overlap (Jaccard over significant words) ≥ 0.2
         → keep;
      3. otherwise → reset.
    """
    if not previous_goal or not previous_goal.strip():
        return False  # nothing meaningful to carry
    if not new_goal or not new_goal.strip():
        return True  # empty follow-up — keep prior context

    new_lower = new_goal.lower()
    for phrase in _FOLLOWUP_PHRASES:
        if phrase in new_lower:
            return True

    prev_words = _significant_words(previous_goal)
    new_words = _significant_words(new_goal)
    if not prev_words or not new_words:
        return False
    overlap = len(prev_words & new_words) / len(prev_words | new_words)
    return overlap >= 0.2


@dataclass
class _Entry:
    """A single durable fact with recency + hit bookkeeping for eviction."""

    text: str
    seq: int
    hits: int = 1
    extra: str = ""  # e.g. failure reason for a command


@dataclass
class WorkspaceDigest:
    """Bounded, deduplicated store of durable workspace facts for one goal.

    Categories:
      * ``layout``       — known file paths (read or written) so the next turn
                           doesn't re-discover them.
      * ``commands_ok``  — non-trivial commands that succeeded (e.g.
                           ``CI=true npm test``).
      * ``commands_bad`` — commands that failed, with the reason, so the agent
                           does not repeat them unchanged.

    The structure is capped at ``max_entries_per_category`` per category; when
    full, the least-recently-touched entry is evicted. :meth:`render` further
    caps the emitted text at ``max_render_chars``.
    """

    goal: str = ""
    max_entries_per_category: int = 12
    max_render_chars: int = 1600
    layout: dict[str, _Entry] = field(default_factory=dict)
    commands_ok: dict[str, _Entry] = field(default_factory=dict)
    commands_bad: dict[str, _Entry] = field(default_factory=dict)
    _seq: int = 0

    # ---- mutation -----------------------------------------------------------

    def _touch(self, bucket: dict[str, _Entry], key: str, text: str, extra: str = "") -> None:
        self._seq += 1
        existing = bucket.get(key)
        if existing is not None:
            existing.seq = self._seq
            existing.hits += 1
            if extra:
                existing.extra = extra
            return
        bucket[key] = _Entry(text=text, seq=self._seq, extra=extra)
        if len(bucket) > self.max_entries_per_category:
            # Evict the least-recently-touched entry.
            oldest = min(bucket, key=lambda k: bucket[k].seq)
            del bucket[oldest]

    def note_file(self, path: str) -> None:
        """Record a known file location (read or written)."""
        path = (path or "").strip()
        if not path or path == "?" or _SESSION_PATH_RE.search(path):
            return
        self._touch(self.layout, path, path)

    def note_command(self, command: str, ok: bool, reason: str = "") -> None:
        """Record a command outcome, skipping pure-exploration commands."""
        command = (command or "").strip()
        if not command:
            return
        first = command.split()[0] if command.split() else ""
        # Strip a leading env assignment (CI=true npm test → npm) for the check.
        if "=" in first and len(command.split()) > 1:
            first = command.split()[1]
        if first in _EXPLORE_PREFIXES:
            return
        if ok:
            # A command that now works is no longer a known-bad one.
            self.commands_bad.pop(command, None)
            self._touch(self.commands_ok, command, command)
        else:
            self._touch(self.commands_bad, command, command, extra=reason)

    def update_from_step_log(self, lines: list[str]) -> None:
        """Ingest a turn's ``step_log`` (see agent_runner) into the digest.

        Recognized line shapes:
          ``wrote <path>`` · ``read <path>`` · ``ran: <cmd>`` ·
          ``ran-failed[<reason>]: <cmd>``
        """
        for raw in lines or []:
            line = str(raw)
            if line.startswith("wrote "):
                self.note_file(line[6:])
            elif line.startswith("read "):
                self.note_file(line[5:])
            elif line.startswith("ran-failed["):
                m = re.match(r"ran-failed\[(.*?)\]:\s*(.*)", line)
                if m:
                    self.note_command(m.group(2), ok=False, reason=m.group(1))
            elif line.startswith("ran: "):
                self.note_command(line[5:], ok=True)

    def reset(self) -> None:
        """Drop all carried facts (used on a goal pivot)."""
        self.layout.clear()
        self.commands_ok.clear()
        self.commands_bad.clear()
        self._seq = 0
        self.goal = ""

    # ---- read ---------------------------------------------------------------

    def is_empty(self) -> bool:
        return not (self.layout or self.commands_ok or self.commands_bad)

    def _sorted(self, bucket: dict[str, _Entry]) -> list[_Entry]:
        return sorted(bucket.values(), key=lambda e: e.seq, reverse=True)

    def render(self) -> str:
        """Render a compact ``<workspace_digest>`` block, or "" if empty."""
        if self.is_empty():
            return ""
        out: list[str] = [
            "<workspace_digest>",
            "Durable facts carried from previous iterations on the SAME goal — "
            "use them instead of re-discovering; do NOT re-read these files or "
            "repeat the failed commands unchanged.",
        ]
        if self.layout:
            out.append("Known files (already located this session):")
            out.extend(f"- {e.text}" for e in self._sorted(self.layout))
        if self.commands_ok:
            out.append("Commands that worked:")
            out.extend(f"- {e.text}" for e in self._sorted(self.commands_ok))
        if self.commands_bad:
            out.append("Commands that FAILED (do not repeat unchanged):")
            for e in self._sorted(self.commands_bad):
                suffix = f" → {e.extra}" if e.extra else ""
                out.append(f"- {e.text}{suffix}")
        out.append("</workspace_digest>")
        block = "\n".join(out)
        if len(block) > self.max_render_chars:
            suffix = "\n…\n</workspace_digest>"
            keep = max(self.max_render_chars - len(suffix), 0)
            block = block[:keep].rstrip() + suffix
        return block

    # ---- serialization ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        def dump(bucket: dict[str, _Entry]) -> dict[str, Any]:
            return {
                k: {"text": e.text, "seq": e.seq, "hits": e.hits, "extra": e.extra}
                for k, e in bucket.items()
            }

        return {
            "goal": self.goal,
            "seq": self._seq,
            "max_entries_per_category": self.max_entries_per_category,
            "max_render_chars": self.max_render_chars,
            "layout": dump(self.layout),
            "commands_ok": dump(self.commands_ok),
            "commands_bad": dump(self.commands_bad),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceDigest:
        def load(raw: dict[str, Any]) -> dict[str, _Entry]:
            return {
                k: _Entry(
                    text=v["text"],
                    seq=v.get("seq", 0),
                    hits=v.get("hits", 1),
                    extra=v.get("extra", ""),
                )
                for k, v in (raw or {}).items()
            }

        d = cls(
            goal=data.get("goal", ""),
            max_entries_per_category=data.get("max_entries_per_category", 12),
            max_render_chars=data.get("max_render_chars", 1600),
            layout=load(data.get("layout", {})),
            commands_ok=load(data.get("commands_ok", {})),
            commands_bad=load(data.get("commands_bad", {})),
        )
        d._seq = data.get("seq", 0)
        return d


class WorkspaceDigestStore:
    """In-memory store of :class:`WorkspaceDigest` keyed by conversation id.

    A single instance is shared across agent runs (see
    ``agent_runner._digest_store``). It is intentionally simple — bounded by the
    number of live conversations, each digest bounded internally — so it adds no
    unbounded growth.
    """

    def __init__(self, max_conversations: int = 500) -> None:
        self._digests: dict[str, WorkspaceDigest] = {}
        self._max = max_conversations

    def get(self, conversation_id: str) -> WorkspaceDigest | None:
        return self._digests.get(conversation_id)

    def get_or_create(self, conversation_id: str) -> WorkspaceDigest:
        digest = self._digests.get(conversation_id)
        if digest is None:
            digest = WorkspaceDigest()
            self.put(conversation_id, digest)
        return digest

    def put(self, conversation_id: str, digest: WorkspaceDigest) -> None:
        self._digests[conversation_id] = digest
        if len(self._digests) > self._max:
            # Drop an arbitrary oldest-inserted conversation (FIFO-ish).
            oldest = next(iter(self._digests))
            if oldest != conversation_id:
                self._digests.pop(oldest, None)

    def reset(self, conversation_id: str) -> None:
        self._digests.pop(conversation_id, None)

    def clear(self) -> None:
        self._digests.clear()
