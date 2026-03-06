# LangGraph — Checkpoint Conformance Suite

## Purpose

A standalone capability-based test harness that any third-party checkpointer implementation can run against to verify correctness.

## Capability Detection

```python
class Capability(str, Enum):
    PUT            # aput
    PUT_WRITES     # aput_writes
    GET_TUPLE      # aget_tuple
    LIST           # alist
    DELETE_THREAD  # adelete_thread
    DELETE_FOR_RUNS  # OPTIONAL
    COPY_THREAD      # OPTIONAL
    PRUNE            # OPTIONAL

BASE_CAPABILITIES = {PUT, PUT_WRITES, GET_TUPLE, LIST, DELETE_THREAD}
EXTENDED_CAPABILITIES = {DELETE_FOR_RUNS, COPY_THREAD, PRUNE}
```

Detection: checks if async method is overridden vs `BaseCheckpointSaver` default.

## Test Suites

### PUT (17 tests)
- Round-trip for channel_values (str, int, list, dict)
- channel_versions, versions_seen, metadata
- Namespace isolation (root `""` vs child `"child:abc"`)
- Multi-checkpoint same thread, thread isolation
- Parent_config tracking
- Incremental blob updates (only changed channels)

### PUT_WRITES (10 tests)
- Basic write visibility in pending_writes
- Multiple writes same task, multiple tasks same checkpoint
- task_id preserved, channel+value round-trip
- task_path accepted
- Idempotency (duplicate task_id+idx doesn't duplicate)
- Special channels (ERROR, INTERRUPT)
- Namespace isolation
- Writes absent in next checkpoint

### GET_TUPLE (10 tests)
- Non-existent thread → None
- Latest returned without checkpoint_id
- Exact checkpoint_id match
- Config structure, all Checkpoint fields present
- Metadata populated, parent_config correct
- Pending_writes visible, namespace filtering

### LIST
- Filter by thread, namespace, metadata (source)
- Before (pagination by checkpoint_id), limit
- Descending order (newest first)
- Empty result for unknown thread

### DELETE_THREAD
- Removes all checkpoints + writes for thread
- Does not affect other threads

### DELETE_FOR_RUNS
- Removes all checkpoints + writes for given run_ids

### COPY_THREAD
- Copies all from source to target thread_id
- Source remains intact

### PRUNE
- `"keep_latest"` retains only most recent per namespace
- `"delete"` removes all
- Verify correct retention counts

## Runner Pattern

```python
validate(saver_factory) -> CapabilityReport
    # For each capability:
    #   1. Create fresh saver instance
    #   2. Detect capabilities
    #   3. Run applicable test suite
    #   4. Aggregate (passed, failed, failures)
```
