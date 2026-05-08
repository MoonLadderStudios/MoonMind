# Data Model: Canonical Remediation Submissions

**Traceability**: Jira issue `MM-617`; feature path `specs/317-canonical-remediation-submissions`.

## Remediation Run

Represents the normal MoonMind run created to investigate or repair another execution.

Fields relevant to this story:
- `workflowId`: logical identity for the remediation run.
- `runId`: concrete run identity for the remediation run.
- `task.remediation`: nested canonical remediation metadata preserved in the run's task payload.

Validation rules:
- The run must be a normal MoonMind run.
- The run must not be created when remediation validation fails.

## Target Execution

Represents the logical execution selected for remediation.

Fields relevant to this story:
- `workflowId`: required target identity.
- `runId`: current target run identity resolved and pinned at remediation creation.
- `taskRunIds`: optional bounded selected task-run IDs that must belong to the target execution when supplied.

Validation rules:
- Target workflow ID is required and must refer to a visible MoonMind run.
- Run IDs cannot be used where workflow IDs are required.
- Target run ID, when supplied, must match the current target run.
- Self-targeting is rejected.
- Nested remediation targets are rejected unless a future explicit policy allows them.

## Remediation Relationship

Durable directed link from remediation run to target execution.

Fields relevant to this story:
- `remediationWorkflowId`
- `remediationRunId`
- `targetWorkflowId`
- `targetRunId`
- `mode`
- `authorityMode`
- `status`
- `triggerType`
- `activeLockScope`
- `activeLockHolder`
- `latestActionSummary`
- `resolution`
- `contextArtifactRef`
- `createdAt`
- `updatedAt`

Relationships:
- One remediation run has one target relationship in this story.
- A target execution may have many inbound remediation relationships.
- Remediation relationships are separate from dependency relationships.

State transitions:
- Created relationship starts in `created` status.
- Later remediation lifecycle stories may update status, lock, action, resolution, and artifact fields.
- This story requires the fields to be persisted and queryable, not all later lifecycle transitions.
