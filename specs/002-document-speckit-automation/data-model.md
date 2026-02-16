# Data Model: Skills-First Spec Automation Pipeline

## Entity Overview

| Entity | Purpose | Key Relationships |
|---|---|---|
| SpecAutomationRun | Represents one automation execution and lifecycle. | Aggregates task states and artifacts; links one agent configuration snapshot. |
| SpecAutomationTaskState | Captures per-phase attempt status and metadata. | Belongs to one run; may reference artifacts. |
| SpecAutomationArtifact | Stores logs and outputs from run phases. | Belongs to one run and optional task state. |
| SpecAutomationAgentConfiguration | Captures backend/version/runtime env snapshot. | One-to-one with run. |

## SpecAutomationRun

- `id` (UUID), `external_ref` (optional correlation key), `repository`, `base_branch`
- `status`: `queued | in_progress | succeeded | failed | no_changes`
- `branch_name`, `pull_request_url`, `result_summary`
- `requested_spec_input`, `started_at`, `completed_at`, `worker_hostname`, `job_container_id`

No schema changes are required for 015 alignment.

## SpecAutomationTaskState

- `phase`: retains legacy values (`prepare_job`, `start_job_container`, `git_clone`, `speckit_specify`, `speckit_plan`, `speckit_tasks`, `commit_push`, `open_pr`, `cleanup`) and adds contract targets for `speckit_analyze`, `speckit_implement`.
- `status`: `pending | running | succeeded | failed | skipped | retrying`
- `attempt`, timestamps, stdout/stderr paths
- `metadata` (JSON payload)

### Normalized Skills Metadata (derived)

`SpecAutomationTaskState` now exposes normalized derived metadata from `metadata`:

- `selectedSkill`
- `executionPath` (`skill | direct_fallback | direct_only`)
- `usedSkills`
- `usedFallback`
- `shadowModeRequested`

Derivation rules:

1. Use explicit metadata values when present.
2. If absent and phase starts with `speckit_`, default `selectedSkill=speckit`.
3. If `selectedSkill=speckit` and execution path is missing, default `executionPath=skill`.
4. Infer `usedSkills` and `usedFallback` from `executionPath` when possible.

## SpecAutomationArtifact

- `artifact_type`: `stdout_log | stderr_log | diff_summary | commit_status | metrics_snapshot | environment_info`
- `storage_path`, `content_type`, `size_bytes`, `expires_at`
- `source_phase`: optional phase reference

No structural change required; compatibility preserved.

## SpecAutomationAgentConfiguration

- `agent_backend`, `agent_version`, `prompt_pack_version`, `runtime_env`, `created_at`
- Used for auditability and backend swapping.

No structural change required for 015 alignment.
