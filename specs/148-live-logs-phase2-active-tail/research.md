# Research: Live Logs Phase 2 Active Tail

## Decision: Use structured event journal as the first merged-log synthesis source

**Rationale**: The journal is the durable normalized history defined by the Live Logs desired state and already carries run-global sequence, stream, text, kind, and session metadata. Rendering it first gives refreshed active pages the same visible facts that later structured timeline clients will use.

**Alternatives considered**: Prefer final merged artifact first; rejected because active runs often do not have final artifacts yet and terminal runs with session-aware journal rows would lose those rows. Prefer spool first; rejected because spool is a live transport and can contain less durable or stale active data.

## Decision: Keep spool as the active transport fallback

**Rationale**: The existing SSE path and tests already use `live_streams.spool`. Reading it for merged text when the journal is unavailable preserves the current UI lifecycle and keeps initial content independent from SSE success.

**Alternatives considered**: Require `/observability/events` for active loads; rejected because the current UI uses `/logs/merged` before opening SSE and this phase intentionally avoids a frontend rewrite.

## Decision: Keep artifact fallbacks after journal and spool

**Rationale**: Historical and partially migrated runs may only have final merged, legacy combined, stdout, stderr, or diagnostics artifacts. The endpoint must remain useful for those runs while active history improves.

**Alternatives considered**: Drop legacy artifacts once structured history exists; rejected because older records remain operator-visible and the current UI expects the merged endpoint to degrade gracefully.

## Decision: Render additive event fields conservatively

**Rationale**: Older clients only need human-readable text. The merged projection should expose stream headers and event text, while preserving kind/session context in labels where helpful without depending on a richer frontend row model.

**Alternatives considered**: Return JSON from `/logs/merged`; rejected because it would break the current merged-tail text consumer.
