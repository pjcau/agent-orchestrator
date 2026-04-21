"""Unit tests for gateway_api pure helpers.

These exercise small primitive functions in isolation — fast, no
network, no ASGI — to guard against regressions independent of the
endpoint wiring covered by test_gateway_api_security.py.
"""

import pytest

from agent_orchestrator.dashboard.gateway_api import _sanitize_log


class TestSanitizeLog:
    """``_sanitize_log`` is the log-injection sanitizer used before every
    logger call that receives a user-controlled value. These tests pin
    the behavior CodeQL's py/log-injection rule relies on."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("plain text", "plain text"),
            ("with\nnewline", "with\\nnewline"),
            ("carriage\rreturn", "carriage\\rreturn"),
            ("tab\there", "tab\\there"),
            ("mix\n\r\tall", "mix\\n\\r\\tall"),
            ("", ""),
            ("no special chars 123!@#", "no special chars 123!@#"),
        ],
    )
    def test_replaces_control_chars(self, raw: str, expected: str) -> None:
        assert _sanitize_log(raw) == expected

    def test_log_forgery_payload_neutralized(self) -> None:
        """A classic log-forgery payload ("INFO fake admin login") must not
        produce a second log line after sanitization."""
        attack = "user\nINFO admin login from 10.0.0.1"
        sanitized = _sanitize_log(attack)
        assert "\n" not in sanitized
        assert "\\n" in sanitized

    def test_crlf_injection_neutralized(self) -> None:
        attack = "user\r\nFAKE: spoofed"
        sanitized = _sanitize_log(attack)
        assert "\r" not in sanitized
        assert "\n" not in sanitized

    def test_returns_same_type(self) -> None:
        result = _sanitize_log("x")
        assert isinstance(result, str)

    def test_idempotent_on_already_sanitized(self) -> None:
        once = _sanitize_log("a\nb")
        twice = _sanitize_log(once)
        assert twice == "a\\nb"
