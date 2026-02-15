# Data Model: Skills-First Workflow Umbrella

## Enum: WorkflowStageName

Canonical stage names used by skills-first orchestration.

- `specify`
- `plan`
- `tasks`
- `analyze`
- `implement`

## Enum: StageExecutionPath

Execution path for each stage attempt.

- `skill`
- `direct_fallback`
- `direct_only`

## Value Object: SkillCatalogEntry

Represents one registered skill option.

- **Fields**:
  - `skill_id` (string)
  - `supported_stages` (set[WorkflowStageName])
  - `allowlisted` (bool)
  - `is_speckit` (bool)
  - `health_check` (string | null)
- **Validation rules**:
  - `skill_id` must be unique.
  - `supported_stages` cannot be empty.
  - Non-allowlisted entries cannot be selected for execution.

## Value Object: WorkflowStageRequest

Canonical input to stage execution.

- **Fields**:
  - `run_id` (UUID)
  - `feature_id` (string)
  - `stage` (WorkflowStageName)
  - `requested_skill_id` (string | null)
  - `payload` (dict)
  - `metadata` (dict)
- **Validation rules**:
  - `run_id`, `feature_id`, and `stage` are required.
  - If `requested_skill_id` is provided, it must be allowlisted for that stage.

## Value Object: WorkflowStageResult

Canonical stage output independent of provider.

- **Fields**:
  - `run_id` (UUID)
  - `stage` (WorkflowStageName)
  - `selected_skill_id` (string)
  - `execution_path` (StageExecutionPath)
  - `status` (`succeeded` | `failed`)
  - `duration_ms` (int)
  - `artifacts` (list[dict])
  - `error_message` (string | null)
- **Validation rules**:
  - `selected_skill_id` must be populated when `execution_path=skill`.
  - `duration_ms` must be non-negative.

## Value Object: WorkerStartupProfile

Captures worker readiness checks at startup.

- **Fields**:
  - `worker_id` (string)
  - `queues` (list[string])
  - `speckit_available` (bool)
  - `codex_preflight_status` (`passed` | `failed`)
  - `embedding_provider` (string)
  - `embedding_model` (string)
  - `ready` (bool)
  - `failure_reason` (string | null)
- **Validation rules**:
  - `ready=true` requires `speckit_available=true` for Codex-capable workers.
  - If `embedding_provider=google`, credential presence must be validated.

## State Transitions

1. `pending` -> `skill_running` when a stage is dispatched through a selected skill.
2. `skill_running` -> `succeeded` when skill result succeeds.
3. `skill_running` -> `fallback_running` when skill fails and fallback is enabled.
4. `fallback_running` -> `succeeded` when fallback succeeds.
5. `skill_running` or `fallback_running` -> `failed` when no successful path remains.
