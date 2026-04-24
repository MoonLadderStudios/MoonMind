# Feature Specification: Agent Skill System Phase 6

**Feature Branch**: `122-agent-skill-phase6`  
**Status**: Active  
**Input**: Implement Phase 6 of spec.md (Input) using test-driven development

## User Scenarios & Testing

### User Story 1 - Submit Task with Explicity Selected Agent Skills (Priority: P1)

As a Mission Control Operator, I want to be able to explicitly select or exclude agent skills when submitting a task on the dashboard, so that I can control exactly which skills the orchestrated task will possess.

**Why this priority**: Task submitting UI is essential to allow users to use the capability added in Phase 5 dynamically without needing to write JSON payloads.

**Independent Test**: The UI correctly displays available local and repository agent skills, serializes them accurately into `task.skills`, and correctly routes them into `resolved_skillset_ref`.

**Acceptance Scenarios**:

1. **Given** a user is on the submit task form, **When** they request available skills, **Then** the form renders a selection of valid target skills.

---

### User Story 2 - Task Details Skill Snapshot and Provenance Display (Priority: P1)

As an operator investigating task executions, I want to see a clear trace of what agent skills a task resolved and used, along with compact tags describing their origin (e.g. `repo`, `local`), so that I can debug resolution overlap and agent context issues.

**Why this priority**: Operator debugging needs a clear trace to understand the context of agent outputs.

**Independent Test**: The frontend fetches `resolved_skillset_ref` metadata and renders a provenance UI widget for each resolved skill on the task details page.

**Acceptance Scenarios**:

1. **Given** a task details page loads a resolved task metadata payload, **When** skill selections are valid, **Then** display it in an easy-to-read badge format.

---

### User Story 3 - Debug Materialization Secrets Redaction (Priority: P2)

As an administrator, I want to ensure non-superuser tier users cannot extract raw manifest prompt indexes and materialization storage paths through the API, to enforce privacy boundaries on specific execution contexts.

**Why this priority**: Security against unauthorized metadata access ensures enterprise readiness.

**Acceptance Scenarios**:

1. **Given** a non-superuser fetches execution details, **When** the backend resolves the request, **Then** any `manifest` internal paths or restricted logs are pruned or explicitly denied.

---

## Requirements

### Functional Requirements

- **DOC-REQ-001**: System MUST provide UI layout for selecting agent skills gracefully when configuring new tasks.
- **DOC-REQ-002**: System MUST render explicitly resolved `resolved_skillset_ref` states and precedence in task detail views.
- **DOC-REQ-003**: System MUST provide a compact provenance display (badge) for skill origins.
- **DOC-REQ-004**: System MUST differentiate between explicit selector and inherited default skills in the proposal review layout.
- **DOC-REQ-005**: System MUST strictly gate access and visibility to raw manifests, prompt indexes, and materialization references across the API ensuring only superusers can view the debugging outputs.
- **DOC-REQ-006**: System MUST supply E2E test scripts rendering explicit skill selection submission and React interface tests for the debug module.

## Success Criteria

### Measurable Outcomes

- **SC-001**: E2E browser tests successfully complete task creation explicitly overriding a particular skillset.
- **SC-002**: Unit tests successfully prune unauthorized `manifest` and `materialization_ref` elements when an execution payload is retrieved.

## Traceability

| Requirement | Implementation Component | Test Coverage |
|-------------|--------------------------|---------------|
| DOC-REQ-001 | dashboard.js / tasks-list.tsx | test_task_create_submit_browser.py |
| DOC-REQ-002 | task-detail.tsx | test_task_create_submit_browser.py |
| DOC-REQ-003 | SkillProvenanceBadge.tsx | UI Tests |
| DOC-REQ-004 | proposals.tsx | UI Tests |
| DOC-REQ-005 | executions.py | test_executions.py |
| DOC-REQ-006 | test_task_create_submit_browser.py | E2E Suite |
