# Feature Specification: Task Dependencies Phase 1 — Backend Foundation

**Feature Branch**: `101-task-dependencies-phase1`
**Created**: 2026-03-22
**Status**: Draft
**Input**: User description: "Implement Phase 1 of docs/Tasks/TaskDependencies.md"

## Source Document Requirements

Extracted from `docs/Tasks/TaskDependencies.md` §8 Phase 1 and referenced design sections.

| Requirement ID | Source Citation | Requirement Summary |
|----------------|----------------|---------------------|
| DOC-REQ-001 | §3.2 "Updated lifecycle stages" | The `MoonMind.Run` lifecycle MUST include a `waiting_on_dependencies` stage between `initializing` and `planning`. |
| DOC-REQ-002 | §3.2 "waiting_on_dependencies state — implementation requirements" item 1 | A `WAITING_ON_DEPENDENCIES` value MUST be added to the `MoonMindWorkflowState` enum in `api_service/db/models.py`. |
| DOC-REQ-003 | §3.2 "waiting_on_dependencies state — implementation requirements" item 2 | An Alembic migration MUST add the new enum value to the PostgreSQL `moonmindworkflowstate` native enum type. |
| DOC-REQ-004 | §3.2 "waiting_on_dependencies state — implementation requirements" item 4 | A workflow state constant `STATE_WAITING_ON_DEPENDENCIES` MUST be added to `run.py` alongside existing constants. |
| DOC-REQ-005 | §3.2 "waiting_on_dependencies state — implementation requirements" item 3 | The projection sync in `api_service/core/sync.py` MUST recognize `waiting_on_dependencies` during projection sync without warning on unknown values. |
| DOC-REQ-006 | §3.2 "waiting_on_dependencies state — implementation requirements" item 5 | Mission Control status mapping MUST render a dashboard status for the `WAITING_ON_DEPENDENCIES` state. |
| DOC-REQ-007 | §3.2 paragraph on naming | The value MUST use lowercase naming (`waiting_on_dependencies`) to match existing convention. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Workflow correctly reports waiting state (Priority: P1)

An operator submits a task with dependencies. While waiting for prerequisites, the system reports the workflow as being in a `waiting_on_dependencies` state. The API and dashboard correctly surface this state so the operator can see the task is blocked, not failed or stuck.

**Why this priority**: Without the state enum and status mappings, no other Phase (workflow logic, API validation, or UI) can function. This is the foundational primitive.

**Independent Test**: Can be fully tested by adding the enum value, running the DB migration, and verifying that projection sync and status mapping functions accept the new value without errors.

**Acceptance Scenarios**:

1. **Given** a `MoonMindWorkflowState` enum, **When** the value `WAITING_ON_DEPENDENCIES` is referenced, **Then** it resolves to the string `"waiting_on_dependencies"`.
2. **Given** a Temporal workflow emitting `mm_state = "waiting_on_dependencies"`, **When** the projection sync processes this value, **Then** it maps to the correct `MoonMindWorkflowState` member without logging a warning.
3. **Given** a workflow in `WAITING_ON_DEPENDENCIES` state, **When** the dashboard status mapper processes it, **Then** it returns a recognizable dashboard status string (e.g., `"waiting"`).

---

### User Story 2 - State persists across database operations (Priority: P1)

The new state value can be written to and read from the PostgreSQL database without schema errors. Existing data and workflows using other states are unaffected.

**Why this priority**: The Alembic migration is a prerequisite for any production deployment of the new state.

**Independent Test**: Run `alembic upgrade head` on a test database and verify the state can be stored and retrieved. Run `alembic downgrade` and verify rollback succeeds.

**Acceptance Scenarios**:

1. **Given** a PostgreSQL database at the current schema version, **When** the new Alembic migration runs, **Then** the `moonmindworkflowstate` enum type includes `waiting_on_dependencies`.
2. **Given** a database with the new migration applied, **When** a row is inserted with `state = 'waiting_on_dependencies'`, **Then** the value is stored and retrievable without error.
3. **Given** a database with the new migration, **When** the migration is downgraded, **Then** the `waiting_on_dependencies` value is removed and the database returns to the previous schema.

---

### User Story 3 - Compatibility layer recognizes new state (Priority: P2)

The compatibility status mapping layer used by the legacy API path correctly translates the new state, so existing dashboard query patterns continue to work.

**Why this priority**: Ensures backward-compatible API consumers can interpret the new state without breaking.

**Independent Test**: Call the compatibility status mapping function with the new state and verify it returns a valid status string.

**Acceptance Scenarios**:

1. **Given** the compatibility status map in `compatibility.py`, **When** `MoonMindWorkflowState.WAITING_ON_DEPENDENCIES` is looked up, **Then** a dashboard-friendly status string is returned.
2. **Given** the reverse mapping (dashboard status → enum states), **When** the new dashboard status is queried, **Then** it includes `WAITING_ON_DEPENDENCIES` in the result set.

---

### Edge Cases

- What happens if the migration is applied to a database that already has an unrelated pending migration? The migration must be compatible with Alembic's linear revision chain.
- What happens if an older version of the API service encounters a row with `state = 'waiting_on_dependencies'`? Projection sync currently warns and ignores unknown values — this is acceptable for rolling deploys.
- What happens if the workflow constant is used in `run.py` but the database migration hasn't been applied yet? The workflow can set the state locally, but the projection sync will log a warning until the DB migration is deployed.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST add `WAITING_ON_DEPENDENCIES = "waiting_on_dependencies"` to the `MoonMindWorkflowState` enum in `api_service/db/models.py`. (DOC-REQ-002, DOC-REQ-007)
- **FR-002**: System MUST generate an Alembic migration that adds `waiting_on_dependencies` to the PostgreSQL `moonmindworkflowstate` native enum type. The migration MUST support both upgrade and downgrade. (DOC-REQ-003)
- **FR-003**: System MUST add `STATE_WAITING_ON_DEPENDENCIES = "waiting_on_dependencies"` as a module-level constant in `moonmind/workflows/temporal/workflows/run.py`. (DOC-REQ-004)
- **FR-004**: System MUST update the projection sync state mapping in `api_service/core/sync.py` so that `mm_state = "waiting_on_dependencies"` is recognized and mapped to `MoonMindWorkflowState.WAITING_ON_DEPENDENCIES`. (DOC-REQ-005)
- **FR-005**: System MUST add `MoonMindWorkflowState.WAITING_ON_DEPENDENCIES` to the dashboard status mapping in `api_service/api/routers/executions.py` with an appropriate dashboard status value. (DOC-REQ-006)
- **FR-006**: System MUST add `MoonMindWorkflowState.WAITING_ON_DEPENDENCIES` to the compatibility status mapping in `moonmind/workflows/tasks/compatibility.py`. (DOC-REQ-006)
- **FR-007**: The new enum value MUST use lowercase naming consistent with existing values (`initializing`, `planning`, `executing`, etc.). (DOC-REQ-007)
- **FR-008**: All existing unit tests MUST continue to pass after the changes. No regressions.

### Key Entities

- **MoonMindWorkflowState**: The enum defining all lifecycle states for workflows visible in the dashboard. Extended with a new `WAITING_ON_DEPENDENCIES` member.
- **TemporalExecutionCanonicalRecord / TemporalExecutionRecord**: Database models whose `state` column references `MoonMindWorkflowState`. The new value must be a valid column value.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The `MoonMindWorkflowState` enum has exactly one new member `WAITING_ON_DEPENDENCIES` with the string value `"waiting_on_dependencies"`.
- **SC-002**: The Alembic migration applies and rolls back cleanly on PostgreSQL.
- **SC-003**: The projection sync function accepts `mm_state = "waiting_on_dependencies"` without logging warnings or raising errors.
- **SC-004**: Both dashboard status maps (executions router + compatibility layer) return a valid status for the new state.
- **SC-005**: All existing unit tests pass with zero regressions (`./tools/test_unit.sh` exit code 0).
