# Tasks: Submit Discriminated Executable Payloads

**Input**: `specs/279-submit-discriminated-executable-payloads/spec.md` and `specs/279-submit-discriminated-executable-payloads/plan.md`

**Prerequisites**: spec, plan, research, data model, contract, and quickstart complete.

**Unit Test Command**: `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py`

**Frontend Test Command**: `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`

**Integration Test Strategy**: No compose-backed `integration_ci` test is required for this narrow payload-contract story because there is no new database, external service, or worker-topology behavior. Integration-boundary coverage is provided by the Create-page submission test in `frontend/src/entrypoints/task-create.test.tsx`, which exercises preset expansion through submitted task payload shape, and by runtime materialization tests at the worker boundary.

**Final Verification Command**: `./tools/test_unit.sh`

**Source Traceability**: MM-559 Jira preset brief is preserved in `spec.md`. Tasks cover FR-001 through FR-007, SC-001 through SC-004, and DESIGN-REQ-008, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-016, and DESIGN-REQ-019.

## Phase 1: Setup

- [X] T001 Confirm `.specify/feature.json` points to `specs/279-submit-discriminated-executable-payloads` and the requirements checklist is complete.
- [X] T002 Inspect existing task contract, runtime materializer, and Create-page submission tests in `moonmind/workflows/tasks/task_contract.py`, `moonmind/workflows/temporal/worker_runtime.py`, and `frontend/src/entrypoints/task-create.test.tsx`.

## Phase 2: Foundational Tests

- [X] T003 [P] Add failing task contract unit tests for explicit Tool, Skill, Preset rejection, Activity rejection, conflicting payloads, and provenance preservation in `tests/unit/workflows/tasks/test_task_contract.py` for FR-001, FR-004, FR-005, FR-006, FR-007, DESIGN-REQ-008, DESIGN-REQ-012, DESIGN-REQ-016, and DESIGN-REQ-019.
- [X] T004 [P] Add failing runtime materialization unit tests for explicit Tool and Skill mapping and provenance-agnostic mapping in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py` for FR-002, FR-006, SCN-001, SCN-002, SCN-004, and DESIGN-REQ-011.
- [X] T005 [P] Add failing Create-page integration-boundary submission expectations that applied preset steps include explicit `type` values in `frontend/src/entrypoints/task-create.test.tsx` for FR-003, SC-003, and DESIGN-REQ-016.
- [X] T006 [P] Confirm compose-backed integration is not required and record integration-boundary coverage in `specs/279-submit-discriminated-executable-payloads/tasks.md`.
- [X] T007 Run the focused failing tests and record the expected red result in `specs/279-submit-discriminated-executable-payloads/tasks.md`.

## Phase 3: Story Implementation

**Story**: Submitted task steps use explicit executable Tool/Skill discriminators and reject unresolved Preset or Activity labels.

**Independent Test**: Validate task payloads and materialize runtime plans for Tool, Skill, Preset, Activity, conflicting payloads, and preset provenance cases.

**Traceability IDs**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, SC-001, SC-002, SC-003, SC-004, DESIGN-REQ-008, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-016, DESIGN-REQ-019.

- [X] T008 Implement submitted step discriminator validation in `moonmind/workflows/tasks/task_contract.py` for FR-001, FR-004, FR-005, and FR-007.
- [X] T009 Implement explicit Step Type aware runtime plan mapping in `moonmind/workflows/temporal/worker_runtime.py` for FR-002, FR-006, and DESIGN-REQ-011.
- [X] T010 Implement explicit `type` serialization for Create-page submitted Tool and Skill steps in `frontend/src/entrypoints/task-create.tsx` for FR-003 and DESIGN-REQ-016.
- [X] T011 Run focused backend and frontend tests and fix implementation defects.

## Phase 4: Story Validation And Polish

- [X] T012 Validate the single story end-to-end against Tool, Skill, Preset rejection, Activity rejection, provenance, and Create-page submission scenarios in `specs/279-submit-discriminated-executable-payloads/verification.md`.
- [X] T013 Update `specs/279-submit-discriminated-executable-payloads/tasks.md` checkboxes and red/green evidence.
- [X] T014 Review changed code for MM-559 traceability, no raw credentials, and no Temporal Activity Step Type leakage.

## Phase 5: Final Verification

- [X] T015 Run `./tools/test_unit.sh` for final unit verification or record the exact blocker.
- [X] T016 Run `/moonspec-verify` equivalent by comparing implementation, tests, and artifacts against `specs/279-submit-discriminated-executable-payloads/spec.md`, then write `specs/279-submit-discriminated-executable-payloads/verification.md`.

## Execution Evidence

- Red-first backend run: `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py` initially failed on missing submitted Step Type validation and explicit Tool/Skill runtime mapping.
- Focused backend green run: `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py` passed with 69 Python tests.
- Focused frontend integration-boundary green run: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` passed with 213 tests.
- TypeScript check: `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json` passed.
- Final full unit run: `./tools/test_unit.sh` passed with 4215 Python tests, 16 subtests, and 471 frontend tests.
