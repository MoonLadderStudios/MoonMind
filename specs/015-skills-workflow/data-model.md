# Data Model: Skills Workflow Alignment Refresh

## Enum: WorkflowStageName

Canonical stage names used by current skills-first workflow task execution.

- `discover_next_phase`
- `submit_codex_job`
- `apply_and_publish`

## Enum: StageExecutionPath

Execution path for each stage attempt.

- `skill`
- `direct_fallback`
- `direct_only`

## Enum: OrchestrationMode

Execution intent selected for the feature/update workflow.

- `runtime`
- `docs`

## Value Object: StageExecutionMetadata (persisted metadata normalization)

Normalized skill-routing metadata surfaced for each phase.

- **Fields**:
  - `selected_skill` (string | null)
  - `adapter_id` (string | null)
  - `execution_path` (`skill` | `direct_fallback` | `direct_only` | null)
  - `used_skills` (bool | null)
  - `used_fallback` (bool | null)
  - `shadow_mode_requested` (bool | null)
- **Validation rules**:
  - For legacy Speckit phases, missing `selected_skill` defaults to `speckit`.
  - If `selected_skill=speckit` and `adapter_id` is missing, default `adapter_id` to `speckit`.
  - `used_fallback=true` implies `execution_path=direct_fallback` when execution path is present.

## Value Object: SpecAutomationPhaseState (API payload view)

Normalized API contract for phase details returned by `/api/spec-automation/runs/{run_id}`.

- **Fields**:
  - `phase` (enum)
  - `status` (enum)
  - `attempt` (int >= 1)
  - `metadata` (object | null)
  - `selected_skill` (string | null)
  - `adapter_id` (string | null)
  - `execution_path` (`StageExecutionPath` | null)
  - `used_skills` (bool | null)
  - `used_fallback` (bool | null)
  - `shadow_mode_requested` (bool | null)
- **Validation rules**:
  - API `selected_skill` maps from normalized metadata `selectedSkill`.
  - API `adapter_id` maps from normalized metadata `adapterId`.
  - API `execution_path` maps from normalized metadata `executionPath`.

## Value Object: WorkflowStageContract

Canonical stage contract metadata used in `015` documentation.

- **Fields**:
  - `run_id` (UUID)
  - `feature_id` (string)
  - `stage` (`WorkflowStageName`)
  - `skill_execution` (`StageExecutionMetadata`)
  - `artifacts` (list[dict])

## Value Object: SharedSkillsWorkspace

Run-scoped skill materialization footprint shared by Codex and Gemini adapters.

- **Fields**:
  - `skills_active_path` (string)
  - `agents_skills_path` (string)
  - `gemini_skills_path` (string)
  - `selection_source` (string)
  - `skills` (list[dict])
- **Validation rules**:
  - `.agents/skills` and `.gemini/skills` must resolve to the same `skills_active_path`.

## Value Object: WorkerFastPathProfile

Startup profile expected by quickstart and compose contract.

- **Fields**:
  - `codex_auth_ready` (bool)
  - `gemini_auth_ready` (bool)
  - `worker_runtime_mode` (string)
  - `default_embedding_provider` (string)
  - `google_embedding_model` (string)
  - `worker_token_ready` (bool)
- **Validation rules**:
  - If `default_embedding_provider=google`, a Google/Gemini API key must be configured.

## Value Object: ImplementationIntentProfile

Feature-level orchestration mode profile used for guardrails in planning/tasks.

- **Fields**:
  - `orchestration_mode` (`OrchestrationMode`)
  - `requires_runtime_code` (bool)
  - `requires_validation_tests` (bool)
  - `validation_command` (string)
- **Validation rules**:
  - `orchestration_mode=runtime` requires `requires_runtime_code=true`.
  - Runtime mode requires `validation_command=./tools/test_unit.sh`.

## State Transitions

1. `queued` -> `running` when stage execution begins.
2. `running` -> `skill` path result when adapter execution succeeds.
3. `running` -> `direct_fallback` when adapter execution fails and fallback is enabled.
4. `running` -> `direct_only` when skills routing is disabled/canaried out.
5. `running` -> `failed` on adapter resolution or execution failure without successful fallback.
