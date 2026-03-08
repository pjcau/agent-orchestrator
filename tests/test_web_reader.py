"""Tests for the web reader skill — HTML stripping, meta extraction, URL validation."""

import pytest

from agent_orchestrator.skills.web_reader import (
    WebReaderSkill,
    _extract_meta,
    _strip_html,
)


class TestStripHtml:
    def test_removes_tags(self):
        assert _strip_html("<p>Hello</p>") == "Hello"

    def test_removes_script_blocks(self):
        html = "<script>var x=1;</script><p>Content</p>"
        assert "var x" not in _strip_html(html)
        assert "Content" in _strip_html(html)

    def test_removes_style_blocks(self):
        html = "<style>.foo{color:red}</style><p>Text</p>"
        assert "color" not in _strip_html(html)
        assert "Text" in _strip_html(html)

    def test_decodes_entities(self):
        assert "A & B" in _strip_html("<p>A &amp; B</p>")

    def test_collapses_whitespace(self):
        result = _strip_html("<p>  Hello   World  </p>")
        assert "  " not in result
        assert "Hello World" in result

    def test_empty_string(self):
        assert _strip_html("") == ""

    def test_plain_text_passthrough(self):
        assert _strip_html("No HTML here") == "No HTML here"


class TestExtractMeta:
    def test_extracts_title(self):
        html = "<html><head><title>My Page</title></head></html>"
        meta = _extract_meta(html)
        assert meta["title"] == "My Page"

    def test_extracts_description(self):
        html = '<html><head><meta name="description" content="A great page"></head></html>'
        meta = _extract_meta(html)
        assert meta["description"] == "A great page"

    def test_extracts_og_description(self):
        html = '<html><head><meta property="og:description" content="OG desc"></head></html>'
        meta = _extract_meta(html)
        assert meta["description"] == "OG desc"

    def test_missing_title(self):
        meta = _extract_meta("<html><head></head></html>")
        assert meta["title"] == ""

    def test_missing_description(self):
        meta = _extract_meta("<html><head><title>T</title></head></html>")
        assert meta["description"] == ""

    def test_decodes_html_entities_in_title(self):
        html = "<html><head><title>A &amp; B</title></head></html>"
        meta = _extract_meta(html)
        assert meta["title"] == "A & B"


class TestWebReaderSkill:
    def test_skill_properties(self):
        skill = WebReaderSkill()
        assert skill.name == "web_read"
        assert "URL" in skill.description
        assert "url" in skill.parameters["properties"]

    @pytest.mark.asyncio
    async def test_invalid_scheme_rejected(self):
        skill = WebReaderSkill()
        result = await skill.execute({"url": "ftp://example.com/file"})
        assert not result.success
        assert "Invalid URL scheme" in result.error

    @pytest.mark.asyncio
    async def test_missing_aiohttp_error(self, monkeypatch):
        """If aiohttp is not installed, should return clear error."""
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def mock_import(name, *args, **kwargs):
            if name == "aiohttp":
                raise ImportError("No module named 'aiohttp'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", mock_import)
        skill = WebReaderSkill()
        result = await skill.execute({"url": "https://example.com"})
        assert not result.success
        assert "aiohttp" in result.error
