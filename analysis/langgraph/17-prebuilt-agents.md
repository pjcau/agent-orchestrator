# LangGraph — Prebuilt Agents (create_react_agent)

## Note: Deprecation

All prebuilt classes are deprecated in favor of `langchain.agents` equivalents (v1.0 migration). Code is functional but marked with `@deprecated(category=LangGraphDeprecatedSinceV10)`.

## create_react_agent — Core Agent Factory

```python
def create_react_agent(
    model: str | LanguageModelLike | Callable[[StateSchema, Runtime], BaseChatModel],
    tools: Sequence[BaseTool | Callable | dict] | ToolNode,
    *,
    prompt: Prompt | None = None,
    response_format: StructuredResponseSchema | None = None,
    pre_model_hook: RunnableLike | None = None,
    post_model_hook: RunnableLike | None = None,
    state_schema: StateSchemaType | None = None,
    context_schema: type[Any] | None = None,
    checkpointer: Checkpointer | None = None,
    store: BaseStore | None = None,
    interrupt_before: list[str] | None = None,
    interrupt_after: list[str] | None = None,
    debug: bool = False,
    version: Literal["v1", "v2"] = "v2",
    name: str | None = None,
) -> CompiledStateGraph
```

## Key Design Decisions

### 1. Dynamic Model Selection

`model` accepts a callable `(state, runtime) -> BaseChatModel`, enabling runtime model switching based on state or context. First-class pattern.

### 2. Two Execution Versions

| Version | Tool Execution |
|---------|---------------|
| **v1** | Single ToolNode processes all tool calls in parallel internally |
| **v2** (default) | Uses `Send` API to distribute individual tool calls across separate ToolNode instances. Better parallelism and per-tool-call HITL |

### 3. Graph Structure

Nodes: `agent`, `tools`, optional `pre_model_hook`, `post_model_hook`, `generate_structured_response`

The function builds a `StateGraph`, adds nodes, wires conditional edges via `should_continue`, returns `workflow.compile()`.

### 4. Anti-Stall via remaining_steps

`AgentState` includes `remaining_steps: RemainingSteps` (managed value). When `remaining_steps < 2` and tool calls exist, returns graceful message instead of `GraphRecursionError`.

### 5. Prompt Polymorphism

```python
Prompt = (
    SystemMessage | str |
    Callable[[StateSchema], LanguageModelInput] |
    Runnable[StateSchema, LanguageModelInput]
)
```

Strings become `SystemMessage` prepended to messages. Callables receive full state.

### 6. Structured Output

Separate LLM call **after** agent loop finishes — not in the tool-calling loop. The model re-reads the full conversation to generate structured output.

### 7. Tool Binding Detection

`_should_bind_tools` inspects model to check if tools already bound (via `RunnableBinding.kwargs["tools"]`). Supports OpenAI-style and Anthropic-style tool schemas.

## AgentState

```python
class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    remaining_steps: RemainingSteps    # managed value
    is_last_step: IsLastStep           # managed value
```
