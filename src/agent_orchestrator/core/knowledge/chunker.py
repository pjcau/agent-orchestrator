"""Document chunkers — split a document into searchable units.

SRP: each chunker only knows one heuristic. New strategies (semantic,
recursive, layout-aware) plug in by subclassing ``Chunker``.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    """A piece of a source document ready for embedding."""

    text: str
    # Position within the source document (e.g. char offset, header path)
    location: str = ""


class Chunker(ABC):
    """Abstract chunker. SRP: split text into ``Chunk`` objects."""

    @abstractmethod
    def chunk(self, text: str) -> list[Chunk]: ...


# ---------------------------------------------------------------------------
# TextChunker — fixed window with overlap
# ---------------------------------------------------------------------------


class TextChunker(Chunker):
    """Plain fixed-window chunker with character-level overlap.

    Suitable for plain text and as a fallback for unknown formats.
    Defaults: 1000-char window, 100-char overlap. Empty input → no chunks.
    """

    def __init__(self, window: int = 1000, overlap: int = 100) -> None:
        if window <= 0:
            raise ValueError("window must be positive")
        if overlap < 0 or overlap >= window:
            raise ValueError("overlap must be >= 0 and < window")
        self._window = window
        self._overlap = overlap

    def chunk(self, text: str) -> list[Chunk]:
        text = (text or "").strip()
        if not text:
            return []
        chunks: list[Chunk] = []
        step = self._window - self._overlap
        i = 0
        while i < len(text):
            piece = text[i : i + self._window]
            chunks.append(Chunk(text=piece.strip(), location=f"char:{i}"))
            i += step
        return chunks


# ---------------------------------------------------------------------------
# MarkdownChunker — split on headers, then fixed-window inside large sections
# ---------------------------------------------------------------------------


_MD_HEADER = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


class MarkdownChunker(Chunker):
    """Section-aware chunker for Markdown documents.

    Splits on ``#``..``######`` headers, keeping the header path
    (e.g. ``H1 / H2 / H3``) as ``location`` for each chunk. Sections larger
    than ``max_section_chars`` are further split with a ``TextChunker``.
    """

    def __init__(self, max_section_chars: int = 2000, overlap: int = 200) -> None:
        if max_section_chars <= 0:
            raise ValueError("max_section_chars must be positive")
        # Clamp overlap so it never equals/exceeds the window the fallback
        # TextChunker will receive — avoids ValueError when callers shrink
        # max_section_chars without also shrinking overlap.
        safe_overlap = min(overlap, max(0, max_section_chars - 1))
        self._max = max_section_chars
        self._fallback = TextChunker(window=max_section_chars, overlap=safe_overlap)

    def chunk(self, text: str) -> list[Chunk]:
        text = (text or "").strip()
        if not text:
            return []

        # Find all header matches with their positions.
        matches = list(_MD_HEADER.finditer(text))
        if not matches:
            # No headers — degrade to plain fixed-window chunking.
            return self._fallback.chunk(text)

        # Build sections: from each header start to the next header start.
        sections: list[tuple[list[str], int, int]] = []  # (header_path, start, end)
        path_stack: list[tuple[int, str]] = []  # (depth, title)
        for idx, m in enumerate(matches):
            depth = len(m.group(1))
            title = m.group(2).strip()
            # Pop stack to current depth - 1
            while path_stack and path_stack[-1][0] >= depth:
                path_stack.pop()
            path_stack.append((depth, title))
            start = m.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            sections.append(
                (
                    [t for _, t in path_stack],
                    start,
                    end,
                )
            )

        chunks: list[Chunk] = []
        for path, start, end in sections:
            body = text[start:end].strip()
            if not body:
                continue
            location = " / ".join(path)
            if len(body) <= self._max:
                chunks.append(Chunk(text=body, location=location))
            else:
                # Large section — sub-chunk and tag with the header path.
                for sub in self._fallback.chunk(body):
                    chunks.append(Chunk(text=sub.text, location=f"{location} ({sub.location})"))
        return chunks
