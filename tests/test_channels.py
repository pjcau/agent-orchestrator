"""Tests for channel-based state management (v1.1 Sprint 1)."""

import operator
import pytest

from agent_orchestrator.core.channels import (
    BaseChannel,
    LastValue,
    BinaryOperatorChannel,
    TopicChannel,
    EphemeralChannel,
    BarrierChannel,
    ChannelManager,
    EmptyChannelError,
    InvalidUpdateError,
)


# ─── LastValue ────────────────────────────────────────────────────────


class TestLastValue:
    def test_empty_raises(self):
        ch = LastValue()
        with pytest.raises(EmptyChannelError):
            ch.get()

    def test_default_value(self):
        ch = LastValue(default=42)
        assert ch.get() == 42
        assert ch.is_available()

    def test_single_update(self):
        ch = LastValue()
        assert ch.update([10]) is True
        assert ch.get() == 10

    def test_no_update(self):
        ch = LastValue(default=1)
        assert ch.update([]) is False
        assert ch.get() == 1

    def test_multiple_updates_raises(self):
        ch = LastValue()
        with pytest.raises(InvalidUpdateError):
            ch.update([1, 2])

    def test_checkpoint_roundtrip(self):
        ch = LastValue()
        ch.update([99])
        data = ch.checkpoint()
        ch2 = LastValue()
        ch2.from_checkpoint(data)
        assert ch2.get() == 99

    def test_checkpoint_empty(self):
        ch = LastValue()
        assert ch.checkpoint() is None


# ─── BinaryOperatorChannel ────────────────────────────────────────────


class TestBinaryOperatorChannel:
    def test_add_reducer(self):
        ch = BinaryOperatorChannel(operator.add, default=0)
        ch.update([5])
        assert ch.get() == 5
        ch.update([3])
        assert ch.get() == 8

    def test_multiple_concurrent_updates(self):
        ch = BinaryOperatorChannel(operator.add, default=0)
        ch.update([1, 2, 3])
        assert ch.get() == 6

    def test_empty_no_default_raises(self):
        ch = BinaryOperatorChannel(operator.add)
        with pytest.raises(EmptyChannelError):
            ch.get()

    def test_first_update_without_default(self):
        ch = BinaryOperatorChannel(operator.add)
        ch.update([10])
        assert ch.get() == 10

    def test_list_concat_reducer(self):
        ch = BinaryOperatorChannel(operator.add, default=[])
        ch.update([[1, 2]])
        ch.update([[3]])
        assert ch.get() == [1, 2, 3]

    def test_checkpoint_roundtrip(self):
        ch = BinaryOperatorChannel(operator.add, default=0)
        ch.update([10])
        data = ch.checkpoint()
        ch2 = BinaryOperatorChannel(operator.add, default=0)
        ch2.from_checkpoint(data)
        assert ch2.get() == 10


# ─── TopicChannel ─────────────────────────────────────────────────────


class TestTopicChannel:
    def test_accumulate(self):
        ch = TopicChannel()
        ch.update(["a", "b"])
        ch.update(["c"])
        assert ch.get() == ["a", "b", "c"]

    def test_always_available(self):
        ch = TopicChannel()
        assert ch.is_available()
        assert ch.get() == []

    def test_non_accumulate_resets(self):
        ch = TopicChannel(accumulate=False)
        ch.update(["x"])
        assert ch.get() == ["x"]
        ch.reset()
        assert ch.get() == []

    def test_checkpoint_roundtrip(self):
        ch = TopicChannel()
        ch.update([1, 2, 3])
        data = ch.checkpoint()
        ch2 = TopicChannel()
        ch2.from_checkpoint(data)
        assert ch2.get() == [1, 2, 3]


# ─── EphemeralChannel ─────────────────────────────────────────────────


class TestEphemeralChannel:
    def test_stores_and_resets(self):
        ch = EphemeralChannel()
        ch.update(["trigger"])
        assert ch.get() == "trigger"
        ch.reset()
        assert not ch.is_available()

    def test_takes_last_value(self):
        ch = EphemeralChannel()
        ch.update(["a", "b", "c"])
        assert ch.get() == "c"

    def test_never_persisted(self):
        ch = EphemeralChannel()
        ch.update(["x"])
        assert ch.checkpoint() is None
        ch.from_checkpoint("anything")
        assert not ch.is_available()


# ─── BarrierChannel ───────────────────────────────────────────────────


class TestBarrierChannel:
    def test_waits_for_all(self):
        ch = BarrierChannel({"a", "b", "c"})
        assert not ch.is_available()
        ch.update(["a"])
        assert not ch.is_available()
        ch.update(["b", "c"])
        assert ch.is_available()
        assert ch.get() is True

    def test_reset_clears(self):
        ch = BarrierChannel({"x"})
        ch.update(["x"])
        assert ch.is_available()
        ch.reset()
        assert not ch.is_available()

    def test_checkpoint_roundtrip(self):
        ch = BarrierChannel({"a", "b"})
        ch.update(["a"])
        data = ch.checkpoint()
        ch2 = BarrierChannel({"a", "b"})
        ch2.from_checkpoint(data)
        assert not ch2.is_available()
        ch2.update(["b"])
        assert ch2.is_available()


# ─── ChannelManager ──────────────────────────────────────────────────


class TestChannelManager:
    def test_register_and_get_state(self):
        mgr = ChannelManager()
        mgr.register("count", LastValue(default=0))
        mgr.register("log", TopicChannel())
        state = mgr.get_state()
        assert state == {"count": 0, "log": []}

    def test_apply_writes(self):
        mgr = ChannelManager()
        mgr.register("x", LastValue(default=0))
        mgr.register("msgs", TopicChannel())
        changed = mgr.apply_writes({"x": [42], "msgs": ["hello", "world"]})
        assert "x" in changed
        assert "msgs" in changed
        state = mgr.get_state()
        assert state["x"] == 42
        assert state["msgs"] == ["hello", "world"]

    def test_auto_creates_last_value_for_unknown_keys(self):
        mgr = ChannelManager()
        mgr.apply_writes({"new_key": [99]})
        assert mgr.get_state()["new_key"] == 99

    def test_reset_ephemeral(self):
        mgr = ChannelManager()
        mgr.register("trigger", EphemeralChannel())
        mgr.register("count", LastValue(default=0))
        mgr.apply_writes({"trigger": ["go"], "count": [1]})
        mgr.reset_ephemeral()
        state = mgr.get_state()
        assert "trigger" not in state  # ephemeral cleared
        assert state["count"] == 1  # persistent kept

    def test_checkpoint_and_restore(self):
        mgr = ChannelManager()
        mgr.register("x", LastValue(default=0))
        mgr.register("log", TopicChannel())
        mgr.apply_writes({"x": [10], "log": ["a", "b"]})
        data = mgr.checkpoint()

        mgr2 = ChannelManager()
        mgr2.register("x", LastValue())
        mgr2.register("log", TopicChannel())
        mgr2.restore(data)
        assert mgr2.get_state() == {"x": 10, "log": ["a", "b"]}
