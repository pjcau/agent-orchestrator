"""Pluggable pre/post LLM-call guardrail layer (P3).

SOLID rationale:
  S — Single Responsibility: each Guardrail class owns exactly one concern (PII, secrets, …).
  O — Open/Closed: new guardrails are added by subclassing Guardrail; no existing code changes.
  L — Liskov Substitution: any Guardrail subclass is a valid plug-in to GuardrailManager.
  I — Interface Segregation: check_input and check_output have independent default no-ops
      so implementors override only what they need.
  D — Dependency Inversion: GuardrailManager depends on the Guardrail ABC, never concrete types;
      concrete guardrails are injected at construction time or loaded from YAML config.
"""

from __future__ import annotations

import json
import re
from abc import ABC
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from .provider import Message

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GuardrailResult:
    """Immutable result from a single guardrail check."""

    passed: bool
    reason: str = ""
    action: Literal["allow", "block", "redact"] = "allow"
    redacted_text: str | None = None


# Convenience singletons
_ALLOW = GuardrailResult(passed=True, action="allow")


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class GuardrailBlocked(RuntimeError):
    """Raised by Agent.execute() when a guardrail blocks execution.

    Attributes:
        guardrail_name: Name of the guardrail that triggered the block.
        reason: Human-readable reason for the block.
        side: "input" or "output" indicating which check fired.
    """

    def __init__(self, guardrail_name: str, reason: str, side: str = "input") -> None:
        super().__init__(f"Guardrail '{guardrail_name}' blocked ({side}): {reason}")
        self.guardrail_name = guardrail_name
        self.reason = reason
        self.side = side


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class Guardrail(ABC):
    """Base class for all guardrails.

    Subclasses override check_input, check_output, or both.
    The default implementations return an "allow" result so that guardrails
    can be input-only, output-only, or bidirectional without boilerplate.
    """

    @property
    def name(self) -> str:
        """Unique guardrail name used in logs, metrics, and events."""
        return self.__class__.__name__

    async def check_input(self, messages: list[Message]) -> GuardrailResult:
        """Check messages before they are sent to the LLM.

        Returns a GuardrailResult. Default: allow.
        """
        return _ALLOW

    async def check_output(self, response: str) -> GuardrailResult:
        """Check the assistant's response after it is received.

        Returns a GuardrailResult. Default: allow.
        """
        return _ALLOW


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class GuardrailManager:
    """Manages an ordered collection of Guardrail instances.

    Execution rules:
      - Returns the FIRST blocking result (action == "block") immediately.
      - If no block, collects all redact results and applies them left-to-right.
      - Returns "allow" if nothing fired.
    """

    def __init__(self, guardrails: list[Guardrail] | None = None) -> None:
        self._guardrails: list[Guardrail] = guardrails or []

    def register(self, guardrail: Guardrail) -> None:
        """Append a guardrail to the ordered list."""
        self._guardrails.append(guardrail)

    async def run_input(self, messages: list[Message]) -> GuardrailResult:
        """Run all input guardrails; short-circuit on the first block."""
        accumulated_text: str | None = None

        for g in self._guardrails:
            result = await g.check_input(messages)
            if result.action == "block":
                return GuardrailResult(
                    passed=False,
                    reason=result.reason,
                    action="block",
                    redacted_text=None,
                )
            if result.action == "redact" and result.redacted_text is not None:
                accumulated_text = result.redacted_text

        if accumulated_text is not None:
            return GuardrailResult(
                passed=True,
                reason="redacted",
                action="redact",
                redacted_text=accumulated_text,
            )
        return _ALLOW

    async def run_output(self, response: str) -> GuardrailResult:
        """Run all output guardrails; short-circuit on the first block."""
        accumulated_text: str | None = None

        for g in self._guardrails:
            result = await g.check_output(response)
            if result.action == "block":
                return GuardrailResult(
                    passed=False,
                    reason=result.reason,
                    action="block",
                    redacted_text=None,
                )
            if result.action == "redact" and result.redacted_text is not None:
                accumulated_text = result.redacted_text

        if accumulated_text is not None:
            return GuardrailResult(
                passed=True,
                reason="redacted",
                action="redact",
                redacted_text=accumulated_text,
            )
        return _ALLOW


# ---------------------------------------------------------------------------
# Built-in guardrails
# ---------------------------------------------------------------------------

# --- PII Scanner ---

_PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    (
        "phone",
        re.compile(
            r"""
            (?:(?:\+1[-.\s]?)?          # optional US country code
            \(?[2-9]\d{2}\)?            # area code
            [-.\s]?                      # separator
            [2-9]\d{2}                  # exchange
            [-.\s]?                      # separator
            \d{4})                      # subscriber
            """,
            re.VERBOSE,
        ),
    ),
    # US SSN: 3-2-4 digit pattern
    ("ssn", re.compile(r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")),
    # IBAN: 2 letters + 2 digits + up to 30 alphanumerics
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{1,30}\b")),
    # Credit cards: 13-19 digits with optional spaces/dashes
    (
        "credit_card",
        re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    ),
]

_PII_REPLACEMENT = "[REDACTED-PII]"


class PIIScanner(Guardrail):
    """Detect and redact PII (email, phone, SSN, IBAN, credit card) from messages."""

    def __init__(self, action: Literal["redact", "block"] = "redact") -> None:
        self._action: Literal["redact", "block"] = action

    @property
    def name(self) -> str:
        return "PIIScanner"

    def _scan_text(self, text: str) -> tuple[bool, str]:
        """Return (found_pii, redacted_text)."""
        redacted = text
        found = False
        for _label, pattern in _PII_PATTERNS:
            new_text, count = pattern.subn(_PII_REPLACEMENT, redacted)
            if count > 0:
                found = True
                redacted = new_text
        return found, redacted

    async def check_input(self, messages: list[Message]) -> GuardrailResult:
        combined = " ".join(m.content for m in messages if m.content)
        found, redacted = self._scan_text(combined)
        if not found:
            return _ALLOW
        if self._action == "block":
            return GuardrailResult(passed=False, reason="PII detected in input", action="block")
        return GuardrailResult(
            passed=True,
            reason="PII redacted from input",
            action="redact",
            redacted_text=redacted,
        )

    async def check_output(self, response: str) -> GuardrailResult:
        found, redacted = self._scan_text(response)
        if not found:
            return _ALLOW
        if self._action == "block":
            return GuardrailResult(passed=False, reason="PII detected in output", action="block")
        return GuardrailResult(
            passed=True,
            reason="PII redacted from output",
            action="redact",
            redacted_text=redacted,
        )


# --- Secrets Scanner ---

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # AWS access key ID
    ("aws_key", re.compile(r"(?<![A-Z0-9])(AKIA[0-9A-Z]{16})(?![A-Z0-9])")),
    # AWS secret access key (40 chars base62)
    ("aws_secret", re.compile(r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])")),
    # GitHub PATs (classic ghp_ and fine-grained github_pat_)
    ("github_token", re.compile(r"(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,255}")),
    # Generic API keys: word "key" or "token" or "secret" followed by 16+ char value
    (
        "generic_api_key",
        re.compile(
            r"""
            (?i)(?:api[-_]?key|token|secret|password|passwd|pwd)
            \s*[:=]\s*
            ["\']?([A-Za-z0-9\-_.]{16,})
            """,
            re.VERBOSE,
        ),
    ),
]

_SECRET_REPLACEMENT = "[REDACTED-SECRET]"


class SecretsScanner(Guardrail):
    """Detect secrets (AWS keys, GitHub tokens, generic API keys) and block by default."""

    def __init__(self, action: Literal["redact", "block"] = "block") -> None:
        self._action: Literal["redact", "block"] = action

    @property
    def name(self) -> str:
        return "SecretsScanner"

    def _scan_text(self, text: str) -> tuple[bool, str]:
        redacted = text
        found = False
        for _label, pattern in _SECRET_PATTERNS:
            new_text, count = pattern.subn(_SECRET_REPLACEMENT, redacted)
            if count > 0:
                found = True
                redacted = new_text
        return found, redacted

    async def check_input(self, messages: list[Message]) -> GuardrailResult:
        combined = " ".join(m.content for m in messages if m.content)
        found, redacted = self._scan_text(combined)
        if not found:
            return _ALLOW
        if self._action == "block":
            return GuardrailResult(passed=False, reason="Secret detected in input", action="block")
        return GuardrailResult(
            passed=True,
            reason="Secret redacted from input",
            action="redact",
            redacted_text=redacted,
        )

    async def check_output(self, response: str) -> GuardrailResult:
        found, redacted = self._scan_text(response)
        if not found:
            return _ALLOW
        if self._action == "block":
            return GuardrailResult(passed=False, reason="Secret detected in output", action="block")
        return GuardrailResult(
            passed=True,
            reason="Secret redacted from output",
            action="redact",
            redacted_text=redacted,
        )


# --- Prompt Injection Detector ---

_INJECTION_PHRASES: list[str] = [
    "ignore previous instructions",
    "ignore all previous",
    "disregard previous",
    "forget your instructions",
    "forget everything",
    "you are now",
    "new instructions:",
    "system prompt",
    "act as",
    "roleplay as",
    "pretend you are",
    "pretend to be",
    "override your",
    "bypass your",
    "jailbreak",
    "developer mode",
    "dan mode",
]

_INJECTION_RE = re.compile(
    "|".join(re.escape(p) for p in _INJECTION_PHRASES),
    re.IGNORECASE,
)


class PromptInjectionDetector(Guardrail):
    """Block suspected prompt injection attempts based on heuristic phrase matching."""

    def __init__(self, action: Literal["block", "redact"] = "block") -> None:
        self._action: Literal["block", "redact"] = action

    @property
    def name(self) -> str:
        return "PromptInjectionDetector"

    def _scan(self, text: str) -> tuple[bool, str]:
        match = _INJECTION_RE.search(text)
        if not match:
            return False, text
        redacted = _INJECTION_RE.sub("[REDACTED-INJECTION]", text)
        return True, redacted

    async def check_input(self, messages: list[Message]) -> GuardrailResult:
        for msg in messages:
            found, _ = self._scan(msg.content or "")
            if found:
                if self._action == "block":
                    return GuardrailResult(
                        passed=False,
                        reason="Prompt injection attempt detected",
                        action="block",
                    )
                redacted = _INJECTION_RE.sub("[REDACTED-INJECTION]", msg.content or "")
                return GuardrailResult(
                    passed=True,
                    reason="Injection phrase redacted",
                    action="redact",
                    redacted_text=redacted,
                )
        return _ALLOW


# --- Output Schema Guard ---


class OutputSchemaGuard(Guardrail):
    """Validate assistant output against a JSON Schema.

    The output must be valid JSON that matches the provided schema.
    Blocks if validation fails or output is not valid JSON.
    """

    def __init__(
        self,
        schema: dict[str, Any],
        action: Literal["block", "redact"] = "block",
    ) -> None:
        self._schema = schema
        self._action: Literal["block", "redact"] = action

    @property
    def name(self) -> str:
        return "OutputSchemaGuard"

    async def check_output(self, response: str) -> GuardrailResult:
        try:
            data = json.loads(response.strip())
        except (json.JSONDecodeError, ValueError):
            return GuardrailResult(
                passed=False,
                reason="Output is not valid JSON",
                action=self._action,
            )

        errors = _validate_json_schema(data, self._schema)
        if errors:
            return GuardrailResult(
                passed=False,
                reason=f"Output schema validation failed: {'; '.join(errors)}",
                action=self._action,
            )
        return _ALLOW


def _validate_json_schema(data: Any, schema: dict[str, Any], path: str = "") -> list[str]:
    """Minimal structural JSON Schema validator (no external deps).

    Supports: type, required, properties, items, minimum, maximum,
    minLength, maxLength, enum, pattern.
    """
    errors: list[str] = []
    loc = path or "root"

    schema_type = schema.get("type")
    if schema_type is not None:
        type_map: dict[str, type | tuple[type, ...]] = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "array": list,
            "object": dict,
            "null": type(None),
        }
        expected = type_map.get(schema_type)
        if expected is not None and not isinstance(data, expected):
            errors.append(f"{loc}: expected {schema_type}, got {type(data).__name__}")
            return errors  # no point checking further

    if schema_type == "object" or isinstance(data, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in data:
                errors.append(f"{loc}: missing required key '{key}'")
        props = schema.get("properties", {})
        for key, sub_schema in props.items():
            if key in data:
                errors.extend(_validate_json_schema(data[key], sub_schema, f"{loc}.{key}"))

    if schema_type == "array" or isinstance(data, list):
        items_schema = schema.get("items")
        if items_schema:
            for i, item in enumerate(data):
                errors.extend(_validate_json_schema(item, items_schema, f"{loc}[{i}]"))

    if schema_type == "string" or isinstance(data, str):
        min_length = schema.get("minLength")
        max_length = schema.get("maxLength")
        if min_length is not None and len(data) < min_length:
            errors.append(f"{loc}: string too short (min {min_length})")
        if max_length is not None and len(data) > max_length:
            errors.append(f"{loc}: string too long (max {max_length})")
        pattern = schema.get("pattern")
        if pattern and not re.search(pattern, data):
            errors.append(f"{loc}: string does not match pattern '{pattern}'")

    if schema_type in ("number", "integer") or isinstance(data, (int, float)):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and data < minimum:
            errors.append(f"{loc}: value {data} < minimum {minimum}")
        if maximum is not None and data > maximum:
            errors.append(f"{loc}: value {data} > maximum {maximum}")

    enum = schema.get("enum")
    if enum is not None and data not in enum:
        errors.append(f"{loc}: value {data!r} not in enum {enum}")

    return errors


# --- Cost Guard ---


class CostGuard(Guardrail):
    """Block a call if the current spend would exceed the configured budget.

    Args:
        budget_usd: Maximum allowed cumulative cost in USD.
        get_current_cost: Callable returning the current accumulated cost.
    """

    def __init__(self, budget_usd: float, get_current_cost: Callable[[], float]) -> None:
        self._budget = budget_usd
        self._get_cost = get_current_cost

    @property
    def name(self) -> str:
        return "CostGuard"

    async def check_input(self, messages: list[Message]) -> GuardrailResult:
        current = self._get_cost()
        if current >= self._budget:
            return GuardrailResult(
                passed=False,
                reason=(
                    f"Budget exceeded: current cost ${current:.4f} >= budget ${self._budget:.4f}"
                ),
                action="block",
            )
        return _ALLOW


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------

_GUARDRAIL_REGISTRY: dict[str, type[Guardrail]] = {
    "pii_scanner": PIIScanner,
    "secrets_scanner": SecretsScanner,
    "prompt_injection": PromptInjectionDetector,
    "output_schema": OutputSchemaGuard,
}


def guardrail_manager_from_config(config: dict[str, Any]) -> GuardrailManager:
    """Build a GuardrailManager from the ``guardrails:`` section of orchestrator.yaml.

    Expected shape::

        guardrails:
          input:
            - type: pii_scanner
              action: redact
            - type: secrets_scanner
              action: block
            - type: prompt_injection
              action: block
          output:
            - type: output_schema
              schema_path: ./schemas/response.json
              action: block

    ``config`` is the dict value of the ``guardrails`` key (not the full YAML).
    Returns a GuardrailManager with all parsed guardrails registered.
    """
    manager = GuardrailManager()

    for side in ("input", "output"):
        entries = config.get(side) or []
        for entry in entries:
            guardrail_type = entry.get("type", "")
            action = entry.get("action", "block")
            cls = _GUARDRAIL_REGISTRY.get(guardrail_type)
            if cls is None:
                raise ValueError(
                    f"Unknown guardrail type '{guardrail_type}'. "
                    f"Available: {sorted(_GUARDRAIL_REGISTRY)}"
                )

            if guardrail_type == "output_schema":
                schema_path = entry.get("schema_path")
                schema_inline = entry.get("schema")
                if schema_path:
                    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
                elif schema_inline:
                    schema = schema_inline
                else:
                    raise ValueError("output_schema guardrail requires 'schema_path' or 'schema'")
                manager.register(OutputSchemaGuard(schema=schema, action=action))
            else:
                # All other built-ins accept optional `action` kwarg
                try:
                    manager.register(cls(action=action))  # type: ignore[call-arg]
                except TypeError:
                    manager.register(cls())  # type: ignore[call-arg]

    return manager
