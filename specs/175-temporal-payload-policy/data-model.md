# Data Model: Temporal Payload Policy

## Compact Temporal Mapping

- **Owner**: `moonmind/schemas/temporal_payload_policy.py`
- **Purpose**: Shared validation for approved `metadata` and `providerSummary` escape hatches.
- **Shape**: JSON object containing strings, numbers, booleans, nulls, arrays, and nested objects.
- **Bounds**:
  - raw `bytes` are rejected
  - individual strings are capped
  - serialized JSON mapping size is capped
  - unsupported Python object types are rejected
- **Large payload rule**: transcripts, diagnostics bodies, summaries, checkpoints, provider payloads, and binary outputs use artifact refs.

## Affected Boundary Models

- `AgentRunHandle.metadata`
- `AgentRunStatus.metadata`
- `AgentRunResult.metadata`
- Codex managed-session request/response metadata fields under `_CodexManagedSessionRemoteContract`
- Integration `providerSummary` fields in Temporal API/signal models

## Explicit Binary Serializer

- `Base64Bytes` remains the explicit typed serializer for binary activity fields.
- It serializes as base64 strings on JSON dumps and accepts legacy list/int or cleartext string compatibility inputs at the typed boundary.
