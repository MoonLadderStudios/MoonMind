# Contract: Active Merged Tail

## `GET /api/task-runs/{id}/logs/merged`

Returns `text/plain`.

Required behavior:
- Return journal-rendered merged text when `observabilityEventsRef` resolves to at least one valid event row.
- Return spool-rendered merged text when no usable journal content exists and the workspace spool has valid rows for the run.
- Return final or legacy artifacts when no usable active structured source exists.
- Return split stdout/stderr fallback when only stream-specific artifacts exist.
- Return 404 only when no source can produce visible content.

Response headers for synthesized responses:
- `X-Merged-Synthesized: true`
- `X-Merged-Order-Source: journal | spool | legacy-log-artifact | artifact-fallback`

Final merged artifacts may still be served directly as file responses when no usable journal or spool content exists.

## `GET /api/task-runs/{id}/observability-summary`

Returns a JSON object with `summary`.

Required behavior:
- `summary.supportsLiveStreaming` is `true` only when the run is active and live capable.
- `summary.liveStreamStatus` is `available`, `unavailable`, or `ended`.
- `summary.observabilityEventsRef` is present when recorded.
- `summary.sessionSnapshot` includes the latest session identity fields available from the session store or managed-run record.

## `GET /api/task-runs/{id}/logs/stream`

Existing contract preserved:
- Active capable runs return SSE.
- Active incapable runs return 400.
- Terminal runs return 410.
- Missing records return 404.
