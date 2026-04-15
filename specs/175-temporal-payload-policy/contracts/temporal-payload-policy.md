# Contract: Temporal Payload Policy

## Compact Metadata Contract

Temporal-facing metadata and provider-summary fields:

- MUST be JSON objects.
- MUST NOT contain raw bytes at any nesting level.
- MUST NOT carry large transcripts, diagnostics bodies, summaries, checkpoints, or provider response bodies inline.
- MAY carry compact artifact refs such as `summaryRef`, `checkpointRef`, `diagnosticsRef`, `resultRef`, and `payloadArtifactRef`.
- MAY carry compact annotation fields such as status, reason, source, attempt, and runtime identifiers.

## Binary Contract

Nested binary data in JSON-shaped Temporal payloads must use explicit typed serializers such as `Base64Bytes`. Larger binary outputs must be stored as artifacts and referenced by ID/ref.
