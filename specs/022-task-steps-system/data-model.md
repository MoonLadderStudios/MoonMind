# Data Model: Task Steps System

## Entity: TaskStepSpec (new canonical payload object)

### Fields
- `id`: optional string identifier; when absent runtime derives deterministic fallback `step-{index+1}`.
- `title`: optional short display text for operator/UI context.
- `instructions`: optional step-specific instruction fragment.
- `skill`: optional `TaskSkillSelection` override with `id`, `args`, optional `requiredCapabilities`.

### Validation Rules
- At least one of `instructions` or `skill` must be present and meaningful.
- Forbid step-level task-scoped controls: `runtime`, `model`, `effort`, `git`, `publish`, `container`, `repository`, `targetRuntime`.
- Duplicate/missing `id` values are allowed because runtime falls back to index-based identity for deterministic processing.

## Entity: TaskExecutionSpec (existing, extended)

### Added Field
- `steps: list[TaskStepSpec]` default `[]`.

### Existing Required Field (unchanged)
- `instructions` remains required and is treated as task objective for every step.

### Additional Cross-Field Rule
- If `task.container.enabled=true` and `task.steps` non-empty, reject payload as unsupported in first rollout.

## Entity: ResolvedTaskStep (runtime-only)

### Runtime Properties
- `step_index`: 0-based sequence index.
- `step_id`: `task.steps[i].id` or generated `step-{i+1}`.
- `step_title`: optional title.
- `effective_skill_id`: `step.skill.id` -> `task.skill.id` -> `auto`.
- `effective_skill_args`: selected args object from effective skill.
- `has_step_instructions`: boolean for event payloads.

### Behavior
- Runtime executes exactly one invocation per resolved step.
- Runtime halts on first failed step.

## Entity: StepExecutionEvent (queue event payload)

### Event Types
- `task.steps.plan`
- `task.step.started`
- `task.step.finished`
- `task.step.failed`

### Event Payload Fields
- `stepIndex`
- `stepId`
- `stepTitle` (optional)
- `effectiveSkill`
- `hasStepInstructions`
- `summary` (optional)

## Entity: StepArtifactDescriptor (artifact naming convention)

### Per-Step Paths
- `logs/steps/step-<index4>.log`
- `patches/steps/step-<index4>.patch` (optional; empty diff omitted)

### Existing Task-Level Artifacts (retained)
- `logs/prepare.log`
- `logs/execute.log`
- `logs/publish.log`
- `patches/changes.patch`
- `task_context.json`
- `publish_result.json`
