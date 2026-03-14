# Data Model: Skills-First Workflow Pipeline

## Entity Overview

| Entity | Purpose | Key Relationships |
|---|---|---|
| AutomationRun | Represents one automation execution and lifecycle. | Aggregates task states and artifacts; links one agent configuration snapshot. |
| AutomationTaskState | Captures per-phase attempt status and metadata. | Belongs to one run; may reference artifacts. |
| AutomationArtifact | Stores logs and outputs from run phases. | Belongs to one run and optional task state. |
| AutomationAgentConfiguration | Captures backend/version/runtime env snapshot. | One-to-one with run. |

## AutomationRun

- `id` (UUID), `external_ref` (optional correlation key), `repository`, `base_branch`
- `status`: `queued | in_progress | succeeded | failed | no_changes`
- `branch_name`, `pull_request_url`, `result_summary`
- `requested_workflow_input`, `started_at`, `completed_at`, `worker_hostname`, `job_container_id`

No schema changes are required for 015 alignment.

## AutomationTaskState

- `phase`: retains legacy values (`prepare_job`, `start_job_container`, `git_clone`, `agentkit_specify`, `agentkit_plan`, `agentkit_tasks`, `commit_push`, `open_pr`, `cleanup`) and adds contract targets for `agentkit_analyze`, `agentkit_implement`.
- `status`: `pending | running | succeeded | failed | skipped | retrying`
- `attempt`, timestamps, stdout/stderr paths
- `metadata` (JSON payload)

### Normalized Skills Metadata (derived)

`AutomationTaskState` now exposes normalized derived metadata from `metadata`:

- `selectedSkill`
- `executionPath` (`skill | direct_fallback | direct_only`)
- `usedSkills`
- `usedFallback`
- `shadowModeRequested`

Derivation rules:

1. Use explicit metadata values when present.
2. If absent and phase starts with `agentkit_`, default `selectedSkill=agentkit`.
3. If `selectedSkill=agentkit` and execution path is missing, default `executionPath=skill`.
4. Infer `usedSkills` and `usedFallback` from `executionPath` when possible.

## AutomationArtifact

- `artifact_type`: `stdout_log | stderr_log | diff_summary | commit_status | metrics_snapshot | environment_info`
- `storage_path`, `content_type`, `size_bytes`, `expires_at`
- `source_phase`: optional phase reference

No structural change required; compatibility preserved.

## AutomationAgentConfiguration

- `agent_backend`, `agent_version`, `prompt_pack_version`, `runtime_env`, `created_at`
- Used for auditability and backend swapping.

No structural change required for 015 alignment.
