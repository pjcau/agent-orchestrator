# LangGraph — Serialization System

## Protocol Hierarchy

```
SerializerProtocol (runtime_checkable)
    dumps_typed(obj) -> (str, bytes)
    loads_typed((str, bytes)) -> Any
    ├── JsonPlusSerializer          # PRIMARY
    └── EncryptedSerializer         # Wrapper with CipherProtocol
```

## JsonPlusSerializer (Primary)

Uses `ormsgpack` (Rust-backed) as main encoding.

### dumps_typed() Dispatch

| Object | Type Tag | Encoding |
|--------|----------|----------|
| `None` | `"null"` | empty bytes |
| `bytes` | `"bytes"` | raw bytes |
| `bytearray` | `"bytearray"` | raw bytes |
| anything else | `"msgpack"` | ormsgpack binary |
| fallback | `"pickle"` | pickle (if enabled) |

### msgpack Extension Codes

| Code | Name | Handles |
|------|------|---------|
| 0 | `EXT_CONSTRUCTOR_SINGLE_ARG` | UUID, Decimal, set, frozenset, deque, IP, Enum, secrets |
| 1 | `EXT_CONSTRUCTOR_POS_ARGS` | Path, re.Pattern, Send, tuple |
| 2 | `EXT_CONSTRUCTOR_KW_ARGS` | namedtuple, dataclass, Item |
| 3 | `EXT_METHOD_SINGLE_ARG` | datetime (fromisoformat), timezone |
| 4 | `EXT_PYDANTIC_V1` | Pydantic v1 models (.dict()) |
| 5 | `EXT_PYDANTIC_V2` | Pydantic v2 models (.model_dump()) |
| 6 | `EXT_NUMPY_ARRAY` | numpy arrays (dtype, shape, order, bytes) |

Each ext encodes `(module, name, arg/args/kwargs)` and deserializes via `importlib.import_module(module).name(...)`.

### Security — msgpack Allowlist

- `LANGGRAPH_STRICT_MSGPACK=true` → strict mode (block anything not safe)
- `SAFE_MSGPACK_TYPES` — always allowed: datetime, UUID, Decimal, set, frozenset, deque, IP, Path, ZoneInfo, re.compile, LangChain message types
- `allowed_msgpack_modules=True` → allow all (with warning logs)
- `allowed_msgpack_modules=None` → block anything not in safe types
- `allowed_msgpack_modules=[...]` → explicit allowlist merged with safe types

### Serde Event Hooks

Global listener registry receiving `SerdeEvent` dicts:
- `"msgpack_unregistered_allowed"` — type allowed but not in allowlist
- `"msgpack_blocked"` — type blocked by strict mode
- `"msgpack_method_blocked"` — method blocked

Used for observability/auditing of serialization.

## EncryptedSerializer

Wraps any `SerializerProtocol`. Type tag becomes `"{original_type}+{ciphername}"` (e.g., `"msgpack+aes"`).

```python
# Factory
EncryptedSerializer.from_pycryptodome_aes(key)
# Key from LANGGRAPH_AES_KEY env var (16/24/32 bytes)
# Uses AES-EAX mode, nonce (16B) + tag (16B) prepended to ciphertext
```

Detects unencrypted data by absence of `+` in type tag (backward compatible).

## Allowlist Builder (_serde.py)

- `curated_core_allowlist()` — hardcodes 14 LangChain message types
- `collect_allowlist_from_schemas(schemas, channels)` — recursively walks type annotations (TypedDict, Pydantic, dataclass, enum, Union, Annotated)
- `apply_checkpointer_allowlist(checkpointer, allowlist)` — applies via `with_allowlist`
