# Research: Auth Operator Diagnostics

## OAuth Session Projection

Decision: Extend the existing OAuth session response with a compact `profile_summary` when the referenced Provider Profile exists.

Rationale: The OAuth session API already owns enrollment status, timestamps, terminal transport refs, and failure redaction. Joining the selected profile in the existing request path gives operators the registered profile summary without exposing credential files or raw auth volumes.

Alternatives considered: Add a new diagnostics endpoint; rejected because it would duplicate the existing session fetch surface for this single-story projection.

## Managed Codex Launch Diagnostics

Decision: Attach `authDiagnostics` metadata to the managed session launch handle in the Temporal activity boundary after materialization and after controller launch succeeds.

Rationale: The activity receives both the selected profile payload and the shaped launch request. This boundary can report selected profile refs, auth mount target, Codex home path, readiness, and ownership without adding persistent storage or raw credential data to workflow history.

Alternatives considered: Persist diagnostics in `CodexManagedSessionRecord`; deferred because this story only requires operator-visible launch/session metadata and artifact/log refs already exist for durable evidence.

## Failure Classification

Decision: Sanitize launch/materialization failure text through the existing operator-summary sanitizer and expose component ownership in the raised activity error.

Rationale: Failed launches currently propagate a sanitized activity error. Adding owner classification preserves the design boundary and avoids leaking token-like values or auth paths.

Alternatives considered: Return a failed handle instead of raising; rejected because the current activity contract uses exceptions for launch failures.

## Task Execution Evidence

Decision: Reuse existing session artifact/log/summary/diagnostics refs as the durable execution evidence model and avoid exposing auth volumes or runtime homes as artifacts.

Rationale: The source design explicitly treats logs, summaries, diagnostics, and continuity artifacts as the audit truth. Existing managed-session records already contain refs for those surfaces.

Alternatives considered: Add new artifact types for auth homes; rejected as out of scope and explicitly disallowed by the source design.
