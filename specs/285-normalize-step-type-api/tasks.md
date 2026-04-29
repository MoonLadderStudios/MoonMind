# Tasks: Normalize Step Type API and Executable Submission Payloads

**Input**: `specs/285-normalize-step-type-api/spec.md` and `specs/285-normalize-step-type-api/plan.md`

**Prerequisites**: spec, plan, research, data model, contract, and quickstart complete.

**Unit Test Command**: `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py`
**Frontend Test Command**: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
**Integration Test Strategy**: No compose-backed `integration_ci` test is required because this story changes draft reconstruction and executable validation contracts without new storage, service topology, or external provider behavior. Frontend edit/rerun reconstruction tests serve as the integration-boundary coverage for draft APIs.
**Final Verification Command**: `./tools/test_unit.sh`

**Source Traceability**: MM-566 Jira preset brief is preserved in `spec.md`. Tasks cover FR-001 through FR-007, SCN-001 through SCN-006, SC-001 through SC-005, and DESIGN-REQ-012, DESIGN-REQ-014, DESIGN-REQ-015, and DESIGN-REQ-019.

## Phase 1: Setup

- [X] T001 Confirm active MM-566 artifacts under `specs/285-normalize-step-type-api/` and `.specify/feature.json`.
- [X] T002 Inspect existing MM-559 task contract implementation and Step Type source doc in `moonmind/workflows/tasks/task_contract.py`, `tests/unit/workflows/tasks/test_task_contract.py`, and `docs/Steps/StepTypes.md`.

## Phase 2: Foundational

- [X] T003 Confirm no database migration, service dependency, or compose integration harness is required for MM-566 in `specs/285-normalize-step-type-api/plan.md`.

## Phase 3: Story

**Story**: Draft APIs represent Tool, Skill, and Preset Step Types explicitly while executable submissions accept only Tool and Skill.

**Independent Test**: Reconstruct explicit Tool, Skill, Preset, and legacy step payloads; validate executable submission rejects Preset, Activity, and mixed payloads.

**Traceability IDs**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SCN-006, SC-001, SC-002, SC-003, SC-004, DESIGN-REQ-012, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-019.

- [X] T004 [P] Add failing frontend draft reconstruction tests for explicit Tool, Skill, Preset, and legacy Skill shapes in `frontend/src/entrypoints/task-create.test.tsx` for FR-001, FR-004, FR-006, SCN-001, SCN-002, SCN-003, and SCN-006.
- [X] T005 Run focused frontend test command and record the red result in `specs/285-normalize-step-type-api/tasks.md`.
- [X] T006 Implement explicit draft Step Type preservation in `frontend/src/lib/temporalTaskEditing.ts` for FR-001, FR-004, FR-006, DESIGN-REQ-012, and DESIGN-REQ-015.
- [X] T007 Verify Step Type documentation convergence in `docs/Steps/StepTypes.md` for FR-007, SC-004, and DESIGN-REQ-019.
- [X] T008 Run focused frontend and backend validation commands and fix defects.
- [X] T009 Validate story evidence against executable boundary tests in `tests/unit/workflows/tasks/test_task_contract.py` for FR-002, FR-003, FR-005, SCN-004, SCN-005, and DESIGN-REQ-014.

## Final Phase: Polish And Verification

- [X] T010 Run final `./tools/test_unit.sh` or record the exact blocker.
- [X] T011 Run `/moonspec-verify` equivalent and write `specs/285-normalize-step-type-api/verification.md`.

## Execution Evidence

- Red-first frontend run: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` initially failed in `reconstructs ordered editable steps from Temporal execution fields` because draft reconstruction now preserved the original `skill` payload and the pre-existing expectation did not account for it.
- Focused frontend green run: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` passed with 219 tests.
- Focused backend validation: `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py` passed the focused 25 Python task-contract tests and the wrapper's 477 frontend tests.
- TypeScript check: `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json` passed.
- Final full unit run: `./tools/test_unit.sh` passed with 4221 Python tests, 1 xpass, 16 subtests, and 477 frontend tests.
