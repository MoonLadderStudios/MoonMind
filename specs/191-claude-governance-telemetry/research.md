# Research: Claude Governance Telemetry

## Schema Boundary

Decision: Add governance telemetry contracts to `moonmind/schemas/managed_session_models.py` and export them from `moonmind/schemas/__init__.py`.

Rationale: Existing Claude stories use this module as the compact managed-session schema boundary. MM-349 depends on session, policy, decision, context, checkpoint, child-work, and surface records, so keeping evidence models adjacent avoids a premature split while preserving importable validation.

Alternatives considered: Create a separate `claude_governance_models.py` module. Rejected for this story because the current schema surface is still compact enough and downstream imports already centralize Claude managed-session contracts through `moonmind.schemas`.

## Payload-Light Storage Evidence

Decision: Represent central-plane evidence as store records and bounded artifact references, while runtime-local payload classes are named separately and rejected when embedded in default central-plane records.

Rationale: The source design requires MoonMind to preserve local-code residency and avoid centralizing source code, transcripts, file reads, checkpoint payloads, or local caches by default.

Alternatives considered: Store sampled payload snippets for audit convenience. Rejected because it violates the default payload-light model and would make source-code centralization ambiguous.

## Event Subscription And Envelope Shape

Decision: Add compact subscription and event-envelope models with closed event families and normalized event names for session, surface, policy, turn, work, decision, and child-work events.

Rationale: The design calls for append-only event streams and explicit subscription APIs. Closed names prevent unknown runtime values from becoming silent audit gaps.

Alternatives considered: Reuse individual surface, policy, context, and child-work event classes only. Rejected because MM-349 needs a cross-family envelope and subscription contract that can carry bounded governance evidence across families.

## Retention Evidence

Decision: Model retention as policy-controlled class records with explicit class names and values for hot session metadata, event logs, usage rollups, audit metadata, and checkpoint payload references.

Rationale: Retention must be policy-driven rather than hard-coded, and auditors need to see whether each class came from policy.

Alternatives considered: Expose retention values as free-form metadata on storage records. Rejected because it would not prove policy control or required class coverage.

## Telemetry Normalization

Decision: Model Claude OpenTelemetry observations as normalized managed-session metrics, event/log envelopes, and optional trace spans using closed metric and span names from the source design.

Rationale: The source design explicitly requires Claude OpenTelemetry to map into MoonMind's shared observability schema instead of inventing a parallel pipeline.

Alternatives considered: Treat OTel payloads as opaque export refs only. Rejected because MM-349 requires normalized metrics and spans to be asserted independently.

## Usage Rollups

Decision: Add usage rollup evidence keyed by session, optional group, user, workspace, runtime kind, provider mode, token direction, and optional child/team dimensions, with validation against child double counting.

Rationale: The design requires usage rollups by session, group, user, workspace, runtime kind, and provider, while prior child-work stories require child and team usage to remain inspectable without double counting.

Alternatives considered: Reuse `ClaudeChildWorkUsage` directly. Rejected because MM-349 needs higher-level rollup dimensions that apply to sessions, groups, users, workspaces, and providers.

## Test Strategy

Decision: Use focused schema unit tests plus one integration-style synthetic fixture flow test.

Rationale: This story defines runtime contracts at the schema boundary and can be validated without live Claude provider credentials. Unit tests cover invariants and invalid values; the boundary test proves the complete synthetic evidence flow remains payload-light.

Alternatives considered: Provider verification tests. Rejected because live Claude telemetry export is outside this story.
