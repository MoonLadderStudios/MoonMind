# Tasks: Typed Workflow Messages

**Input**: Design documents from `/specs/174-typed-workflow-messages/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Test Commands**:

- Unit tests: `pytest tests/unit/schemas/test_managed_session_models.py tests/unit/workflows/temporal/workflows/test_agent_session.py -q`
- Integration tests: `pytest tests/integration/services/temporal/workflows/test_agent_session_lifecycle.py -q`
- Final verification: `/speckit.verify`

## Phase 1: Setup

- [X] T001 Inspect existing managed-session workflow and schema contracts in `moonmind/workflows/temporal/workflows/agent_session.py` and `moonmind/schemas/managed_session_models.py` (FR-001-FR-011)
- [X] T002 Confirm existing story artifacts do not exist and create `/specs/174-typed-workflow-messages/` (SC-001)

## Phase 2: Story - Typed Managed Session Controls

**Summary**: As an operator relying on long-running managed sessions, I want managed-session workflow inputs, continuation state, and control messages to use explicit typed contracts so live sessions remain deterministic, epoch-safe, idempotent, and controllable.

**Independent Test**: Validate schema models, workflow validators, and lifecycle signal/update/query behavior.

**Traceability**: FR-001-FR-011, SC-001-SC-004, DESIGN-REQ-012-DESIGN-REQ-016

### Unit Tests

- [X] T003 [P] Add schema tests for `CodexManagedSessionAttachRuntimeHandlesSignal` in `tests/unit/schemas/test_managed_session_models.py` (FR-004, DESIGN-REQ-013)
- [X] T004 [P] Add schema tests for explicit clear/cancel/terminate update request models in `tests/unit/schemas/test_managed_session_models.py` (FR-005, DESIGN-REQ-013, DESIGN-REQ-015)
- [X] T005 [P] Update Continue-As-New workflow test to assert the handoff is `CodexManagedSessionWorkflowInput` in `tests/unit/workflows/temporal/workflows/test_agent_session.py` (FR-003, FR-009, DESIGN-REQ-012, DESIGN-REQ-015)

### Implementation

- [X] T006 Add explicit typed managed-session signal and update request contracts in `moonmind/schemas/managed_session_models.py` (FR-004, FR-005)
- [X] T007 Export new managed-session contracts from `moonmind/schemas/__init__.py` (FR-004, FR-005)
- [X] T008 Update `attach_runtime_handles` to use `CodexManagedSessionAttachRuntimeHandlesSignal` in `moonmind/workflows/temporal/workflows/agent_session.py` (FR-004)
- [X] T009 Update clear/cancel/terminate updates and validators to use operation-specific request models in `moonmind/workflows/temporal/workflows/agent_session.py` (FR-005, FR-006, FR-007)
- [X] T010 Validate Continue-As-New payload construction through `CodexManagedSessionWorkflowInput` in `moonmind/workflows/temporal/workflows/agent_session.py` (FR-003, FR-009)

### Validation

- [X] T011 Run focused unit tests for managed-session schemas and workflow behavior (SC-001, SC-002, SC-003)
- [X] T012 Run lifecycle integration test for managed-session signal/update/query behavior (SC-004)
- [X] T013 Run full `./tools/test_unit.sh` suite
- [X] T014 Run `/speckit.verify` final verification

## Dependencies & Execution Order

- T003-T005 define the regression coverage before production code is finalized.
- T006-T010 implement the typed runtime contracts.
- T011-T014 validate the completed story.
