# Data Model: Celery Chain Workflow Integration

## Entities

### SpecWorkflowRun
- **Purpose**: Represents a single execution of the Spec Kit automation initiated from MoonMind.
- **Key Fields**:
  - `id` (UUID): Primary identifier and foreign key for related records.
  - `feature_key` (string): Spec Kit branch identifier such as `001-celery-chain-workflow`.
  - `celery_chain_id` (string): Celery root task ID for traceability.
  - `status` (enum): `pending`, `running`, `succeeded`, `failed`, `cancelled`.
  - `phase` (enum): `discover`, `submit`, `apply`, `publish`, `complete`.
  - `branch_name` (string, nullable): Branch created during run.
  - `pr_url` (string, nullable): Pull request URL once published.
  - `codex_task_id` (string, nullable): Identifier returned by `codex cloud exec`.
  - `started_at` / `finished_at` (timestamp): Execution window.
  - `created_by` (UUID): Operator or system profile initiating the run.
  - `artifacts_path` (string): Filesystem location for JSONL logs and patches.
- **Relationships**: One-to-many with `SpecWorkflowTaskState`; optional one-to-one with `WorkflowCredentialAudit`.
- **Validation Rules**: `feature_key`, `status`, and `phase` required; transitions must follow sequential phase order; `pr_url` requires `branch_name`.

### SpecWorkflowTaskState
- **Purpose**: Stores status updates for each Celery task in the chain.
- **Key Fields**:
  - `id` (UUID): Primary key.
  - `workflow_run_id` (UUID, FK): References `SpecWorkflowRun`.
  - `task_name` (string): Canonical task identifier (`discover_next_phase`, `submit_to_codex`, etc.).
  - `status` (enum): `queued`, `running`, `succeeded`, `failed`, `skipped`.
  - `attempt` (int): Retry counter starting at 1.
  - `payload` (JSONB): Structured output (e.g., task ID, branch meta, error details).
  - `started_at` / `finished_at` (timestamp): Timing metadata.
- **Relationships**: Many-to-one with `SpecWorkflowRun`.
- **Validation Rules**: `task_name` unique per `workflow_run_id`+`attempt`; `payload` must contain a `code` field when status is `failed` to align with the API error schema.

### WorkflowCredentialAudit
- **Purpose**: Captures credential validations performed before executing downstream tasks.
- **Key Fields**:
  - `id` (UUID): Primary key.
  - `workflow_run_id` (UUID, FK): References `SpecWorkflowRun`.
  - `codex_status` (enum): `valid`, `invalid`, `expires_soon`.
  - `github_status` (enum): `valid`, `invalid`, `scope_missing`.
  - `checked_at` (timestamp): Validation time.
  - `notes` (text): Additional remediation guidance if invalid.
- **Relationships**: One-to-one with `SpecWorkflowRun`.
- **Validation Rules**: `checked_at` required; statuses must be consistent with generated notes (e.g., `invalid` requires explanation).

### WorkflowArtifact
- **Purpose**: References stored files associated with a run.
- **Key Fields**:
  - `id` (UUID): Primary key.
  - `workflow_run_id` (UUID, FK): References `SpecWorkflowRun`.
  - `artifact_type` (enum): `codex_logs`, `codex_patch`, `gh_push_log`, `gh_pr_response`.
  - `path` (string): Filesystem-relative path or signed URL.
  - `created_at` (timestamp): Insertion time.
- **Relationships**: Many-to-one with `SpecWorkflowRun`.
- **Validation Rules**: `artifact_type` + `path` unique per `workflow_run_id`; paths must reside within configured artifact root.

## State Transitions

1. `SpecWorkflowRun.status`: `pending` → `running` when discovery task starts → `succeeded` upon PR publication → `failed` when any task returns terminal failure.
2. `SpecWorkflowRun.phase`: sequential progression `discover` → `submit` → `apply` → `publish` → `complete`. Backward transitions only allowed during retries, at which point phase resets to the retried task.
3. `SpecWorkflowTaskState.status`: `queued` → `running` → (`succeeded` | `failed`). Retries append new rows with incremented `attempt` rather than mutating prior entries.

## Derived Data & Indexing

- Index `SpecWorkflowRun` on `feature_key`, `status`, and `created_by` for UI filters.
- Partial index on `SpecWorkflowTaskState` where `status = 'failed'` to accelerate failure dashboards.
- Unique constraint on (`workflow_run_id`, `task_name`, `attempt`).
