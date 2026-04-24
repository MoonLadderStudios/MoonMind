# Feature Specification: Agent Skill System Phase 5

**Feature Branch**: `118-agent-skill-phase5`
**Created**: 2026-03-31
**Status**: Draft
**Input**: User description: "Fully implement Phase 5 of spec.md (Input) using test-driven development."

## Source Document Requirements

- **DOC-REQ-001**: `spec.md (Input)` §10 - System MUST add canonical `task.skills` handling to task submission models.
- **DOC-REQ-002**: `spec.md (Input)` §10 - System MUST add canonical `step.skills` handling to plan-node or step execution models.
- **DOC-REQ-003**: `spec.md (Input)` §10 - System MUST ensure `step.skills` correctly inherits from and overrides `task.skills`.
- **DOC-REQ-004**: `spec.md (Input)` §10 - System MUST add validation for invalid skill selectors during task submit or plan validation.
- **DOC-REQ-005**: `spec.md (Input)` §10 - System MUST add explicit `agent_skill.*` activity family for resolution vs materialization.
- **DOC-REQ-006**: `spec.md (Input)` §10 - System MUST route `agent_skill.materialize` and related preparation activities to `mm.activity.agent_runtime` or a capable preparation fleet.
- **DOC-REQ-007**: `spec.md (Input)` §10 - System MUST ensure the workflow explicitly propagates `resolved_skillset_ref` across activity boundaries.
- **DOC-REQ-008**: `spec.md (Input)` §10 - System MUST pass `resolved_skillset_ref` or equivalent through the `MoonMind.AgentRun` path.
- **DOC-REQ-009**: `spec.md (Input)` §10 - System MUST ensure workflow payloads carry refs and metadata only.
- **DOC-REQ-010**: `spec.md (Input)` §10 - System MUST ensure retries and continuation paths reuse the same resolved snapshot.
- **DOC-REQ-011**: `spec.md (Input)` §10 - System MUST include workflow-boundary tests for task-level selection, step-level override, child workflow dispatch, and rerun behavior.

## User Scenarios & Testing

### User Story 1 - Submission & Resolution (Priority: P1)

Operators must be able to specify skill sets and versions inside task payloads which translate into stable snapshot resolution.

**Why this priority**: It establishes the foundation for skills existing as explicit context selections within execution.

**Independent Test**: Can be tested via unit tests ensuring `TaskExecutionSpec` and `TaskStepSpec` validation succeed and propagate downward.

**Acceptance Scenarios**:
1. **Given** a task payload with explicit `task.skills` defined, **When** the workflow parses the model, **Then** it cleanly extracts the skill selections.
2. **Given** invalid or colliding skill selections, **When** submitting a workflow, **Then** validation fails with a coherent constraint exception.

### User Story 2 - Inheritance & Overrides (Priority: P2)

Steps within a compiled plan must inherit task-scoped skill behaviors but have the freedom to override or exclude specific tools.

**Why this priority**: Plan DAGs rely on tight controls to grant different agent-runs independent context.

**Independent Test**: Can be verified through data-model property assertions on `Step` execution models.

**Acceptance Scenarios**:
1. **Given** `task.skills` defining a baseline context and a `step.skills` omitting inclusion, **When** evaluated, **Then** baseline context is propagated.
2. **Given** `step.skills` strictly overriding an include constraint from `task.skills`, **When** evaluated, **Then** the step context wins.

### User Story 3 - Temporal Wiring (Priority: P3)

The internal temporal worker routing must execute preparation activities via the explicit `agent_skill.*` signature.

**Why this priority**: Required for production durability and fleet segregation.

**Independent Test**: Can be tested via workflow simulation tests spanning `agent_run` invocations.

**Acceptance Scenarios**:
1. **Given** `MoonMind.AgentRun` dispatch, **When** executing skill prep, **Then** the system delegates to `agent_skill.resolve` and `agent_skill.materialize` activities.

## Requirements

### Functional Requirements

- **FR-001**: System MUST strictly validate `task.skills` on `TaskExecutionSpec` payloads. (DOC-REQ-001, DOC-REQ-004)
- **FR-002**: System MUST inject robust validation into `Step` nodes for `step.skills` schema boundaries. (DOC-REQ-002, DOC-REQ-003)
- **FR-003**: System MUST provide Temporal activities mapped under `agent_skill.resolve` and `agent_skill.materialize` execution routes. (DOC-REQ-005, DOC-REQ-006)
- **FR-004**: System MUST propagate `resolved_skillset_ref` within the `MoonMind.AgentRun` parameter boundaries, stripped of full execution body bytes. (DOC-REQ-007, DOC-REQ-008, DOC-REQ-009)
- **FR-005**: System MUST ensure retries of activities and continuations of standard workflow behavior use the identical previously minted `resolved_skillset_ref`. (DOC-REQ-010)
- **FR-006**: Code contributions MUST include workflow boundary verification matching task specifications. (DOC-REQ-011)

### Key Entities

- **TaskExecutionSpec**: Enhanced to contain globally scoped task behaviors for skills.
- **Step**: Enhanced to contain locally scoped graph node behaviors for skills overrides.
- **MoonMind.AgentRun**: The unified launch protocol for executing dynamic managed workers now aware of `resolved_skillset_ref`.

## Success Criteria

### Measurable Outcomes

- **SC-001**: 100% of newly added model fields include validation tests preventing structurally invalid submissions.
- **SC-002**: Workflows routing logic is free of direct local FS mutation steps inside the top-level orchestration, instead cleanly invoking the `agent_skill.materialize` activity.
- **SC-003**: In-flight runs correctly resolve standard configurations if missing `agent_skill` contexts without crashing backward capability lines.
