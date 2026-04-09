# Data Model: Live Logs History Events

## Historical Observability Query

Phase 3 hardens the existing historical retrieval surface:

- `task_run_id`
- `since`: optional sequence floor for historical event retrieval
- `limit`: bounded response size
- `stream`: optional repeated filter across `stdout`, `stderr`, `system`, `session`
- `kind`: optional repeated filter across canonical observability event kinds

Filtering semantics:

- `since` excludes rows with `sequence <= since`
- `stream` matches only rows whose canonical `stream` is in the requested set
- `kind` matches only rows whose canonical `kind` is in the requested set
- when both `stream` and `kind` are present, a row must satisfy both filters

## Historical Response Envelope

The response remains a simple envelope:

- `events`: list of canonical `RunObservabilityEvent` rows
- `truncated`: whether the response was trimmed to the requested limit
- `sessionSnapshot`: latest known bounded session identity when available

## Session Snapshot Precedence

For summary and historical responses:

1. prefer the managed-session record snapshot when available
2. otherwise use the durable session fields persisted on `ManagedRunRecord`
3. otherwise omit the session snapshot

This preserves truthful session context for completed or partially migrated runs without depending on live container state.

## Historical Source Priority

Historical retrieval remains additive and deterministic:

1. durable `observability.events.jsonl`
2. live spool history when the durable journal is absent
3. artifact-backed synthesis from diagnostics/stdout/stderr and continuity artifacts

Only one source tier should win for a given request.

## SSE Compatibility

`/logs/stream` continues to serialize `RunObservabilityEvent` rows with the same field names and optional session metadata used by historical retrieval. Phase 3 does not introduce a second live-only event schema.
