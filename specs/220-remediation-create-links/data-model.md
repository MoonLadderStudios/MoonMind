# Data Model: Remediation Create Links

## ExecutionRemediationLink

Directed relationship from a remediation execution to the target execution it investigates.

Fields:

- `remediation_workflow_id`: Workflow ID of the remediation `MoonMind.Run`. Primary key and foreign key to `temporal_execution_sources.workflow_id`.
- `remediation_run_id`: Run ID of the remediation execution at create time.
- `target_workflow_id`: Workflow ID of the target `MoonMind.Run`. Indexed foreign key to `temporal_execution_sources.workflow_id`.
- `target_run_id`: Target run ID pinned at remediation create time.
- `mode`: Compact remediation mode. Defaults to `snapshot_then_follow` when omitted.
- `authority_mode`: Compact authority mode. Defaults to `observe_only` when omitted.
- `status`: Link lifecycle status for the persistence slice. Initial value is `created`.
- `trigger_type`: Optional trigger type copied from `task.remediation.trigger.type`.
- `created_at`: Create timestamp.
- `updated_at`: Last update timestamp.

Validation rules:

- `target.workflowId` is required.
- The target must exist and be a `MoonMind.Run`.
- The target must be visible to the creating owner.
- `target.runId`, when supplied, must match the current target run ID in this slice.
- Remediation links do not create dependency edges.
