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

- [ ] T001 Confirm `specs/249-report-semantics-rollout/` contains `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/report-rollout-semantics-contract.md`, and `quickstart.md`
- [ ] T002 Inspect rollout source requirements in `docs/Artifacts/ReportArtifacts.md` sections 2, 5, 17, 19, 20, and 21 before verification work begins (FR-001 through FR-006, DESIGN-REQ-021, DESIGN-REQ-023, DESIGN-REQ-024)
- [ ] T003 Inspect current rollout verification targets in `tests/unit/workflows/temporal/test_report_workflow_rollout.py`, `tests/unit/workflows/temporal/test_artifacts.py`, `tests/unit/api/routers/test_executions.py`, `tests/contract/test_temporal_execution_api.py`, and `frontend/src/entrypoints/task-detail.test.tsx` before adding or tightening coverage (FR-001 through FR-005, SC-001 through SC-005)

---

## Phase 2: Foundational

**Purpose**: Validate that MM-497 reuses the existing report artifact infrastructure and requires no new storage, route, or service foundation.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [ ] T004 Confirm no database migration or new persistent storage is required because MM-497 preserves the existing artifact-backed rollout behavior and introduces no new report storage model (FR-003, FR-004, DESIGN-REQ-023)
- [ ] T005 Confirm no new API route or report storage endpoint is required because MM-497 verifies the staged rollout contract rather than adding a new report subsystem (FR-003, DESIGN-REQ-021, DESIGN-REQ-024)
- [ ] T006 Confirm the canonical rollout/runtime surfaces remain `moonmind/workflows/temporal/report_artifacts.py`, `moonmind/workflows/temporal/artifacts.py`, `api_service/api/routers/executions.py`, and `frontend/src/entrypoints/task-detail.tsx` for this story (FR-001 through FR-005)

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

- [ ] T007 [P] Review and tighten `tests/unit/workflows/temporal/test_report_workflow_rollout.py` coverage for FR-001, FR-002, FR-005, SC-001, SC-002, SC-005, DESIGN-REQ-021, and DESIGN-REQ-024 so generic outputs remain valid and representative report mappings stay explicit
- [ ] T008 [P] Review and tighten `tests/unit/workflows/temporal/test_artifacts.py` guardrail coverage for FR-003, FR-004, SC-003, SC-004, and DESIGN-REQ-023 so no hidden report-storage or out-of-scope capability requirement leaks into the rollout
- [ ] T009 [P] Review and tighten `tests/unit/api/routers/test_executions.py` coverage for FR-003 and SC-003 so execution-detail report behavior continues to coexist with the staged rollout path rather than implying a flag-day migration

### Integration-Style Boundary Tests (write first)

- [ ] T010 [P] Review and tighten `tests/contract/test_temporal_execution_api.py` coverage for FR-003, SC-003, and DESIGN-REQ-021 so canonical report projections remain server-defined and artifact-backed
- [ ] T011 [P] Review and tighten `frontend/src/entrypoints/task-detail.test.tsx` coverage for FR-003, FR-005, SC-002, and SC-005 so Mission Control continues to consume explicit report semantics without browser-side guessing
- [ ] T012 [P] Add or update traceability assertions in `specs/249-report-semantics-rollout/quickstart.md` and later verification notes for FR-006, SC-006, FR-007, and SC-007 so deferred decisions and MM-497 remain explicit

### Red-First Confirmation

- [ ] T013 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_report_workflow_rollout.py tests/unit/workflows/temporal/test_artifacts.py tests/unit/api/routers/test_executions.py --ui-args frontend/src/entrypoints/task-detail.test.tsx` and, when T007-T009 introduce new MM-497-focused verification coverage, confirm it fails before fallback implementation begins; otherwise record that existing verification remains green
- [ ] T014 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/contract/test_temporal_execution_api.py` and, when T010 introduces new MM-497 execution-boundary coverage, confirm it fails before fallback implementation begins; otherwise record that the existing contract boundary remains green

### Fallback Implementation Tasks (only if T013 or T014 exposes drift)

- [ ] T015 Update `moonmind/workflows/temporal/report_artifacts.py` only if T013 exposes drift in generic-output compatibility, explicit `report.*` semantics, or representative workflow mappings (FR-001, FR-002, FR-005, DESIGN-REQ-021, DESIGN-REQ-024)
- [ ] T016 Update `moonmind/workflows/temporal/artifacts.py`, `api_service/api/routers/executions.py`, and/or `frontend/src/entrypoints/task-detail.tsx` only if T013 or T014 exposes staged-rollout or canonical-report-consumption drift (FR-003, DESIGN-REQ-021, DESIGN-REQ-023)
- [ ] T017 Update `specs/249-report-semantics-rollout/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/report-rollout-semantics-contract.md`, and `quickstart.md` only if verification reveals story-level traceability or deferred-decision drift (FR-006, FR-007, SC-006, SC-007)

### Story Validation

- [ ] T018 Rerun the focused unit command from T013 until MM-497 verification passes for FR-001 through FR-005, SC-001 through SC-005, and DESIGN-REQ-021/023/024
- [ ] T019 Rerun the execution API contract command from T014 until MM-497 boundary verification passes for FR-003, SC-003, and DESIGN-REQ-021
- [ ] T020 Run `rg -n "MM-497|DESIGN-REQ-021|DESIGN-REQ-023|DESIGN-REQ-024|report\\.primary|output\\.primary|report_type|auto-pinning|projection timing|export semantics|evidence grouping|multi-step" specs/249-report-semantics-rollout docs/tmp/jira-orchestration-inputs/MM-497-moonspec-orchestration-input.md docs/Artifacts/ReportArtifacts.md` to verify traceability and deferred-decision preservation (FR-006, FR-007, SC-006, SC-007)
- [ ] T021 Escalate to `./tools/test_integration.sh` only if MM-497 fallback implementation crosses the hermetic integration boundary for artifact persistence, activity publication, or compose-backed API serialization

**Checkpoint**: MM-497 rollout behavior is verified against the current repo, and any code changes made were limited to the smallest contract-preserving fix required by failed verification.

---

## Phase 4: Polish And Verification

**Purpose**: Complete final validation and preserve story-level evidence without adding hidden scope.

- [ ] T022 [P] Update `specs/249-report-semantics-rollout/quickstart.md` only if the executed MM-497 verification commands or escalation criteria differ from the current plan
- [ ] T023 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` unless blocked by environment constraints so MM-497 closes with the canonical unit suite
- [ ] T024 Run `/moonspec-verify` for `specs/249-report-semantics-rollout/` and produce the final verification artifact covering MM-497, FR-001 through FR-007, SC-001 through SC-007, and DESIGN-REQ-021, DESIGN-REQ-023, DESIGN-REQ-024

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
