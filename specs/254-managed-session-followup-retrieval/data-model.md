# Data Model: Managed-Session Follow-Up Retrieval

## Overview

MM-506 extends the managed-session retrieval contract from automatic pre-turn retrieval to explicit session-initiated follow-up retrieval. The story does not introduce new persistent storage. It defines the runtime-visible entities and validation rules that the existing retrieval service, gateway surface, and runtime adapters must share.

## Entities

### ManagedSessionRetrievalCapability

Represents the runtime-facing capability signal delivered to a managed session.

Fields:
- `enabled`: Boolean indicating whether follow-up retrieval is available for the session.
- `request_surface`: Runtime-neutral description of the MoonMind-owned retrieval path the session must use.
- `reference_data_notice`: Explicit reminder that retrieved content is reference data, not instructions.
- `allowed_filters`: Bounded set or description of supported retrieval filter keys.
- `max_top_k`: Maximum allowed retrieval result count for a single request.
- `overlay_policy_modes`: Allowed overlay policy values.
- `budget_policies`: Optional policy-bounded budget fields such as token and latency budgets.
- `disabled_reason`: Explicit denial reason when retrieval is unavailable.

Validation rules:
- `enabled=false` requires a non-empty `disabled_reason`.
- `enabled=true` requires non-empty `request_surface` and `reference_data_notice`.
- Capability semantics must be runtime-neutral even when the exact adapter surface differs.

### FollowUpRetrievalRequest

Represents one session-initiated retrieval request sent through a MoonMind-owned surface.

Fields:
- `query`: Required non-empty retrieval query.
- `filters`: Optional repository and source-scope filters within policy.
- `top_k`: Optional bounded result limit.
- `overlay_policy`: Retrieval overlay mode, constrained to supported values.
- `budgets`: Optional bounded budget overrides allowed by policy.
- `transport_preference`: Optional hint when multiple MoonMind-owned transports are available.

Validation rules:
- `query` must be present and non-empty.
- `top_k`, when present, must remain within the allowed range.
- Unsupported filter keys, overlay modes, or budget keys are rejected explicitly.
- Requests outside policy are denied deterministically rather than silently adjusted.

### FollowUpRetrievalResult

Represents the machine-readable and text output returned to the managed runtime.

Fields:
- `context_pack`: Structured retrieval result with retrieved items, filters, budgets, usage, transport, timestamp, and telemetry ID.
- `context_text`: Text form intended for immediate runtime consumption.
- `item_count`: Count of returned items.
- `truncated`: Whether output had to be truncated to remain bounded.
- `artifact_ref`: Optional durable artifact/reference if the retrieval operation is published for later verification.

Validation rules:
- Successful results include both machine-readable retrieval output and text output.
- `item_count` matches the number of returned context items.
- `transport` remains one of the supported MoonMind-owned modes.

### FollowUpRetrievalEvidence

Represents durable observability for one retrieval attempt.

Fields:
- `mode`: `session_initiated` for this story.
- `transport`: Actual retrieval transport used.
- `filters`: Applied filters after validation.
- `budgets`: Effective budgets used for the request.
- `usage`: Observed token/latency usage.
- `status`: `fulfilled`, `denied`, or `failed`.
- `reason`: Explicit denial or failure reason when applicable.
- `artifact_ref`: Durable context reference when published.

Validation rules:
- Denied and failed outcomes require a non-empty `reason`.
- Evidence must stay compact and point to durable refs/artifacts for large bodies.

## Relationships

- One `ManagedSessionRetrievalCapability` governs many `FollowUpRetrievalRequest` attempts during a session.
- Each `FollowUpRetrievalRequest` yields either one `FollowUpRetrievalResult` or one denied/failed `FollowUpRetrievalEvidence` outcome.
- `FollowUpRetrievalResult` and `FollowUpRetrievalEvidence` share the same retrieval transport and budget context.

## State Transitions

1. Capability resolved: retrieval is either enabled with explicit guidance or disabled with an explicit reason.
2. Request submitted: the managed session sends a bounded request through a MoonMind-owned surface.
3. Request validated: MoonMind accepts or denies the request based on policy and supported contract fields.
4. Retrieval fulfilled: MoonMind returns `ContextPack` metadata plus text output and records durable evidence.
5. Retrieval denied or failed: MoonMind returns a deterministic reason and records compact evidence.

## Invariants

- Follow-up retrieval remains MoonMind-owned rather than a runtime-specific direct database integration.
- Capability signalling and request validation are explicit; enablement is never inferred implicitly.
- The same contract must remain usable by Codex and future managed runtimes.
- Large retrieval bodies remain behind durable artifacts or bounded text surfaces rather than unbounded workflow payloads.
