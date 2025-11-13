# Data Model: Celery Chain Workflow Integration

## Entity: SpecWorkflowRun
- **Purpose**: Canonical record per MoonMind-triggered Spec Kit automation run; stores identifiers, chain metadata, operator inputs, and aggregated artifacts.
- **Primary Identifier**: `id` (UUID).
- **Key Attributes**:
  - `feature_key` (string, required) – Spec identifier or repo/task handle used for idempotent branch naming.
  - `requested_by_user_id` (UUID, required) – MoonMind user who initiated the run.
  - `repository` (string, required) – fully-qualified repo slug (`org/repo`).
  - `branch_name` (string, nullable) – deterministic branch derived from `feature_key`; set once discovery succeeds.
  - `pull_request_url` (string, nullable) – PR created/updated by the publish task.
  - `celery_chain_id` (string, required) – Celery `AsyncResult.id` for the head of the chain.
  - `status` (enum: `pending`, `running`, `succeeded`, `failed`, `no_work`, `cancelled`, `retrying`).
  - `current_task_name` (string, nullable) – friendly name of the active Celery task during execution.
  - `codex_task_id` (string, nullable) – identifier returned by Codex submission task.
  - `codex_logs_path` (string, nullable) – path to JSONL stream captured under `var/artifacts/spec_workflows/<run_id>/codex.jsonl`.
  - `credential_audit_id` (UUID, nullable) – FK to `WorkflowCredentialAudit` summarizing checks performed for the run.
  - `created_at` / `updated_at` / `completed_at` (timestamps, required/nullable) – lifecycle markers for SLA tracking.
- **Relationships**:
  - One-to-many with `SpecWorkflowTaskState` (each run has many task states).
  - One-to-many with `WorkflowArtifact` (artifacts keyed by type) and optional one-to-one with `WorkflowCredentialAudit`.
- **Validation Rules**:
  - `branch_name` must follow `<feature-key>/<run-suffix>` slug pattern and remain immutable once PR step starts.
  - `status` transitions must obey the state machine defined below (e.g., cannot jump from `pending` to `succeeded` without `running`).

## Entity: SpecWorkflowTaskState
- **Purpose**: Tracks per-task execution data emitted by Celery for UI monitoring and audit.
- **Primary Identifier**: composite (`run_id`, `task_name`, `attempt` #) or surrogate `id`.
- **Key Attributes**:
  - `run_id` (UUID, FK to `SpecWorkflowRun.id`).
  - `task_name` (enum/string: `discover`, `submit`, `apply`, `publish`, `finalize`, `retry-hook`).
  - `state` (enum: `waiting`, `received`, `started`, `succeeded`, `failed`, `retrying`).
  - `message` (JSON / text) – structured payload describing result or failure context.
  - `artifact_paths` (JSON array) – relative paths to artifacts produced by the task (e.g., Codex logs, patches).
  - `started_at`, `finished_at` (timestamps, nullable) – used for runtime charts.
  - `retry_count` (integer, default 0) – increments when Celery retries the task.
- **Relationships**:
  - Many-to-one with `SpecWorkflowRun`.
- **Validation Rules**:
  - Each new `state` entry for a (`run_id`, `task_name`) must have `started_at` when entering `started` and `finished_at` when final states occur.
  - `retry_count` increments monotonically; `state=retrying` requires `retry_count > 0`.

## Entity: WorkflowCredentialAudit
- **Purpose**: Records which secrets (Codex, GitHub) were validated for a run and any problems encountered.
- **Primary Identifier**: `id` (UUID).
- **Key Attributes**:
  - `run_id` (UUID, FK) – optional link back to `SpecWorkflowRun`.
  - `codex_status` (enum: `passed`, `failed`, `skipped`).
  - `codex_checked_at` (timestamp, nullable).
  - `codex_message` (text) – remediation guidance on failure.
  - `github_status` (enum: `passed`, `failed`, `skipped`).
  - `github_checked_at` (timestamp, nullable).
  - `github_message` (text).
  - `environment_snapshot` (JSON) – captures version numbers, queue/worker metadata, commit SHAs.
- **Relationships**:
  - Optional one-to-one with `SpecWorkflowRun` (a run references the latest audit row).
- **Validation Rules**:
  - When a status is `failed`, the corresponding `*_message` must be populated.
  - At least one of Codex/GitHub statuses must be `passed` for the run to proceed beyond discovery.

## Entity: WorkflowArtifact
- **Purpose**: Describes artifacts generated while executing the chain for later download/debugging.
- **Primary Identifier**: `id` (UUID) or composite of (`run_id`, `artifact_type`).
- **Key Attributes**:
  - `run_id` (UUID, FK).
  - `artifact_type` (enum: `codex_logs`, `codex_patch`, `apply_output`, `pr_payload`, `retry_context`).
  - `path` (string) – filesystem/Object Store path relative to `var/artifacts/spec_workflows/<run_id>` or signed URL.
  - `content_type` (string) – MIME descriptor for download.
  - `size_bytes` (integer, nullable) – optional for quota tracking.
  - `digest` (string, nullable) – checksum for integrity validation.
  - `created_at` (timestamp).
- **Relationships**:
  - Many-to-one with `SpecWorkflowRun`.
- **Validation Rules**:
  - (`run_id`, `artifact_type`) should be unique when `artifact_type` is singular (e.g., only one `codex_logs` per run) but may allow multiples for repeating artifacts (e.g., multiple `retry_context` entries) tracked via suffix.

## Lifecycle & State Transitions

### SpecWorkflowRun
1. `pending` → `running`: triggered when the Celery chain is enqueued and discovery task starts.
2. `running` → `no_work`: discovery reported no actionable phases; no downstream tasks execute.
3. `running` → `failed`: any task raises unrecoverable error; `current_task_name` captures the failing task.
4. `running` → `succeeded`: finalize task completes and PR URL recorded.
5. `failed` → `retrying`: operator triggers retry/resume; `celery_chain_id` updated to the new chain.
6. `retrying` → (`running` | `failed` | `succeeded`) depending on retry outcome.
7. Any terminal state sets `completed_at` and freezes mutable fields (branch, PR URL).

### SpecWorkflowTaskState
- `waiting` → `received` → `started` → (`succeeded` | `failed`).
- On Celery retry the task re-enters `waiting`/`received` with `retry_count+1` and optional `state=retrying` entry for audit clarity.

### WorkflowCredentialAudit
- `codex_status`/`github_status` start as `skipped`.
- After validation, status flips to `passed` or `failed`; subsequent retries append new audit rows if re-validation occurs.

### WorkflowArtifact
- Created whenever a task streams outputs; artifacts linked to subsequent retries either overwrite the same row (if `artifact_type` unique) or create new rows suffixed with attempt identifiers for traceability.
