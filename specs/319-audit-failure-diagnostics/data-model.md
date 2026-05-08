# Data Model: Skills On Demand Audit and Diagnostics

## SkillsOnDemandAuditEvent

Purpose: Bounded audit/observability record emitted once for each Skills On Demand query or request attempt.

Fields:

- `event_type`: Required enum. `skills_on_demand.query` or `skills_on_demand.request`.
- `workflow_id`: Required when available. Workflow identifier for the managed run.
- `run_id`: Optional run identifier.
- `step_id`: Optional step identifier.
- `runtime_id`: Optional managed runtime identifier.
- `current_snapshot_id`: Optional active snapshot identifier for query events.
- `parent_snapshot_id`: Optional parent active snapshot identifier for request events.
- `requested_skills`: Request-only ordered list of requested Skill names after normalization.
- `query_hash`: Query-only hash of normalized query text.
- `result_count`: Query-only count of returned metadata results.
- `result`: Request-only result value: `activated`, `denied`, `requires_approval`, or `no_change`.
- `result_code`: Optional stable result or failure code.
- `denied`: Query-focused boolean denial marker.
- `denial_code`: Optional query denial code.
- `derived_snapshot_id`: Optional derived snapshot identifier for activated requests.
- `manifest_ref`: Optional compact manifest reference for activated requests.
- `diagnostics_ref`: Optional controlled reference to detailed diagnostic evidence.

Validation rules:

- `event_type` determines which query-only or request-only fields are required.
- Raw natural-language query text is never stored; `query_hash` is required for query attempts that include query text.
- `requested_skills` contains normalized Skill names only, not Skill bodies, content refs, or source paths.
- Refs must be compact identifiers or artifact locators appropriate for operator-visible diagnostics.
- Secret-like values, full Skill bodies, arbitrary database locations, and unrestricted artifact refs are invalid.

## SkillsOnDemandFailureDiagnostic

Purpose: Safe structured failure evidence returned or referenced when a query, request, materialization, or runtime refresh fails.

Fields:

- `status`: Required value `denied`.
- `code`: Required stable failure code.
- `message`: Required safe human-readable message.
- `current_snapshot_ref`: Optional current active snapshot ref when safe and relevant.
- `diagnostics_ref`: Optional controlled reference to larger diagnostic artifact.

Validation rules:

- `code` must be one of the documented Skills On Demand failure classes when the class applies.
- `message` must be single-purpose and secret-free.
- `current_snapshot_ref` must not imply activation of a failed derived snapshot.
- `diagnostics_ref` must point only to controlled diagnostic evidence.

## SkillsOnDemandDiagnosticArtifact

Purpose: Larger controlled evidence for failures or denied requests that should not be embedded in high-cardinality event fields or workflow-visible metadata.

Fields:

- `diagnostics_ref`: Stable ref returned by the event or failure diagnostic.
- `failure_code`: Stable failure code.
- `summary`: Short safe description.
- `context`: Bounded key/value context such as runtime id, step id, snapshot id, requested Skill names, and materialization phase.
- `redaction_status`: Marker proving secret/body/path redaction was applied.

Validation rules:

- Artifact content must not contain full Skill bodies, secret values, raw high-cardinality query text, arbitrary database refs, or unrestricted artifact refs.
- Artifact content may include normalized requested Skill names and compact snapshot/materialization refs.

## State Transitions

```text
query received
  -> query event emitted
  -> ok result OR denied failure diagnostic

request received
  -> request validation
  -> request event emitted
  -> no_change OR denied failure diagnostic OR activated derived snapshot

materialization attempted
  -> activated event when verified
  -> materialization_failed diagnostic and previous snapshot preserved on failure

runtime refresh attempted
  -> activated event when refresh is ready
  -> runtime_refresh_failed diagnostic and previous snapshot preserved on failure
```

Invariant: failures never make a derived snapshot the active snapshot.
