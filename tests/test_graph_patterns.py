"""Tests for v0.7.0 — Advanced Graph Patterns."""

import asyncio
import time

import pytest
from agent_orchestrator.core.graph import (
    START,
    END,
    StateGraph,
)
from agent_orchestrator.core.graph_patterns import (
    SubGraphNode,
    retry_node,
    loop_node,
    map_reduce_node,
    provider_annotated_node,
    long_context_node,
)
from agent_orchestrator.core.graph_templates import (
    EdgeTemplate,
    GraphTemplate,
    GraphTemplateStore,
    NodeTemplate,
)
from agent_orchestrator.core.provider import (
    Completion,
    ModelCapabilities,
    Provider,
    StreamChunk,
    Usage,
)


# --- Mock Provider ---


class MockProvider(Provider):
    def __init__(self, model: str = "mock", context: int = 4096):
        self._model = model
        self._context = context

    async def complete(self, messages, tools=None, system=None, max_tokens=4096, temperature=0.0):
        return Completion(
            content=f"response from {self._model}",
            usage=Usage(input_tokens=100, output_tokens=50, cost_usd=0.001),
        )

    async def stream(self, messages, tools=None, system=None, max_tokens=4096):
        yield StreamChunk(content="mock", is_final=True)

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(max_context=self._context)

    @property
    def input_cost_per_million(self) -> float:
        return 0.0

    @property
    def output_cost_per_million(self) -> float:
        return 0.0


# --- SubGraphNode ---


class TestSubGraphNode:
    @pytest.mark.asyncio
    async def test_basic_subgraph(self):
        async def double(state):
            return {"value": state.get("value", 0) * 2}

        sub = StateGraph()
        sub.add_node("double", double)
        sub.add_edge(START, "double")
        sub.add_edge("double", END)

        node = SubGraphNode(sub.compile())
        result = await node({"value": 5})
        assert result["value"] == 10

    @pytest.mark.asyncio
    async def test_subgraph_with_input_mapping(self):
        async def increment(state):
            return {"x": state.get("x", 0) + 1}

        sub = StateGraph()
        sub.add_node("inc", increment)
        sub.add_edge(START, "inc")
        sub.add_edge("inc", END)

        node = SubGraphNode(
            sub.compile(),
            input_mapping={"parent_val": "x"},
            output_mapping={"x": "result"},
        )
        result = await node({"parent_val": 10})
        assert result["result"] == 11

    @pytest.mark.asyncio
    async def test_subgraph_no_mapping_passes_full_state(self):
        async def echo(state):
            return {"echoed": state.get("input", "")}

        sub = StateGraph()
        sub.add_node("echo", echo)
        sub.add_edge(START, "echo")
        sub.add_edge("echo", END)

        node = SubGraphNode(sub.compile())
        result = await node({"input": "hello"})
        assert result["echoed"] == "hello"


# --- retry_node ---


class TestRetryNode:
    @pytest.mark.asyncio
    async def test_succeeds_on_first_try(self):
        call_count = 0

        async def good_node(state):
            nonlocal call_count
            call_count += 1
            return {"output": "ok"}

        wrapped = retry_node(good_node, max_retries=3)
        result = await wrapped({})
        assert result["output"] == "ok"
        assert result["_retry_info"]["attempt"] == 0
        assert result["_retry_info"]["succeeded"] is True
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_failure(self):
        call_count = 0

        async def flaky_node(state):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("not yet")
            return {"output": "recovered"}

        wrapped = retry_node(flaky_node, max_retries=3)
        result = await wrapped({})
        assert result["output"] == "recovered"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self):
        async def always_fail(state):
            raise ValueError("fail")

        wrapped = retry_node(always_fail, max_retries=2)
        with pytest.raises(RuntimeError, match="failed after 2 retries"):
            await wrapped({})

    @pytest.mark.asyncio
    async def test_upgrade_providers(self):
        providers_used = []

        async def node_that_reads_provider(state):
            provider = state.get("_provider")
            if provider:
                providers_used.append(provider.model_id)
            if len(providers_used) < 2:
                raise ValueError("need upgrade")
            return {"output": "done"}

        p1 = MockProvider("local-model")
        p2 = MockProvider("cloud-model")
        wrapped = retry_node(
            node_that_reads_provider,
            max_retries=3,
            upgrade_providers=[p1, p2],
        )
        result = await wrapped({})
        assert "cloud-model" in providers_used
        assert result["output"] == "done"


# --- loop_node ---


class TestLoopNode:
    @pytest.mark.asyncio
    async def test_loops_until_condition_false(self):
        async def increment(state):
            return {"counter": state.get("counter", 0) + 1}

        wrapped = loop_node(
            increment,
            condition=lambda s: s.get("counter", 0) < 5,
            max_iterations=10,
        )
        result = await wrapped({"counter": 0})
        assert result["counter"] == 5
        assert result["_loop_iterations"] == 5

    @pytest.mark.asyncio
    async def test_stops_at_max_iterations(self):
        async def increment(state):
            return {"counter": state.get("counter", 0) + 1}

        wrapped = loop_node(
            increment,
            condition=lambda s: True,  # never stops
            max_iterations=3,
        )
        result = await wrapped({"counter": 0})
        assert result["counter"] == 3
        assert result["_loop_iterations"] == 3

    @pytest.mark.asyncio
    async def test_no_iterations_if_condition_false(self):
        async def noop(state):
            return {"touched": True}

        wrapped = loop_node(
            noop,
            condition=lambda s: False,
            max_iterations=10,
        )
        result = await wrapped({})
        assert result.get("_loop_iterations") == 0
        assert "touched" not in result


# --- map_reduce_node ---


class TestMapReduceNode:
    @pytest.mark.asyncio
    async def test_basic_map_reduce(self):
        async def square(state):
            return {"squared": state["_item"] ** 2}

        def sum_results(results):
            return {"total": sum(r.get("squared", 0) for r in results)}

        wrapped = map_reduce_node(square, sum_results, items_key="numbers")
        result = await wrapped({"numbers": [1, 2, 3, 4, 5]})
        assert result["results"]["total"] == 55

    @pytest.mark.asyncio
    async def test_empty_items(self):
        async def identity(state):
            return {"val": state["_item"]}

        def merge(results):
            return {"items": [r.get("val") for r in results]}

        wrapped = map_reduce_node(identity, merge)
        result = await wrapped({"items": []})
        assert result["results"]["items"] == []

    @pytest.mark.asyncio
    async def test_concurrency_limit(self):
        max_concurrent = 0
        current = 0

        async def tracked(state):
            nonlocal current, max_concurrent
            current += 1
            max_concurrent = max(max_concurrent, current)
            await asyncio.sleep(0.01)
            current -= 1
            return {"val": state["_item"]}

        def merge(results):
            return results

        wrapped = map_reduce_node(tracked, merge, max_concurrency=2)
        await wrapped({"items": [1, 2, 3, 4, 5]})
        assert max_concurrent <= 2

    @pytest.mark.asyncio
    async def test_item_index(self):
        async def capture_index(state):
            return {"index": state["_item_index"]}

        def collect(results):
            return sorted(r["index"] for r in results)

        wrapped = map_reduce_node(capture_index, collect, items_key="data")
        result = await wrapped({"data": ["a", "b", "c"]})
        assert result["results"] == [0, 1, 2]


# --- provider_annotated_node ---


class TestProviderAnnotatedNode:
    def test_local_preference(self):
        providers = {
            "ollama": MockProvider("ollama-local"),
            "cloud": MockProvider("cloud-model"),
        }
        selected_model = []

        def factory(provider):
            selected_model.append(provider.model_id)

            async def node(state):
                return {"model": provider.model_id}

            return node

        provider_annotated_node(providers, factory, preferred="local")
        assert "ollama-local" in selected_model

    def test_cloud_preference(self):
        providers = {
            "ollama": MockProvider("ollama-local"),
            "cloud": MockProvider("cloud-model"),
        }
        selected = []

        def factory(provider):
            selected.append(provider.model_id)

            async def node(state):
                return {}

            return node

        provider_annotated_node(providers, factory, preferred="cloud")
        assert "cloud-model" in selected

    def test_specific_key(self):
        providers = {
            "a": MockProvider("model-a"),
            "b": MockProvider("model-b"),
        }
        selected = []

        def factory(provider):
            selected.append(provider.model_id)

            async def node(state):
                return {}

            return node

        provider_annotated_node(providers, factory, preferred="b")
        assert "model-b" in selected

    def test_fallback(self):
        providers = {
            "cloud": MockProvider("cloud-model"),
        }
        selected = []

        def factory(provider):
            selected.append(provider.model_id)

            async def node(state):
                return {}

            return node

        # No local, but fallback to cloud
        provider_annotated_node(providers, factory, preferred="local", fallback="cloud")
        assert "cloud-model" in selected

    def test_raises_if_no_match(self):
        providers = {"cloud": MockProvider("cloud-model")}

        def factory(provider):
            async def node(state):
                return {}

            return node

        with pytest.raises(ValueError, match="could not resolve"):
            provider_annotated_node(providers, factory, preferred="local")


# --- long_context_node ---


class TestLongContextNode:
    def test_selects_large_context_provider(self):
        providers = {
            "small": MockProvider("small", context=32_000),
            "large": MockProvider("large", context=200_000),
        }
        selected = []

        def factory(provider):
            selected.append(provider.model_id)

            async def node(state):
                return {}

            return node

        long_context_node(providers, factory, min_context=128_000)
        assert "large" in selected

    def test_fallback_to_largest_if_none_meet_minimum(self):
        providers = {
            "a": MockProvider("a", context=32_000),
            "b": MockProvider("b", context=64_000),
        }
        selected = []

        def factory(provider):
            selected.append(provider.model_id)

            async def node(state):
                return {}

            return node

        long_context_node(providers, factory, min_context=128_000)
        assert "b" in selected

    def test_raises_if_empty_providers(self):
        def factory(provider):
            async def node(state):
                return {}

            return node

        with pytest.raises(ValueError, match="empty"):
            long_context_node({}, factory)


# --- GraphTemplateStore ---


class TestGraphTemplateStore:
    def _make_template(self, name="test"):
        return GraphTemplate(
            name=name,
            description="A test template",
            version=1,
            nodes=[
                NodeTemplate("step1", "custom", {"function_name": "do_step1"}),
                NodeTemplate("step2", "custom", {"function_name": "do_step2"}),
            ],
            edges=[
                EdgeTemplate("__start__", "step1"),
                EdgeTemplate("step1", "step2"),
                EdgeTemplate("step2", "__end__"),
            ],
            created_at=time.time(),
        )

    def test_save_and_get(self):
        store = GraphTemplateStore()
        tmpl = self._make_template()
        saved = store.save(tmpl)
        assert saved.version == 1
        retrieved = store.get("test")
        assert retrieved is not None
        assert retrieved.name == "test"

    def test_auto_increment_version(self):
        store = GraphTemplateStore()
        store.save(self._make_template())
        store.save(self._make_template())
        store.save(self._make_template())
        assert store.get("test").version == 3
        assert store.get_versions("test") == [1, 2, 3]

    def test_get_specific_version(self):
        store = GraphTemplateStore()
        store.save(self._make_template())
        t2 = self._make_template()
        t2.description = "version 2"
        store.save(t2)
        v1 = store.get_version("test", 1)
        v2 = store.get_version("test", 2)
        assert v1.description == "A test template"
        assert v2.description == "version 2"

    def test_get_nonexistent(self):
        store = GraphTemplateStore()
        assert store.get("nope") is None
        assert store.get_version("nope", 1) is None

    def test_list_templates(self):
        store = GraphTemplateStore()
        store.save(self._make_template("a"))
        store.save(self._make_template("b"))
        assert sorted(store.list_templates()) == ["a", "b"]

    def test_delete(self):
        store = GraphTemplateStore()
        store.save(self._make_template())
        assert store.delete("test") is True
        assert store.get("test") is None
        assert store.delete("test") is False

    def test_export_import_dict(self):
        store = GraphTemplateStore()
        store.save(self._make_template())
        exported = store.export_dict("test")
        assert exported["name"] == "test"
        assert len(exported["nodes"]) == 2
        assert len(exported["edges"]) == 3

        imported = store.import_dict(exported)
        assert imported.name == "test"
        assert len(imported.nodes) == 2

    def test_json_roundtrip(self):
        store = GraphTemplateStore()
        store.save(self._make_template())
        json_str = store.to_json("test")
        imported = store.from_json(json_str)
        assert imported.name == "test"

    def test_export_yaml_is_json(self):
        store = GraphTemplateStore()
        store.save(self._make_template())
        yaml_str = store.export_yaml("test")
        imported = store.import_yaml(yaml_str)
        assert imported.name == "test"

    @pytest.mark.asyncio
    async def test_build_graph_custom_nodes(self):
        store = GraphTemplateStore()
        store.save(self._make_template())

        async def do_step1(state):
            return {"step1_done": True}

        async def do_step2(state):
            return {"step2_done": True}

        graph = store.build_graph(
            "test",
            node_registry={"do_step1": do_step1, "do_step2": do_step2},
        )
        compiled = graph.compile()
        result = await compiled.invoke({})
        assert result.success is True
        assert result.state.get("step1_done") is True
        assert result.state.get("step2_done") is True

    @pytest.mark.asyncio
    async def test_build_graph_llm_nodes(self):
        store = GraphTemplateStore()
        tmpl = GraphTemplate(
            name="llm_test",
            description="LLM template",
            version=1,
            nodes=[
                NodeTemplate(
                    "analyze",
                    "llm",
                    {
                        "system": "Analyze this.",
                        "prompt_key": "input",
                        "output_key": "analysis",
                        "provider": "mock",
                    },
                ),
            ],
            edges=[
                EdgeTemplate("__start__", "analyze"),
                EdgeTemplate("analyze", "__end__"),
            ],
            created_at=time.time(),
        )
        store.save(tmpl)
        graph = store.build_graph("llm_test", providers={"mock": MockProvider()})
        compiled = graph.compile()
        result = await compiled.invoke({"input": "test data"})
        assert result.success is True
        assert "analysis" in result.state

    def test_build_graph_unknown_template(self):
        store = GraphTemplateStore()
        with pytest.raises(KeyError):
            store.build_graph("nonexistent")

    def test_build_graph_missing_custom_function(self):
        store = GraphTemplateStore()
        store.save(self._make_template())
        with pytest.raises(ValueError, match="not found in node_registry"):
            store.build_graph("test", node_registry={})
