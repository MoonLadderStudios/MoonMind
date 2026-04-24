# Tasks: Report Semantics Rollout

**Input**: Design documents from `specs/249-report-semantics-rollout/`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/report-rollout-semantics-contract.md`, `quickstart.md`

**Tests**: Unit tests, execution-API contract verification, and focused Mission Control UI verification are REQUIRED. For MM-497, run verification-first checks before any production changes, confirm failures only if new verification coverage is added, and implement only if those checks expose drift from the spec.

**Organization**: Tasks are grouped around the single MM-497 story: preserve generic-output compatibility while newer report-producing workflows use explicit `report.*` semantics, representative report mappings remain valid, and deferred rollout choices stay explicit in downstream artifacts.

**Source Traceability**: MM-497; FR-001 through FR-007; acceptance scenarios 1-6; SC-001 through SC-007; DESIGN-REQ-021, DESIGN-REQ-023, DESIGN-REQ-024.

**Requirement Status Summary**: verification-first = 5 (`FR-001`, `FR-002`, `FR-004`, `FR-005`, `DESIGN-REQ-021`); conditional fallback = 3 (`FR-003`, `DESIGN-REQ-023`, `DESIGN-REQ-024`); traceability-only = 2 (`FR-006`, `FR-007`); existing-evidence rows = 5 (`FR-001`, `FR-002`, `FR-004`, `FR-005`, `DESIGN-REQ-021`).

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_report_workflow_rollout.py tests/unit/workflows/temporal/test_artifacts.py tests/unit/api/routers/test_executions.py --ui-args frontend/src/entrypoints/task-detail.test.tsx`
- Execution API contract verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/contract/test_temporal_execution_api.py`
- Hermetic integration escalation: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup

**Purpose**: Confirm the planning artifacts and current verification targets for the single MM-497 story.

- [X] T001 Confirmed `specs/249-report-semantics-rollout/` contains `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/report-rollout-semantics-contract.md`, and `quickstart.md`
- [X] T002 Inspected rollout source requirements in `docs/Artifacts/ReportArtifacts.md` sections 2, 5, 17, 19, 20, and 21 before verification work began, confirming generic-output compatibility, representative mappings, migration guidance, and deferred rollout questions remained explicit (FR-001 through FR-006, DESIGN-REQ-021, DESIGN-REQ-023, DESIGN-REQ-024)
- [X] T003 Inspected current rollout verification targets in `tests/unit/workflows/temporal/test_report_workflow_rollout.py`, `tests/unit/workflows/temporal/test_artifacts.py`, `tests/unit/api/routers/test_executions.py`, `tests/contract/test_temporal_execution_api.py`, and `frontend/src/entrypoints/task-detail.test.tsx` before tightening traceability, confirming the repo already carried focused MM-497 evidence (FR-001 through FR-005, SC-001 through SC-005)

---

## Phase 2: Foundational

**Purpose**: Validate that MM-497 reuses the existing report artifact infrastructure and requires no new storage, route, or service foundation.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T004 Confirmed no database migration or new persistent storage is required because MM-497 preserves the existing artifact-backed rollout behavior and introduces no new report storage model (FR-003, FR-004, DESIGN-REQ-023)
- [X] T005 Confirmed no new API route or report storage endpoint is required because MM-497 verifies the staged rollout contract rather than adding a new report subsystem (FR-003, DESIGN-REQ-021, DESIGN-REQ-024)
- [X] T006 Confirmed the canonical rollout/runtime surfaces remain `moonmind/workflows/temporal/report_artifacts.py`, `moonmind/workflows/temporal/artifacts.py`, `api_service/api/routers/executions.py`, and `frontend/src/entrypoints/task-detail.tsx` for this story (FR-001 through FR-005)

**Checkpoint**: Foundation ready - story verification and any fallback implementation work can now begin.

---

## Phase 3: Story - Roll Out Report Semantics Incrementally

**Summary**: As a MoonMind maintainer, I want report semantics to roll out incrementally so existing generic outputs continue working while new report-producing workflows adopt explicit report conventions.

**Independent Test**: Exercise one existing generic-output workflow and one report-producing workflow, then verify the generic workflow remains non-report output by default, the report workflow uses explicit report conventions, migration-safe semantics remain bounded, and deferred product choices are preserved rather than being silently implied.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, SC-007, DESIGN-REQ-021, DESIGN-REQ-023, DESIGN-REQ-024

**Unit Test Plan**:

- Verify rollout validation, generic-output fallback behavior, explicit `report.*` semantics, and representative workflow mappings in `tests/unit/workflows/temporal/test_report_workflow_rollout.py`.
- Verify artifact classification and supporting rollout guardrails in `tests/unit/workflows/temporal/test_artifacts.py`.
- Verify execution-detail report projection behavior continues to coexist safely with explicit report semantics in `tests/unit/api/routers/test_executions.py`.

**Integration Test Plan**:

- Verify execution-detail report projection and canonical report semantics remain server-defined through `tests/contract/test_temporal_execution_api.py`.
- Verify Mission Control continues to surface canonical reports without browser-side heuristics in `frontend/src/entrypoints/task-detail.test.tsx`.
- Escalate to `./tools/test_integration.sh` only if fallback implementation changes artifact persistence, activity publication boundaries, or compose-backed serialization behavior.

### Unit Verification Tests (write first)

- [X] T007 [P] Reviewed `tests/unit/workflows/temporal/test_report_workflow_rollout.py` coverage for FR-001, FR-002, FR-005, SC-001, SC-002, SC-005, DESIGN-REQ-021, and DESIGN-REQ-024; existing rollout assertions already kept generic outputs valid and representative report mappings explicit
- [X] T008 [P] Reviewed `tests/unit/workflows/temporal/test_artifacts.py` guardrail coverage for FR-003, FR-004, SC-003, SC-004, and DESIGN-REQ-023; existing artifact assertions already preserved the staged rollout boundary and out-of-scope guardrails
- [X] T009 [P] Reviewed `tests/unit/api/routers/test_executions.py` coverage for FR-003 and SC-003; existing execution-detail assertions already showed report behavior coexisting with the staged rollout path rather than implying a flag-day migration

### Integration-Style Boundary Tests (write first)

- [X] T010 [P] Reviewed `tests/contract/test_temporal_execution_api.py` coverage for FR-003, SC-003, and DESIGN-REQ-021; existing contract assertions already kept canonical report projections server-defined and artifact-backed
- [X] T011 [P] Reviewed `frontend/src/entrypoints/task-detail.test.tsx` coverage for FR-003, FR-005, SC-002, and SC-005; existing Mission Control assertions already consumed explicit report semantics without browser-side guessing
- [X] T012 [P] Updated `specs/249-report-semantics-rollout/quickstart.md` and later verification notes for FR-006, SC-006, FR-007, and SC-007 so deferred decisions and MM-497 remain explicit

### Red-First Confirmation

- [X] T013 Ran `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_report_workflow_rollout.py tests/unit/workflows/temporal/test_artifacts.py tests/unit/api/routers/test_executions.py --ui-args frontend/src/entrypoints/task-detail.test.tsx`; no new MM-497-specific unit assertions were required, and the existing verification remained green with 171 Python tests and 85 focused UI tests passing
- [X] T014 Ran `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/contract/test_temporal_execution_api.py`; no new MM-497 contract assertions were required, and the existing contract boundary remained green with 8 Python contract tests and 14 wrapped frontend Vitest files passing

### Fallback Implementation Tasks (only if T013 or T014 exposes drift)

- [X] T015 No update was required in `moonmind/workflows/temporal/report_artifacts.py` because T013 exposed no drift in generic-output compatibility, explicit `report.*` semantics, or representative workflow mappings (FR-001, FR-002, FR-005, DESIGN-REQ-021, DESIGN-REQ-024)
- [X] T016 No update was required in `moonmind/workflows/temporal/artifacts.py`, `api_service/api/routers/executions.py`, or `frontend/src/entrypoints/task-detail.tsx` because T013 and T014 exposed no staged-rollout or canonical-report-consumption drift (FR-003, DESIGN-REQ-021, DESIGN-REQ-023)
- [X] T017 No story-level spec, plan, research, data-model, or contract correction was required after verification; traceability remained aligned, and the only feature-local artifact update was the planned `quickstart.md` clarification captured in T012 (FR-006, FR-007, SC-006, SC-007)

### Story Validation

- [X] T018 Reran the focused unit command from T013 until MM-497 verification passed for FR-001 through FR-005, SC-001 through SC-005, and DESIGN-REQ-021/023/024
- [X] T019 Reran the execution API contract command from T014 until MM-497 boundary verification passed for FR-003, SC-003, and DESIGN-REQ-021
- [X] T020 Ran `rg -n "MM-497|DESIGN-REQ-021|DESIGN-REQ-023|DESIGN-REQ-024|report\.primary|output\.primary|report_type|auto-pinning|projection timing|export semantics|evidence grouping|multi-step" specs/249-report-semantics-rollout docs/tmp/jira-orchestration-inputs/MM-497-moonspec-orchestration-input.md docs/Artifacts/ReportArtifacts.md` to verify traceability and deferred-decision preservation (FR-006, FR-007, SC-006, SC-007)
- [X] T021 Hermetic integration escalation was not required because MM-497 remained a verification-first story and no fallback implementation crossed the artifact persistence, activity publication, or compose-backed API serialization boundary

**Checkpoint**: MM-497 rollout behavior is verified against the current repo, and any code changes made were limited to the smallest contract-preserving fix required by failed verification.

---

## Phase 4: Polish And Verification

**Purpose**: Complete final validation and preserve story-level evidence without adding hidden scope.

- [X] T022 [P] No additional `quickstart.md` update was required because the executed MM-497 verification commands and escalation criteria matched the current plan after the T012 clarification
- [X] T023 Ran `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`; the canonical unit suite passed with 3956 Python tests passed, 1 xpassed, 104 warnings, 16 subtests passed, and the wrapped frontend Vitest suite passed 14 files / 415 tests
- [X] T024 Ran `/moonspec-verify` equivalent for `specs/249-report-semantics-rollout/` and produced `specs/249-report-semantics-rollout/verification.md` covering MM-497, FR-001 through FR-007, SC-001 through SC-007, and DESIGN-REQ-021, DESIGN-REQ-023, DESIGN-REQ-024

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (Phase 1): no dependencies.
- Foundational (Phase 2): depends on Setup completion.
- Story (Phase 3): depends on Foundational completion.
- Polish (Phase 4): depends on focused story verification completing.

### Within The Story

- T007-T012 must complete before red-first confirmation.
- T013-T014 must confirm verification behavior before T015-T017 begin.
- T015-T017 are conditional fallback work only when verification exposes drift.
- T018-T021 validate story completion after verification or fallback fixes.
- T024 depends on validation completion.

### Parallel Opportunities

- T007-T011 can run in parallel because they touch different verification files.
- T012 can run in parallel with T007-T011 once the planning artifacts are stable.
- T022 can run in parallel with verification preparation after T021 completes.

## Implementation Strategy

1. Confirm the MM-497 planning artifacts and the current rollout verification targets.
2. Tighten focused unit, contract, and UI verification first.
3. Confirm verification behavior with focused commands before changing production code.
4. Apply the smallest contract-preserving fallback fix only if verification exposes drift.
5. Rerun focused verification, escalate to hermetic integration only when the changed surface warrants it, and close with full unit validation.
6. Preserve MM-497 traceability and finish with `/moonspec-verify`.
