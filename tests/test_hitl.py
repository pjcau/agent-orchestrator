"""Tests for Human-in-the-Loop (HITL) interrupt/resume."""

import pytest

from agent_orchestrator.core.graph import (
    END,
    START,
    GraphInterrupt,
    Interrupt,
    InterruptType,
    StateGraph,
)
from agent_orchestrator.core.checkpoint import InMemoryCheckpointer


class TestHumanInTheLoop:
    @pytest.mark.asyncio
    async def test_interrupt_pauses_graph(self):
        """A node that raises GraphInterrupt pauses the graph."""

        async def needs_approval(state):
            if not state.get("approved"):
                raise GraphInterrupt(
                    Interrupt(
                        interrupt_type=InterruptType.APPROVAL,
                        message="Approve deployment?",
                        node="approve",
                        options=["approve", "reject"],
                    )
                )
            return {"status": "deployed"}

        g = StateGraph()
        g.add_node("approve", needs_approval)
        g.add_edge(START, "approve")
        g.add_edge("approve", END)

        cp = InMemoryCheckpointer()
        compiled = g.compile(checkpointer=cp)

        # First invocation — should interrupt
        result = await compiled.invoke({}, thread_id="deploy-1")
        assert not result.success
        assert result.interrupted is not None
        assert result.interrupted.interrupt_type == InterruptType.APPROVAL
        assert result.interrupted.message == "Approve deployment?"
        assert result.interrupted.options == ["approve", "reject"]

    @pytest.mark.asyncio
    async def test_resume_after_interrupt(self):
        """Resume graph execution after providing human input."""

        async def needs_approval(state):
            if not state.get("approved"):
                raise GraphInterrupt(
                    Interrupt(
                        interrupt_type=InterruptType.APPROVAL,
                        message="Approve?",
                        node="approve",
                    )
                )
            return {"status": "approved_and_done"}

        g = StateGraph()
        g.add_node("approve", needs_approval)
        g.add_edge(START, "approve")
        g.add_edge("approve", END)

        cp = InMemoryCheckpointer()
        compiled = g.compile(checkpointer=cp)

        # First run — interrupts
        result1 = await compiled.invoke({"data": "important"}, thread_id="t1")
        assert result1.interrupted is not None

        # Resume with human input
        result2 = await compiled.invoke(
            {},
            resume_from="t1:0",
            human_input={"approved": True},
        )
        assert result2.success
        assert result2.state["status"] == "approved_and_done"
        assert result2.state["data"] == "important"  # Original state preserved

    @pytest.mark.asyncio
    async def test_human_input_node(self):
        """Interrupt to get free-form human input."""

        async def ask_name(state):
            if not state.get("user_name"):
                raise GraphInterrupt(
                    Interrupt(
                        interrupt_type=InterruptType.HUMAN_INPUT,
                        message="What is your name?",
                        node="ask_name",
                    )
                )
            return {"greeting": f"Hello, {state['user_name']}!"}

        g = StateGraph()
        g.add_node("ask_name", ask_name)
        g.add_edge(START, "ask_name")
        g.add_edge("ask_name", END)

        cp = InMemoryCheckpointer()
        compiled = g.compile(checkpointer=cp)

        # Interrupt
        result1 = await compiled.invoke({}, thread_id="greet-1")
        assert result1.interrupted is not None
        assert result1.interrupted.interrupt_type == InterruptType.HUMAN_INPUT

        # Resume with name
        result2 = await compiled.invoke(
            {},
            resume_from="greet-1:0",
            human_input={"user_name": "Alice"},
        )
        assert result2.success
        assert result2.state["greeting"] == "Hello, Alice!"

    @pytest.mark.asyncio
    async def test_multiple_interrupts_in_sequence(self):
        """Graph can interrupt multiple times at different nodes."""

        async def step1(state):
            if not state.get("step1_done"):
                raise GraphInterrupt(
                    Interrupt(
                        interrupt_type=InterruptType.HUMAN_INPUT,
                        message="Input for step 1?",
                        node="step1",
                    )
                )
            return {"log": "step1_complete"}

        async def step2(state):
            if not state.get("step2_done"):
                raise GraphInterrupt(
                    Interrupt(
                        interrupt_type=InterruptType.HUMAN_INPUT,
                        message="Input for step 2?",
                        node="step2",
                    )
                )
            return {"log": state.get("log", "") + "+step2_complete"}

        g = StateGraph()
        g.add_node("step1", step1)
        g.add_node("step2", step2)
        g.add_edge(START, "step1")
        g.add_edge("step1", "step2")
        g.add_edge("step2", END)

        cp = InMemoryCheckpointer()
        compiled = g.compile(checkpointer=cp)

        # First interrupt at step1
        r1 = await compiled.invoke({}, thread_id="multi")
        assert r1.interrupted is not None
        assert r1.interrupted.node == "step1"

        # Resume step1 -> interrupts at step2
        r2 = await compiled.invoke(
            {},
            resume_from="multi:0",
            human_input={"step1_done": True},
        )
        assert r2.interrupted is not None
        assert r2.interrupted.node == "step2"

        # Resume step2 -> completes
        r3 = await compiled.invoke(
            {},
            resume_from="multi:1",
            human_input={"step2_done": True},
        )
        assert r3.success
        assert "step2_complete" in r3.state["log"]

    @pytest.mark.asyncio
    async def test_interrupt_in_parallel_node(self):
        """If a parallel node interrupts, the whole step pauses."""

        async def normal_node(state):
            return {"normal": True}

        async def interrupt_node(state):
            raise GraphInterrupt(
                Interrupt(
                    interrupt_type=InterruptType.APPROVAL,
                    message="Need approval",
                    node="interrupt_node",
                )
            )

        g = StateGraph()
        g.add_node("normal", normal_node)
        g.add_node("interrupt_node", interrupt_node)
        g.add_edge(START, "normal")
        g.add_edge(START, "interrupt_node")
        g.add_edge("normal", END)
        g.add_edge("interrupt_node", END)

        cp = InMemoryCheckpointer()
        result = await g.compile(checkpointer=cp).invoke({}, thread_id="par")
        assert result.interrupted is not None
        assert result.interrupted.message == "Need approval"
