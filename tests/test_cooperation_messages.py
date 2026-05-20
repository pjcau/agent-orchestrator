"""Tests for typed cooperation messages (P5a — tactical).

Covers:
- Round-trip dict ↔ class for every typed message.
- ``parse_message`` dispatch.
- Tolerance to missing optional fields and unknown keys.
- Clean error on missing or unknown ``kind``.
- Re-export from ``core.cooperation``.
- Integration: typed ``DelegateMessage`` / ``ResultMessage`` map cleanly to
  the existing ``CooperationProtocol`` queue/store shape.
"""

from __future__ import annotations

import pytest
from agent_orchestrator.core.cooperation import (
    AgentMessage,
    CooperationProtocol,
    SharedContextStore,
    TaskAssignment,
    TaskReport,
)
from agent_orchestrator.core.cooperation_messages import (
    ALL_KINDS,
    KIND_CAPABILITY_QUERY,
    KIND_CAPABILITY_RESPONSE,
    KIND_CONFLICT,
    KIND_DELEGATE,
    KIND_RESULT,
    CapabilityQueryMessage,
    CapabilityResponseMessage,
    ConflictMessage,
    CooperationMessage,
    DelegateMessage,
    ResultMessage,
    parse_message,
)


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_delegate_round_trip(self):
        msg = DelegateMessage(
            from_agent="team-lead",
            to_agent="backend",
            task_id="t1",
            description="Build API",
            priority="high",
            payload={"endpoint": "/users"},
        )
        d = msg.to_dict()
        assert d["kind"] == KIND_DELEGATE
        assert DelegateMessage.from_dict(d) == msg

    def test_result_round_trip(self):
        msg = ResultMessage(
            from_agent="backend",
            to_agent="team-lead",
            task_id="t1",
            success=True,
            output="done",
            error=None,
            metadata={"cost_usd": 0.01},
        )
        d = msg.to_dict()
        assert d["kind"] == KIND_RESULT
        assert ResultMessage.from_dict(d) == msg

    def test_capability_query_round_trip(self):
        msg = CapabilityQueryMessage(
            from_agent="team-lead",
            to_agent="backend",
            query="what skills do you have?",
        )
        d = msg.to_dict()
        assert d["kind"] == KIND_CAPABILITY_QUERY
        assert CapabilityQueryMessage.from_dict(d) == msg

    def test_capability_response_round_trip(self):
        msg = CapabilityResponseMessage(
            from_agent="backend",
            to_agent="team-lead",
            capabilities=["filesystem", "shell", "github_skill"],
        )
        d = msg.to_dict()
        assert d["kind"] == KIND_CAPABILITY_RESPONSE
        assert CapabilityResponseMessage.from_dict(d) == msg

    def test_conflict_round_trip(self):
        msg = ConflictMessage(
            from_agent="frontend",
            to_agent="team-lead",
            task_id="ui",
            reason="Both backend and frontend wrote shared.py",
            proposed_resolution="Keep backend version",
        )
        d = msg.to_dict()
        assert d["kind"] == KIND_CONFLICT
        assert ConflictMessage.from_dict(d) == msg


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class TestParseMessage:
    def test_dispatch_delegate(self):
        d = DelegateMessage(
            from_agent="lead", to_agent="backend", task_id="t1", description="x"
        ).to_dict()
        parsed = parse_message(d)
        assert isinstance(parsed, DelegateMessage)
        assert parsed.task_id == "t1"

    def test_dispatch_all_kinds(self):
        constructors: dict[str, type[CooperationMessage]] = {
            KIND_DELEGATE: DelegateMessage,
            KIND_RESULT: ResultMessage,
            KIND_CAPABILITY_QUERY: CapabilityQueryMessage,
            KIND_CAPABILITY_RESPONSE: CapabilityResponseMessage,
            KIND_CONFLICT: ConflictMessage,
        }
        # Sanity: every advertised kind is dispatchable.
        for kind in ALL_KINDS:
            cls = constructors[kind]
            d = cls(from_agent="a", to_agent="b").to_dict()
            assert isinstance(parse_message(d), cls)

    def test_missing_kind_raises(self):
        with pytest.raises(ValueError, match="missing required 'kind'"):
            parse_message({"from_agent": "lead"})

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError, match="unknown cooperation message kind"):
            parse_message({"kind": "totally_made_up"})

    def test_non_dict_input_raises(self):
        with pytest.raises(ValueError, match="expected dict"):
            parse_message("not a dict")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tolerance
# ---------------------------------------------------------------------------


class TestTolerance:
    def test_missing_optional_fields_default_to_empty(self):
        # Only the kind is provided — every payload field falls back.
        msg = parse_message({"kind": KIND_DELEGATE})
        assert isinstance(msg, DelegateMessage)
        assert msg.task_id == ""
        assert msg.description == ""
        assert msg.priority == "normal"
        assert msg.payload == {}
        # Header fields still get default factories.
        assert msg.message_id  # uuid string
        assert msg.timestamp > 0

    def test_unknown_keys_are_ignored(self):
        msg = parse_message(
            {
                "kind": KIND_RESULT,
                "from_agent": "backend",
                "task_id": "t1",
                "success": True,
                "ghost_field": "should be ignored",
            }
        )
        assert isinstance(msg, ResultMessage)
        assert msg.task_id == "t1"
        assert msg.success is True

    def test_capability_response_defaults_to_empty_list(self):
        msg = CapabilityResponseMessage.from_dict({"kind": KIND_CAPABILITY_RESPONSE})
        assert msg.capabilities == []

    def test_conflict_optional_resolution_can_be_none(self):
        msg = ConflictMessage.from_dict({"kind": KIND_CONFLICT, "task_id": "t1", "reason": "boom"})
        assert msg.proposed_resolution is None


# ---------------------------------------------------------------------------
# Frozen dataclass guarantees
# ---------------------------------------------------------------------------


class TestFrozen:
    def test_delegate_is_frozen(self):
        msg = DelegateMessage(from_agent="a", task_id="t1")
        with pytest.raises(Exception):  # FrozenInstanceError subclasses AttributeError
            msg.task_id = "other"  # type: ignore[misc]

    def test_kind_is_class_var(self):
        # The ``kind`` class variable is part of the type, not the instance.
        assert DelegateMessage.kind == KIND_DELEGATE
        assert ResultMessage.kind == KIND_RESULT
        # to_dict still emits it.
        assert DelegateMessage(from_agent="a").to_dict()["kind"] == KIND_DELEGATE


# ---------------------------------------------------------------------------
# Re-export
# ---------------------------------------------------------------------------


class TestReExport:
    def test_typed_messages_re_exported_from_cooperation(self):
        from agent_orchestrator.core import cooperation as coop

        assert coop.DelegateMessage is DelegateMessage
        assert coop.parse_message is parse_message
        assert KIND_DELEGATE in coop.ALL_KINDS


# ---------------------------------------------------------------------------
# Integration with existing CooperationProtocol
# ---------------------------------------------------------------------------


class TestIntegrationWithProtocol:
    def test_delegate_message_drives_protocol_assignment(self):
        """A typed DelegateMessage carries the same fields as TaskAssignment."""
        proto = CooperationProtocol()
        msg = DelegateMessage(
            from_agent="team-lead",
            to_agent="backend",
            task_id="t1",
            description="Build API",
            priority="normal",
        )
        # Adapt typed message → existing TaskAssignment.
        d = msg.to_dict()
        proto.assign(
            TaskAssignment(
                task_id=d["task_id"],
                from_agent=d["from_agent"],
                to_agent=d["to_agent"],
                description=d["description"],
            )
        )
        pending = proto.get_pending()
        assert len(pending) == 1
        assert pending[0].task_id == "t1"
        assert pending[0].to_agent == "backend"

    def test_result_message_drives_protocol_completion(self):
        """A typed ResultMessage maps cleanly to TaskReport.complete()."""
        proto = CooperationProtocol()
        proto.assign(
            TaskAssignment(task_id="t1", from_agent="lead", to_agent="backend", description="x")
        )
        result = ResultMessage(
            from_agent="backend",
            to_agent="team-lead",
            task_id="t1",
            success=True,
            output="done",
        )
        d = result.to_dict()
        proto.complete(
            TaskReport(
                task_id=d["task_id"],
                agent_name=d["from_agent"],
                success=d["success"],
                output=d["output"],
            )
        )
        assert proto.all_complete()
        completed = proto.get_completed()
        assert "t1" in completed
        assert completed["t1"].success is True

    def test_typed_messages_round_trip_preserves_store_shape(self):
        """Dict produced by to_dict has every field needed to reconstruct
        the legacy AgentMessage envelope used by SharedContextStore."""
        store = SharedContextStore()
        typed = DelegateMessage(
            from_agent="team-lead",
            to_agent="backend",
            task_id="t1",
            description="Build API",
        )
        d = typed.to_dict()
        # Map onto AgentMessage and push through the store — the existing
        # bus-shape (from/to/content/related_task_id) is fully populated.
        store.send_message(
            AgentMessage(
                from_agent=d["from_agent"],
                to_agent=d["to_agent"],
                content=d["description"],
                message_type="request",
                related_task_id=d["task_id"],
            )
        )
        msgs = store.get_messages(agent_name="backend")
        assert len(msgs) == 1
        assert msgs[0].related_task_id == "t1"
        assert msgs[0].from_agent == "team-lead"
