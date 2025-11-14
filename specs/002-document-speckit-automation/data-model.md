# Data Model: Spec Kit Automation Pipeline

## Entity Overview

| Entity | Purpose | Key Relationships |
|--------|---------|-------------------|
| SpecAutomationRun | Represents a single automation execution request and its lifecycle state. | Aggregates SpecAutomationTaskState entries; references AutomationArtifact records; links to AgentConfiguration snapshot. |
| SpecAutomationTaskState | Tracks an individual phase (prepare, clone, specify, plan, tasks, commit, PR, cleanup) within a run. | Belongs to one SpecAutomationRun. |
| AutomationArtifact | Describes persisted outputs (logs, diffs, status files) produced during a run. | References owning SpecAutomationRun and, optionally, the originating SpecAutomationTaskState. |
| AgentConfiguration | Captures the agent backend selection and parameters applied to a run. | Referenced by SpecAutomationRun; immutable snapshot per run. |

## SpecAutomationRun

- **Identifiers**
  - `run_id` (UUID) – primary identifier shared across Celery tasks and API responses.
  - `external_ref` (string, optional) – client-provided correlation ID for scheduling integrations.
- **Attributes**
  - `repository` (string) – GitHub org/repo slug targeted by the run.
  - `branch_name` (string) – feature branch created for the run (e.g., `speckit/{date}/{token}`) when changes exist.
  - `base_branch` (string) – upstream branch the repo was cloned from (defaults to `main`).
  - `status` (enum) – `queued`, `in_progress`, `succeeded`, `failed`, `no_changes`.
  - `result_summary` (string) – human-readable completion message (e.g., PR URL, failure reason).
  - `requested_spec_input` (text) – the specification text passed to `speckit.specify`.
  - `started_at`, `completed_at` (timestamps) – lifecycle tracking.
  - `worker_hostname` (string) – Celery worker host that orchestrated the run.
  - `job_container_id` (string) – Docker container identifier for traceability (cleared after cleanup).
- **Relationships**
  - 1-to-many with `SpecAutomationTaskState`.
  - 1-to-many with `AutomationArtifact`.
  - 1-to-1 with `AgentConfiguration` snapshot (captured at run start).
- **State Transitions**
  - `queued` → `in_progress` when first task starts.
  - `in_progress` → `succeeded` after PR creation or `no_changes`.
  - `in_progress` → `no_changes` when commit step detects no diff.
  - `in_progress` → `failed` on unrecoverable error; stores cause in `result_summary`.

## SpecAutomationTaskState

- **Identifiers**
  - `task_state_id` (UUID).
  - `run_id` (foreign key to SpecAutomationRun).
- **Attributes**
  - `phase` (enum) – phases such as `prepare_job`, `start_job_container`, `git_clone`, `speckit_specify`, `speckit_plan`, `speckit_tasks`, `commit_push`, `open_pr`, `cleanup`.
  - `status` (enum) – `pending`, `running`, `succeeded`, `failed`, `skipped`, `retrying`.
  - `attempt` (integer) – current retry count.
  - `started_at`, `completed_at` (timestamps).
  - `stdout_path`, `stderr_path` (strings) – artifact references.
  - `metadata` (JSON) – structured payload (e.g., branch name, PR URL, error codes).
- **Relationships**
  - Belongs to `SpecAutomationRun`.
  - May reference `AutomationArtifact` records for logs.
- **Validation Rules**
  - `completed_at` must be ≥ `started_at`.
  - `attempt` increments monotonically per retry.
  - `stdout_path`/`stderr_path` required if `status` ∈ {`failed`, `succeeded`} and logs exist.

## AutomationArtifact

- **Identifiers**
  - `artifact_id` (UUID).
  - `run_id` (foreign key).
- **Attributes**
  - `name` (string) – concise label (e.g., `phase-speckit_specify.stdout`).
  - `artifact_type` (enum) – `stdout_log`, `stderr_log`, `diff_summary`, `commit_status`, `metrics_snapshot`, `environment_info`.
  - `storage_path` (string) – relative path under `/work/runs/{run_id}/artifacts` or external URL.
  - `content_type` (string) – media type hint (e.g., `text/plain`, `application/json`).
  - `size_bytes` (integer) – for retention calculations.
  - `expires_at` (timestamp) – scheduled cleanup time (≥ 7 days by default).
  - `source_phase` (enum, optional) – mirrors `SpecAutomationTaskState.phase` where artifact originated.
- **Relationships**
  - Belongs to `SpecAutomationRun`.
  - Optional link to `SpecAutomationTaskState` for traceability.
- **Validation Rules**
  - `expires_at` must be ≥ `completed_at` of owning run + 7 days (minimum retention).
  - `storage_path` must not leak outside shared artifact root when using local volume.

## AgentConfiguration

- **Identifiers**
  - `agent_config_id` (UUID).
  - `run_id` (foreign key).
- **Attributes**
  - `agent_backend` (string) – e.g., `codex_cli`, `future_agent`.
  - `agent_version` (string) – semantic version or git SHA.
  - `prompt_pack_version` (string) – Spec Kit prompts bundle version.
  - `runtime_env` (JSON) – subset of environment variables forwarded to agent (e.g., rate limits).
  - `created_at` (timestamp).
- **Relationships**
  - References `SpecAutomationRun` (1-to-1 per run).
- **Validation Rules**
  - `agent_backend` must be whitelisted in deployment configuration.
  - `agent_version` required for auditability.
