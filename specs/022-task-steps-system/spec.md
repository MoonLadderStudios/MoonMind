# Feature Specification: Task Steps System

**Feature Branch**: `022-task-steps-system`  
**Created**: 2026-02-17  
**Status**: Draft  
**Input**: User description: "Implement the tasks step system as described in docs/TasksStepSystem.md"

## Source Document Requirements

| ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/TasksStepSystem.md` Section 1 | A task run remains a single queue job with one claim, one workspace, and one publish decision. |
| DOC-REQ-002 | `docs/TasksStepSystem.md` Section 5.2 | Wrapper stage contract remains `moonmind.task.prepare -> moonmind.task.execute -> moonmind.task.publish`. |
| DOC-REQ-003 | `docs/TasksStepSystem.md` Section 6.1 | Canonical task payload supports optional `task.steps`; omitted/empty steps resolve to implicit single-step behavior. |
| DOC-REQ-004 | `docs/TasksStepSystem.md` Section 6.2 | Step schema supports optional `id`, `title`, `instructions`, and `skill`; step-level runtime/model/effort/repo/publish overrides are forbidden. |
| DOC-REQ-005 | `docs/TasksStepSystem.md` Section 6.3 | `task.instructions` remains required as task objective even when steps are present. |
| DOC-REQ-006 | `docs/TasksStepSystem.md` Section 7 | Step execution must resolve effective skill precedence (`step.skill` -> `task.skill` -> `auto`) and assemble objective+step instruction prompts. |
| DOC-REQ-007 | `docs/TasksStepSystem.md` Section 8.1 | Prepare stage must materialize the union of non-auto skills referenced by task-level and step-level skill selections. |
| DOC-REQ-008 | `docs/TasksStepSystem.md` Section 8.2 | Execute stage must iterate steps sequentially, emit per-step lifecycle events, and fail fast on first step failure. |
| DOC-REQ-009 | `docs/TasksStepSystem.md` Section 8.2/8.4 | Publish stage executes once per task only after all steps succeed; publish behavior preserves existing none/branch/pr semantics. |
| DOC-REQ-010 | `docs/TasksStepSystem.md` Section 8.3 | Cooperative cancellation semantics are preserved during step execution and step boundaries, including worker cancel acknowledgement flow. |
| DOC-REQ-011 | `docs/TasksStepSystem.md` Section 9.1/9.2 | Step observability requires `task.steps.plan`, `task.step.started`, `task.step.finished`, `task.step.failed` events and per-step log artifacts. |
| DOC-REQ-012 | `docs/TasksStepSystem.md` Section 10 | Queue new task UI must support creating/editing steps and submitting canonical payloads containing steps while keeping task defaults (including the publish default from `MOONMIND_DEFAULT_PUBLISH_MODE`, default `pr`). |
| DOC-REQ-013 | `docs/TasksStepSystem.md` Section 11.2 | Capability derivation must include required capabilities from task-level skill and step-level skill requirements plus existing runtime/git/gh/docker derivation rules. |
| DOC-REQ-014 | `docs/TasksStepSystem.md` Section 12.3 | First rollout must explicitly reject unsupported `task.steps` + container execution combinations (or equivalent explicit unsupported behavior). |
| DOC-REQ-015 | `docs/TasksStepSystem.md` Section 14 | Implementation must include runtime code changes and validation tests across task contract, worker execution flow, and dashboard submission behavior. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Execute Multi-Step Tasks in One Job (Priority: P1)

An operator submits a canonical `type="task"` job containing ordered steps and expects the worker to execute all steps sequentially inside the same claimed job workspace.

**Why this priority**: This is the core feature behavior; without step sequencing the Task Steps system does not exist.

**Independent Test**: Submit a task with three steps and assert one claimed job runs `prepare`, all step invocations in sequence, then a single publish decision.

**Acceptance Scenarios**:

1. **Given** a canonical task payload with `task.steps` and publish mode `pr`, **When** the worker processes the job, **Then** steps execute sequentially in one workspace and publish runs once after all steps succeed.
2. **Given** a canonical task payload with empty `task.steps`, **When** the worker processes the job, **Then** the worker executes an implicit single step derived from task objective and task-level skill.
3. **Given** a task payload containing step-level runtime or publish overrides, **When** the payload is normalized, **Then** validation fails with a task-contract error.

---

### User Story 2 - Observe and Control Step Execution (Priority: P2)

An operator monitors step-level lifecycle events and can cancel a running multi-step task safely without leaving queue state inconsistent.

**Why this priority**: Operators need deterministic observability and cancellation for safe runtime operations.

**Independent Test**: Run a multi-step task, assert required step events/artifacts are emitted, request cancellation during execution, and verify cancel acknowledgement terminalizes the job without success/failure completion.

**Acceptance Scenarios**:

1. **Given** a running task with steps, **When** each step starts/finishes/fails, **Then** step events include step index/id/effective skill metadata and logs are persisted under step artifact paths.
2. **Given** cancellation is requested during step execution, **When** the worker observes cancellation, **Then** it stops before next step, acknowledges cancellation, and does not emit success completion.
3. **Given** step N fails, **When** failure occurs, **Then** execute stage fails immediately, steps N+1..end are not executed, and publish is skipped.

---

### User Story 3 - Author Steps in Queue UI (Priority: P3)

An operator can define, edit, and submit step arrays from `/tasks/queue/new` and the backend accepts canonical payloads with correct defaults and capability derivation.

**Why this priority**: UI authoring is required for routine adoption by users who do not craft raw JSON payloads.

**Independent Test**: Create a queue task from UI with two steps and verify API payload includes `task.steps`, keeps the publish default configured via `MOONMIND_DEFAULT_PUBLISH_MODE` (default `pr`), and job runs through backend normalization.

**Acceptance Scenarios**:

1. **Given** the queue new form, **When** user adds/removes/reorders steps and submits, **Then** payload contains ordered `task.steps` entries with optional step instructions and skill overrides.
2. **Given** form submission with no explicit publish choice, **When** payload is generated, **Then** publish mode defaults to `pr` and required capabilities include `gh`.
3. **Given** step-level skill required capabilities, **When** payload is normalized, **Then** derived `requiredCapabilities` include the union of runtime/git/publish/container and step skill capabilities.

### Edge Cases

- What happens when `task.steps` is present but each step is blank (no instructions and no skill)?
- How does system handle duplicate step IDs or missing step IDs while preserving deterministic event/artifact mapping?
- What happens when `task.container.enabled=true` and `task.steps` is supplied in the same payload?
- How does cancellation behave when requested while a long-running runtime command is mid-step?
- How does capability derivation behave when one step requires capabilities unavailable on the claiming worker?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST preserve one queue job per task run while executing all steps in one claim/workspace/publish scope. (`DOC-REQ-001`)
- **FR-002**: System MUST preserve wrapper stage ordering (`moonmind.task.prepare`, `moonmind.task.execute`, `moonmind.task.publish`) with steps internal to execute stage. (`DOC-REQ-002`)
- **FR-003**: System MUST accept optional `task.steps` arrays for canonical task jobs and MUST resolve missing/empty arrays as an implicit single-step execution path. (`DOC-REQ-003`)
- **FR-004**: System MUST validate step schema fields (`id`, `title`, `instructions`, `skill`) and reject step-level runtime/model/effort/repository/git/publish overrides. (`DOC-REQ-004`)
- **FR-005**: System MUST require `task.instructions` regardless of step usage and include task objective in each step prompt. (`DOC-REQ-005`, `DOC-REQ-006`)
- **FR-006**: System MUST resolve effective skill per step using precedence `step.skill.id` -> `task.skill.id` -> `auto` and execute one runtime invocation per step in order. (`DOC-REQ-006`, `DOC-REQ-008`)
- **FR-007**: Worker prepare stage MUST materialize union of non-auto skill selections from task-level skill and all step-level skills before execute stage. (`DOC-REQ-007`)
- **FR-008**: Execute stage MUST emit step lifecycle events (`task.steps.plan`, `task.step.started`, `task.step.finished`, `task.step.failed`) including step metadata and persist per-step logs. (`DOC-REQ-008`, `DOC-REQ-011`)
- **FR-009**: On first step failure, execute stage MUST stop remaining steps and publish stage MUST NOT run. (`DOC-REQ-008`, `DOC-REQ-009`)
- **FR-010**: Publish stage MUST execute at most once per task run after all steps succeed and preserve existing none/branch/pr semantics. (`DOC-REQ-009`)
- **FR-011**: Worker cancellation flow MUST preserve cooperative cancellation semantics during step execution and acknowledge cancellation through queue cancel-ack endpoint behavior. (`DOC-REQ-010`)
- **FR-012**: Queue submit UI MUST support step authoring/editing, emit canonical ordered `task.steps`, and keep the publish default dictated by `MOONMIND_DEFAULT_PUBLISH_MODE` (default `pr`). (`DOC-REQ-012`)
- **FR-013**: Capability derivation MUST include runtime/git baseline, publish-derived `gh`, container-derived `docker`, task-level skill required capabilities, and step-level skill required capabilities. (`DOC-REQ-013`)
- **FR-014**: Runtime MUST explicitly reject unsupported `task.steps` with container execution in first rollout via deterministic validation error. (`DOC-REQ-014`)
- **FR-015**: Delivery MUST include runtime code updates and validation tests covering contract parsing, worker step execution, events/cancellation, and queue UI submission behavior. (`DOC-REQ-015`)

### Key Entities *(include if feature involves data)*

- **TaskStepSpec**: Canonical step definition with optional `id`, `title`, `instructions`, and optional skill override.
- **ResolvedTaskStep**: Runtime-resolved step produced by combining task-level defaults with step-level overrides.
- **StepExecutionEvent**: Queue event payload describing step lifecycle status and metadata (`stepIndex`, `stepId`, `effectiveSkill`, summary).
- **StepArtifactDescriptor**: Artifact metadata for step logs/patches under step-specific artifact paths.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A canonical task with `N` steps executes exactly `N` runtime invocations in order within one claimed job, verified by deterministic step events in unit/integration tests.
- **SC-002**: Cancellation requested during multi-step execution transitions the job to cancelled acknowledgement path without success/failure completion, verified by worker tests.
- **SC-003**: Queue new task UI can create and submit tasks with at least two steps and backend normalization accepts payloads without manual JSON editing.
- **SC-004**: Automated tests added/updated for task contract normalization, worker step execution, and dashboard submission pass via `./tools/test_unit.sh`.
