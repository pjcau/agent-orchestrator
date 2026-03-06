# LangGraph — Auth & Encryption

## Auth System

Server-side authentication/authorization framework using decorator-based handler registration.

### Setup

```python
from langgraph_sdk.auth import Auth

auth = Auth()

@auth.authenticate
async def authenticate(authorization: str) -> MinimalUserDict:
    # Verify token, return user info
    return {"identity": "user-123", "permissions": ["read", "write"]}
```

### Resource-Level Authorization

```python
@auth.on.threads.create
async def allow_create(ctx: AuthContext, value: ThreadsCreate):
    if "write" not in ctx.user.permissions:
        return False  # 403
    return None  # allow

@auth.on.store.search
async def filter_store(ctx: AuthContext, value: StoreSearch):
    return {"namespace": {"$prefix": f"user:{ctx.user.identity}"}}  # metadata filter
```

### Handler Resolution Hierarchy

1. Exact resource + action match (e.g., `threads.create`)
2. Resource-level match (e.g., `threads.*`)
3. Global handler (`*.*`)
4. If none match → request **accepted** (permissive default)

### Handler Return Semantics

| Return | Meaning |
|--------|---------|
| `None` / `True` | Accept |
| `False` | Reject (403) |
| `FilterType` (dict) | Accept with metadata filtering |

### Resource Types & Actions

| Resource | Actions |
|----------|---------|
| `threads` | create, read, update, delete, search |
| `assistants` | create, read, update, delete, search |
| `crons` | create, read, update, delete, search |
| `store` | put, get, search, delete, list_namespaces |

## Encryption System

At-rest encryption for checkpoints and metadata.

### Setup

```python
from langgraph_sdk.encryption import Encryption

encryption = Encryption()

@encryption.context
async def get_context(user):
    return {"tenant_id": user.identity}

@encryption.encrypt.blob
async def encrypt_blob(data: bytes, context: dict) -> bytes:
    return aes_encrypt(data, key_for(context["tenant_id"]))

@encryption.decrypt.blob
async def decrypt_blob(data: bytes, context: dict) -> bytes:
    return aes_decrypt(data, key_for(context["tenant_id"]))

@encryption.encrypt.json
async def encrypt_json(data: dict, context: dict) -> dict:
    return {k: encrypt_value(v) for k, v in data.items()}

@encryption.decrypt.json
async def decrypt_json(data: dict, context: dict) -> dict:
    return {k: decrypt_value(v) for k, v in data.items()}
```

### Constraints

- JSON encryptors must **preserve keys** (no adding/removing)
- Only values can be encrypted
- This enables SQL JSONB merge operations on encrypted data

### Built-in AES Support

```python
EncryptedSerializer.from_pycryptodome_aes(key)
# LANGGRAPH_AES_KEY env var (16/24/32 bytes)
# AES-EAX mode, nonce (16B) + tag (16B) prepended
```
