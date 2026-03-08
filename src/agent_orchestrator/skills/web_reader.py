"""Web reader skill — fetch and extract text content from URLs."""

from __future__ import annotations

import html
import logging
import re
from urllib.parse import urlparse

from ..core.skill import Skill, SkillResult

logger = logging.getLogger(__name__)


def _strip_html(raw: str) -> str:
    """Remove HTML tags and decode entities, returning plain text."""
    # Remove script and style blocks
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw, flags=re.DOTALL | re.IGNORECASE)
    # Remove tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode entities
    text = html.unescape(text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_meta(raw: str) -> dict[str, str]:
    """Extract title and meta description from HTML."""
    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw, re.DOTALL | re.IGNORECASE)
    desc_match = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        raw,
        re.IGNORECASE,
    )
    og_desc = re.search(
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
        raw,
        re.IGNORECASE,
    )
    return {
        "title": html.unescape(title_match.group(1).strip()) if title_match else "",
        "description": html.unescape(
            (desc_match or og_desc).group(1).strip()
        ) if (desc_match or og_desc) else "",
    }


class WebReaderSkill(Skill):
    """Fetch a URL and extract its text content."""

    def __init__(self, max_chars: int = 15_000, timeout: int = 15):
        self._max_chars = max_chars
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "web_read"

    @property
    def description(self) -> str:
        return "Fetch a URL and extract its text content (HTML stripped)"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"},
                "max_chars": {
                    "type": "integer",
                    "description": "Max characters to return (default 15000)",
                },
            },
            "required": ["url"],
        }

    async def execute(self, params: dict) -> SkillResult:
        url = params["url"]
        max_chars = params.get("max_chars", self._max_chars)

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return SkillResult(success=False, output=None, error=f"Invalid URL scheme: {parsed.scheme}")

        try:
            import aiohttp
        except ImportError:
            return SkillResult(
                success=False, output=None, error="aiohttp is required: pip install aiohttp"
            )

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=self._timeout),
                    headers={"User-Agent": "AgentOrchestrator-ResearchScout/1.0"},
                ) as resp:
                    if resp.status != 200:
                        return SkillResult(
                            success=False,
                            output=None,
                            error=f"HTTP {resp.status} fetching {url}",
                        )
                    raw = await resp.text(errors="replace")
        except Exception as exc:
            return SkillResult(success=False, output=None, error=f"Fetch error: {exc}")

        meta = _extract_meta(raw)
        text = _strip_html(raw)
        if len(text) > max_chars:
            text = text[:max_chars] + "... [truncated]"

        result = {
            "url": url,
            "domain": parsed.netloc,
            "title": meta["title"],
            "description": meta["description"],
            "text": text,
            "char_count": len(text),
        }
        return SkillResult(success=True, output=result)
