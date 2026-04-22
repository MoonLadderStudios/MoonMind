# Data Model: Remediation Context Artifacts

## ExecutionRemediationLink.context_artifact_ref

Nullable pointer to the latest generated remediation context artifact for a remediation execution.

Validation rules:

- The value references a complete Temporal artifact when present.
- The referenced artifact is linked to the remediation execution with link type `remediation.context`.
- Regenerating context may replace the pointer with a newer artifact ID.

## Remediation Context Artifact

JSON artifact with:

- `schemaVersion`: currently `v1`.
- `remediationWorkflowId`: remediation execution workflow ID.
- `generatedAt`: UTC timestamp.
- `target`: pinned target workflow/run identity and compact execution metadata.
- `selectedSteps`: bounded selectors copied from the remediation request.
- `evidence`: artifact and task-run refs only; no raw artifact contents.
- `liveFollow`: requested mode and current support/cursor state.
- `policies`: authority, action, evidence, approval, and lock policy snapshots.
- `boundedness`: limits applied while generating the payload.

State transitions:

- `requested` -> `complete`: artifact is created and written successfully.
- `requested` -> failure: no context ref is recorded on the remediation link.
