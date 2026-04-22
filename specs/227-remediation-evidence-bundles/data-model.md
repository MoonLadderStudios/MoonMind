# Data Model: Remediation Evidence Bundles

## Remediation Context Artifact

- `schemaVersion`: context schema version, currently `v1`.
- `remediationWorkflowId`: remediation execution identity.
- `target`: pinned target workflow/run identity plus current-at-build title, summary, state, and close status.
- `selectedSteps`: bounded selected step/task-run selectors.
- `evidence`: context-declared artifact refs and taskRunIds.
- `liveFollow`: optional follow mode, support flag, taskRunId, and resume cursor.
- `policies`: authority, action, evidence, approval, and lock policy snapshots.
- `boundedness`: explicit max tail lines/taskRunIds and flags proving raw bodies are excluded.

Validation rules:
- Must be linked to the remediation execution as `remediation.context`.
- Must not include raw log bodies, artifact contents, presigned URLs, storage keys, local paths, or secret-like raw fields.
- Must bound taskRunIds and tail lines.

## Evidence Reference

- `artifact_id`/`artifactId`: artifact identifier, not a storage grant.
- `taskRunId`: task-run identifier declared by the context.
- `kind`: optional semantic marker such as input or target evidence.

Validation rules:
- Reads are allowed only when refs are declared in the linked context.
- Artifact and task-run policy checks still apply.

## Live Follow Cursor

- `sequence`: last observed event sequence.

Validation rules:
- Used only when live follow is supported and policy-allowed.
- Cursor is compact and persisted outside raw log bodies.

## Target Health Guard Snapshot

- `workflow_id`: target execution identity.
- `pinned_run_id`: run ID persisted on the remediation link.
- `current_run_id`: current run ID from the target execution projection.
- `state`: current target state.
- `close_status`: current target close status.
- `title` and `summary`: bounded current target display context.
- `target_run_changed`: true when current run differs from the pinned run.

Validation rules:
- Must be read from current target state immediately before action request preparation.
- Does not execute the action.
