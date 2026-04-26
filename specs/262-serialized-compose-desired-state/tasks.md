# Tasks: Serialized Compose Desired-State Execution

**Input**: `specs/262-serialized-compose-desired-state/spec.md`, `specs/262-serialized-compose-desired-state/plan.md`
**Prerequisites**: `research.md`, `data-model.md`, `contracts/deployment-update-execution.md`, `quickstart.md`
**Unit test command**: `pytest tests/unit/workflows/skills/test_deployment_update_execution.py tests/unit/workflows/skills/test_deployment_tool_contracts.py -q`
**Integration test command**: `pytest tests/integration/temporal/test_deployment_update_execution_contract.py -q`

**Source Traceability**: `MM-520`; FR-001 through FR-012; SCN-001 through SCN-007; SC-001 through SC-006; DESIGN-REQ-001 through DESIGN-REQ-006.

## Phase 1: Setup

- [X] T001 Verify active feature pointer `.specify/feature.json` references `specs/262-serialized-compose-desired-state`.
- [X] T002 Confirm existing deployment API/tool contract evidence in `api_service/services/deployment_operations.py`, `moonmind/workflows/skills/deployment_tools.py`, and `tests/unit/workflows/skills/test_deployment_tool_contracts.py`.

## Phase 2: Foundational

- [X] T003 Add deployment update execution module structure in `moonmind/workflows/skills/deployment_execution.py` with injectable lock, desired-state store, artifact writer, and runner boundaries. (FR-001 through FR-011)
- [X] T004 Register a deployment tool handler helper from `moonmind/workflows/skills/deployment_execution.py` without changing the typed contract in `moonmind/workflows/skills/deployment_tools.py`. (FR-009, FR-011)

## Phase 3: Serialized Compose Desired-State Execution

**Story**: Serialize update runs per stack and persist desired state before policy-controlled Compose recreation.

**Independent Test**: Invoke the typed handler with fake infrastructure and assert lock behavior, ordering, command construction, and result semantics.

**Traceability IDs**: FR-001 through FR-012; SCN-001 through SCN-007; DESIGN-REQ-001 through DESIGN-REQ-006.

### Tests First

- [X] T005 [P] Add failing unit tests for same-stack lock contention and side-effect-free `DEPLOYMENT_LOCKED` in `tests/unit/workflows/skills/test_deployment_update_execution.py`. (FR-001, FR-002, SCN-001, DESIGN-REQ-002)
- [X] T006 [P] Add failing unit tests for before-state, desired-state persistence, pull, up, verification, and after-state ordering in `tests/unit/workflows/skills/test_deployment_update_execution.py`. (FR-003, FR-004, SCN-002, DESIGN-REQ-001, DESIGN-REQ-003)
- [X] T007 [P] Add failing unit tests for desired-state payload fields in `tests/unit/workflows/skills/test_deployment_update_execution.py`. (FR-005, DESIGN-REQ-001)
- [X] T008 [P] Add failing unit tests for `changed_services`, `force_recreate`, `removeOrphans`, and `wait` command construction in `tests/unit/workflows/skills/test_deployment_update_execution.py`. (FR-006, FR-007, FR-008, SCN-003, SCN-004, SCN-005, DESIGN-REQ-004)
- [X] T009 [P] Add failing unit tests for verification failure result semantics and evidence refs in `tests/unit/workflows/skills/test_deployment_update_execution.py`. (FR-009, FR-010, SCN-006, DESIGN-REQ-005)
- [X] T010 [P] Add failing unit tests for closed runner modes and no caller runner image/path inputs in `tests/unit/workflows/skills/test_deployment_update_execution.py`. (FR-011, SCN-007, DESIGN-REQ-006)
- [X] T011 [P] Add failing hermetic integration test for `mm.tool.execute` dispatch of `deployment.update_compose_stack` in `tests/integration/temporal/test_deployment_update_execution_contract.py`. (FR-001 through FR-011)

### Implementation

- [X] T012 Implement per-stack nonblocking lock manager and `DEPLOYMENT_LOCKED` `ToolFailure` in `moonmind/workflows/skills/deployment_execution.py`. (FR-001, FR-002)
- [X] T013 Implement desired-state payload creation and store invocation before Compose up in `moonmind/workflows/skills/deployment_execution.py`. (FR-003, FR-004, FR-005)
- [X] T014 Implement typed pull/up command construction in `moonmind/workflows/skills/deployment_execution.py`. (FR-006, FR-007, FR-008)
- [X] T015 Implement evidence artifact writer calls, verification result mapping, and structured tool output in `moonmind/workflows/skills/deployment_execution.py`. (FR-009, FR-010)
- [X] T016 Implement closed deployment runner mode validation and registration helper in `moonmind/workflows/skills/deployment_execution.py`. (FR-011)
- [X] T017 Wire deployment tool handler registration into `moonmind/workflows/temporal/worker_runtime.py` for the skill dispatcher. (FR-009, FR-011)

### Validation

- [X] T018 Run focused unit tests for deployment execution and tool contract until passing: `pytest tests/unit/workflows/skills/test_deployment_update_execution.py tests/unit/workflows/skills/test_deployment_tool_contracts.py -q`.
- [X] T019 Run focused hermetic integration test until passing: `pytest tests/integration/temporal/test_deployment_update_execution_contract.py -q`.

## Final Phase: Polish and Verification

- [X] T020 Run traceability grep for `MM-520`, `DESIGN-REQ-001`, and `deployment.update_compose_stack` across the feature artifacts, code, and tests.
- [X] T021 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for final unit verification.
- [X] T022 Run `./tools/test_integration.sh` when Docker is available; otherwise record the exact blocker.
- [X] T023 Run `/moonspec-verify` for `specs/262-serialized-compose-desired-state/` and produce final verification evidence.

## Dependencies and Execution Order

1. T001-T004 before story tests.
2. T005-T011 before T012-T017.
3. T012-T017 before T018-T019.
4. T018-T019 before T020-T023.

## Parallel Examples

- T005 through T011 can be drafted in parallel because they target independent assertions in new test files.
- T012 through T016 are sequential in one production module.

## Implementation Strategy

Write failing tests first against the new execution boundary, then implement the smallest lifecycle module that satisfies lock, persistence, command construction, evidence, verification, and runner-mode requirements. Keep privileged Docker access injectable and hermetic in tests.
