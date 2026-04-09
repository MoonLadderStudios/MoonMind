# Data Model: Live Logs Phase 7 Hardening and Rollback

## 1. Router metrics

Phase 7 does not add a new persisted business entity. It adds best-effort operational events around existing router surfaces.

### 1.1 Summary metrics

| Metric | Type | When emitted | Tags |
| --- | --- | --- | --- |
| `livelogs.summary.latency` | timing | `/observability-summary` completes route business logic | `stream=livelogs` |

### 1.2 History metrics

| Metric | Type | When emitted | Tags |
| --- | --- | --- | --- |
| `livelogs.history.latency` | timing | `/observability/events` completes a successful history reconstruction | `stream=livelogs`, `source=journal\|spool\|artifacts` |
| `livelogs.history.source` | counter | `/observability/events` serves one history source | `stream=livelogs`, `source=journal\|spool\|artifacts` |
| `livelogs.history.error` | counter | `/observability/events` fails after authorization while loading history | `stream=livelogs` |

### 1.3 Stream metrics

Phase 7 keeps the existing SSE metrics:

| Metric | Type | Tags |
| --- | --- | --- |
| `livelogs.stream.connect` | counter | `stream=livelogs` |
| `livelogs.stream.disconnect` | counter | `stream=livelogs` |
| `livelogs.stream.error` | counter | `stream=livelogs` |

## 2. History source classification

`/observability/events` must classify the source used to reconstruct the response:

- `journal`: persisted `observability.events.jsonl`
- `spool`: shared append-only spool replay
- `artifacts`: synthesized fallback from stdout/stderr/diagnostics/session artifacts

This source is operational metadata only. It is not returned to the browser in the response body.

## 3. Structured-history rollback flag

| Config field | Dashboard key | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `live_logs_structured_history_enabled` | `liveLogsStructuredHistoryEnabled` | boolean | `true` | When `false`, the browser skips `/observability/events` and loads merged history directly. |

This flag is independent from:

- `live_logs_session_timeline_rollout`
- `log_streaming_enabled`

## 4. Frontend behavior

When `liveLogsStructuredHistoryEnabled=false`:

1. Live Logs still fetches summary first.
2. The history query for `/observability/events` is disabled.
3. The merged-tail fallback query becomes the primary historical load path.
4. Active runs may still upgrade to live SSE if the summary says streaming is available.
