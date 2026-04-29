# Tasks: Compile Step Type Payloads Into Runtime Plans and Promotable Proposals

**Input**: `specs/286-compile-step-type-payloads/spec.md` and `specs/286-compile-step-type-payloads/plan.md`

**Prerequisites**: spec, plan, research, data model, contract, and quickstart complete.

**Unit Test Command**: `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/workflows/task_proposals/test_service.py tests/unit/api/routers/test_task_proposals.py`
**Integration Test Strategy**: No compose-backed `integration_ci` test is required because MM-567 verifies deterministic payload validation, runtime plan construction, proposal promotion validation, and API serialization without new storage, service topology, or external provider behavior.
**Final Verification Command**: `./tools/test_unit.sh`

**Source Traceability**: MM-567 Jira preset brief is preserved in `spec.md`. Tasks cover FR-001 through FR-008, SCN-001 through SCN-006, SC-001 through SC-006, and DESIGN-REQ-008, DESIGN-REQ-013, DESIGN-REQ-016, DESIGN-REQ-018, and DESIGN-REQ-019.

## Phase 1: Setup

- [X] T001 Confirm active MM-567 artifacts under `specs/286-compile-step-type-payloads/` and `.specify/feature.json`.
- [X] T002 Inspect source design sections in `docs/Steps/StepTypes.md` and preserve DESIGN-REQ-008, DESIGN-REQ-013, DESIGN-REQ-016, DESIGN-REQ-018, and DESIGN-REQ-019 mappings in `specs/286-compile-step-type-payloads/spec.md`.

## Phase 2: Foundational

- [X] T003 Confirm no database migration, service dependency, or compose integration harness is required for MM-567 in `specs/286-compile-step-type-payloads/plan.md`.
- [X] T004 Confirm existing implementation surfaces in `moonmind/workflows/tasks/task_contract.py`, `moonmind/workflows/temporal/worker_runtime.py`, `moonmind/workflows/task_proposals/service.py`, and `api_service/api/routers/task_proposals.py`.

## Phase 3: Story

**Story**: Executable Tool and Skill payloads compile into runtime plan nodes and promotable proposals without hidden preset execution or user-facing Temporal terminology.

**Independent Test**: Materialize explicit Tool and Skill steps, validate proposal promotion from stored flat payloads, and reject unresolved Preset or Activity Step Types.

**Traceability IDs**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SCN-006, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-008, DESIGN-REQ-013, DESIGN-REQ-016, DESIGN-REQ-018, DESIGN-REQ-019.

- [X] T005 [P] Verify explicit Tool and Skill runtime planner coverage in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py` for FR-001, FR-002, SCN-001, SCN-002, SC-001, DESIGN-REQ-013, and DESIGN-REQ-018.
- [X] T006 [P] Verify executable boundary validation coverage in `tests/unit/workflows/tasks/test_task_contract.py` for FR-005, FR-008, SCN-005, SCN-006, SC-002, DESIGN-REQ-008, and DESIGN-REQ-019.
- [X] T007 [P] Verify proposal promotion coverage in `tests/unit/workflows/task_proposals/test_service.py` for FR-003, FR-004, FR-006, FR-007, SCN-003, SCN-004, SCN-005, SC-003, DESIGN-REQ-016, and DESIGN-REQ-018.
- [X] T008 [P] Verify proposal preview provenance coverage in `tests/unit/api/routers/test_task_proposals.py` for FR-003, SC-004, and DESIGN-REQ-016.
- [X] T009 Verify canonical Step Types documentation in `docs/Steps/StepTypes.md` for SC-005 and DESIGN-REQ-019.
- [X] T010 Run focused unit command from this task file and record the result in `specs/286-compile-step-type-payloads/verification.md`.

## Final Phase: Polish And Verification

- [X] T011 Run final `./tools/test_unit.sh` or record the exact blocker in `specs/286-compile-step-type-payloads/verification.md`.
- [X] T012 Run `/moonspec-verify` equivalent and write `specs/286-compile-step-type-payloads/verification.md`.

## Execution Evidence

- Focused runtime/proposal validation: `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/workflows/task_proposals/test_service.py tests/unit/api/routers/test_task_proposals.py` passed with 102 Python tests and the wrapper frontend run passed 478 tests.
- Final full unit run: `./tools/test_unit.sh` passed with 4221 Python tests, 1 xpassed, 16 subtests passed, and 478 frontend tests.
