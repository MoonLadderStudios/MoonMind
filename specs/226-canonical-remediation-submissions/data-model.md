# Data Model: Canonical Remediation Submissions

## Remediation Execution

A normal `MoonMind.Run` created with canonical remediation intent.

Key attributes:

- `workflow_id`: Logical remediation workflow identity.
- `run_id`: Current remediation run identity at creation.
- `initial_parameters.task.remediation`: Canonical remediation payload.

Validation rules:

- The remediation payload must be an object when present.
- Remediation execution creation must not require target success.

## Target Execution

The existing `MoonMind.Run` selected for remediation.

Key attributes:

- `workflow_id`: Required logical target identity.
- `run_id`: Current target run identity used when the caller omits `target.runId`.
- `workflow_type`: Must represent `MoonMind.Run`.
- `owner`: Must be visible to the caller.

Validation rules:

- The target must exist.
- The target must be visible to the creating principal.
- The target must not be the remediation execution itself.
- The target must not be another remediation execution in this slice.

## Pinned Target Run

The concrete target run snapshot stored at remediation create time.

Key attributes:

- `target.workflowId`: Logical target execution identity.
- `target.runId`: Concrete target run identity resolved or validated at creation.

Validation rules:

- If supplied, `target.runId` must match the current target run identity for this slice.
- If omitted, the service resolves the current target run and persists it before workflow start.

## Remediation Relationship

Durable directed relationship from remediation execution to target execution.

Key attributes:

- `remediation_workflow_id`
- `remediation_run_id`
- `target_workflow_id`
- `target_run_id`
- `mode`
- `authority_mode`
- `status`
- `trigger_type`
- `active_lock_scope`
- `active_lock_holder`
- `latest_action_summary`
- `outcome`
- `created_at`
- `updated_at`

Validation rules:

- Relationship creation is transactional with execution creation.
- Relationship rows do not create dependency edges.
- Outbound lookup returns targets for a remediation execution.
- Inbound lookup returns remediation executions for a target execution.
