# Tasks: Remediation Mission Control Surfaces

**Input**: Design documents from `specs/224-remediation-mission-control/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/remediation-mission-control.md`, `quickstart.md`

**Tests**: Unit tests and integration-style UI tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped around the single MM-457 user story so the work stays focused, traceable, and independently testable.

**Source Traceability**: This task list maps FR-001 through FR-013, SC-001 through SC-008, and DESIGN-REQ-001 through DESIGN-REQ-008 from `spec.md` to concrete test, implementation, and verification work.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_temporal_service.py`
- Integration tests: `./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because the task touches different files and has no dependency on incomplete tasks.
- Include exact file paths in descriptions.
- Include requirement, scenario, success criterion, or source design IDs when the task implements or validates behavior.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm existing remediation and Mission Control foundations before adding UI surfaces.

- [X] T001 Confirm active MM-457 artifacts exist in `specs/224-remediation-mission-control/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/remediation-mission-control.md`, and `quickstart.md`. (FR-013, SC-008)
- [X] T002 Review existing remediation create/link/context/evidence behavior in `api_service/api/routers/executions.py`, `moonmind/workflows/temporal/service.py`, `moonmind/workflows/temporal/remediation_context.py`, and `moonmind/workflows/temporal/remediation_tools.py`. (FR-002, FR-004, FR-005, FR-006)
- [X] T003 Review current task detail action, timeline, artifact, live-log, and evidence-region rendering in `frontend/src/entrypoints/task-detail.tsx` and `frontend/src/entrypoints/task-detail.test.tsx`. (FR-001, FR-006, FR-011, FR-012)
- [X] T004 [P] Review task-create remediation route usage and generated OpenAPI paths in `frontend/src/generated/openapi.ts` and `frontend/src/entrypoints/task-create.tsx`. (FR-002, FR-003)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish compact remediation read and approval contracts required by the Mission Control panels.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T005 Add failing API unit tests for inbound and outbound remediation link read responses in `tests/unit/api/routers/test_executions.py`. (FR-004, FR-005, SC-002, SC-003, DESIGN-REQ-003, DESIGN-REQ-004)
- [X] T006 Add failing service or router unit tests for bounded remediation link summary fields in `tests/unit/workflows/temporal/test_temporal_service.py` or `tests/unit/api/routers/test_executions.py`. (FR-004, FR-005)
- [X] T007 Add failing API unit tests for remediation approval-state read and permission-aware decision behavior in `tests/unit/api/routers/test_executions.py`. (FR-008, FR-009, SC-005, DESIGN-REQ-006)
- [X] T008 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_temporal_service.py` and capture expected red-first failures for T005-T007.
- [X] T009 Implement inbound/outbound remediation link read route or task-detail payload extension in `api_service/api/routers/executions.py`, backed by existing `TemporalExecutionService` link methods. (FR-004, FR-005, DESIGN-REQ-003, DESIGN-REQ-004)
- [X] T010 Implement compact remediation approval read/decision route in `api_service/api/routers/executions.py` only if no existing control-event route can satisfy T007. (FR-008, FR-009, DESIGN-REQ-006)
- [X] T011 Update OpenAPI generation artifacts in `frontend/src/generated/openapi.ts` if backend route changes require generated frontend types. (FR-004, FR-005, FR-008, FR-009)
- [X] T012 Rerun the focused API tests from T008 and fix backend failures until the remediation read/approval contract passes.

**Checkpoint**: Backend/read-model foundation ready - story UI work can now begin.

---

## Phase 3: Story - Remediation Mission Control Surfaces

**Summary**: As a Mission Control operator, I want to create remediation tasks from target execution views and inspect remediation links, evidence, and approvals in task detail so I can understand and govern remediation work without leaving Mission Control.

**Independent Test**: Render a target execution with inbound remediation links and a remediation execution with outbound target metadata, context evidence, action artifacts, and approval events. The story passes when Mission Control shows create remediation entrypoints, bidirectional links, evidence artifact access, approval state, and safe degraded states without changing the underlying task execution contract.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, SC-007, SC-008, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008

**Test Plan**:

- Unit: API/read-model tests for link direction, compact evidence, approval state, permission handling, and canonical create route preservation.
- Integration-style UI: rendered task-detail/create tests for create actions, inbound/outbound panels, evidence grouping, approval controls, degraded states, accessibility/fallback posture, and non-remediation regressions.

### Unit and UI Tests (write first)

> NOTE: Write these tests FIRST. Run them, confirm they FAIL for the expected reason when they expose a gap, then implement only enough code to make them pass.

- [X] T013 Add failing task-detail UI test for eligible target states exposing Create remediation task and ineligible states hiding or disabling it in `frontend/src/entrypoints/task-detail.test.tsx`. (FR-001, SC-001, DESIGN-REQ-001)
- [X] T014 Add failing task-detail UI test for remediation create prefill and canonical route submission in `frontend/src/entrypoints/task-detail.test.tsx` or `frontend/src/entrypoints/task-create.test.tsx`. (FR-002, FR-003, SC-001, DESIGN-REQ-002)
- [X] T015 Add failing task-detail UI test for inbound Remediation Tasks panel on a target execution in `frontend/src/entrypoints/task-detail.test.tsx`. (FR-004, SC-002, DESIGN-REQ-003)
- [X] T016 Add failing task-detail UI test for outbound Remediation Target panel on a remediation execution in `frontend/src/entrypoints/task-detail.test.tsx`. (FR-005, SC-003, DESIGN-REQ-004)
- [X] T017 Add failing task-detail UI test for grouped remediation evidence artifact links and absence of raw storage/path/presigned URL data in `frontend/src/entrypoints/task-detail.test.tsx`. (FR-006, FR-007, SC-004, DESIGN-REQ-005)
- [X] T018 Add failing task-detail UI test for approval-gated remediation state, approve/reject controls, and read-only unauthorized state in `frontend/src/entrypoints/task-detail.test.tsx`. (FR-008, FR-009, SC-005, DESIGN-REQ-006)
- [X] T019 Add failing task-detail UI test for missing link, missing context artifact, missing evidence refs, unavailable live follow, and approval fetch failure degraded states in `frontend/src/entrypoints/task-detail.test.tsx`. (FR-010, SC-006, DESIGN-REQ-007)
- [X] T020 Add failing CSS/accessibility assertions for remediation panel focus, contrast, mobile containment, reduced-motion/fallback posture in `frontend/src/entrypoints/task-detail.test.tsx` and `frontend/src/styles/mission-control.css` inspection helpers. (FR-011, DESIGN-REQ-008)
- [X] T021 Add or confirm non-remediation task-detail/create regression coverage in `frontend/src/entrypoints/task-detail.test.tsx` and `frontend/src/entrypoints/task-create.test.tsx`. (FR-012, SC-007)
- [X] T022 Run `./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx` and capture expected red-first failures for T013-T021.

### Implementation

- [X] T023 Implement remediation create action visibility and target-state eligibility in `frontend/src/entrypoints/task-detail.tsx`. (FR-001, DESIGN-REQ-001)
- [X] T024 Implement remediation create draft/prefill and canonical route submission from task detail in `frontend/src/entrypoints/task-detail.tsx` or shared create helpers in `frontend/src/entrypoints/task-create.tsx`. (FR-002, FR-003, DESIGN-REQ-002)
- [X] T025 Implement inbound Remediation Tasks panel in `frontend/src/entrypoints/task-detail.tsx`. (FR-004, DESIGN-REQ-003)
- [X] T026 Implement outbound Remediation Target panel in `frontend/src/entrypoints/task-detail.tsx`. (FR-005, DESIGN-REQ-004)
- [X] T027 Implement remediation evidence grouping and safe artifact link rendering in `frontend/src/entrypoints/task-detail.tsx`. (FR-006, FR-007, DESIGN-REQ-005)
- [X] T028 Implement approval-gated remediation display and approve/reject controls in `frontend/src/entrypoints/task-detail.tsx`, wired to the trusted backend route from T010 when required. (FR-008, FR-009, DESIGN-REQ-006)
- [X] T029 Implement degraded and empty states for missing remediation links, context artifacts, evidence refs, live follow, and approval metadata in `frontend/src/entrypoints/task-detail.tsx`. (FR-010, DESIGN-REQ-007)
- [X] T030 Update remediation panel styling in `frontend/src/styles/mission-control.css` using existing Mission Control evidence-region patterns. (FR-011, DESIGN-REQ-008)
- [X] T031 Run `./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx` and fix UI failures.
- [X] T032 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_temporal_service.py` and fix backend regressions.

**Checkpoint**: Backend/read-model and UI story slices are covered by API and integration-style UI tests.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [X] T033 [P] Review MM-457 traceability in `specs/224-remediation-mission-control/spec.md`, `plan.md`, `tasks.md`, and `contracts/remediation-mission-control.md`. (FR-013, SC-008)
- [X] T034 [P] Review rendered remediation panels for long workflow IDs, run IDs, action labels, artifact labels, and mobile containment in `frontend/src/entrypoints/task-detail.test.tsx`. (FR-011)
- [X] T035 Run quickstart validation commands from `specs/224-remediation-mission-control/quickstart.md`.
- [X] T036 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx` for final focused frontend evidence.
- [X] T037 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_temporal_service.py` for final focused backend evidence.
- [X] T038 Run `/moonspec-verify` final verification and write the result to `specs/224-remediation-mission-control/verification.md`. (SC-008)

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (Phase 1): no dependencies.
- Foundational (Phase 2): depends on Setup and blocks UI story work.
- Story (Phase 3): depends on backend/read-model foundation where needed.
- Polish (Phase 4): depends on story tests and implementation passing.

### Within The Story

- API/read-model tests must be authored before backend route changes.
- UI tests must be authored before task-detail implementation.
- Create action and data-fetch wiring should land before panels that consume remediation data.
- Evidence grouping and approval controls depend on remediation panel data shape.
- Final `/moonspec-verify` runs only after tests pass and tasks are marked complete.

### Parallel Opportunities

- T004 can run in parallel with T002 and T003.
- T005 and T006 can be written together; T007 is separate if approval uses a different route.
- T013 through T021 are in the same frontend test file family and should be edited in one ordered batch.
- T033 and T034 can run in parallel after implementation and tests pass.

## Implementation Strategy

1. Lock the backend remediation read/approval contract with failing tests.
2. Implement the smallest trusted API/read-model surface Mission Control needs.
3. Add task-detail and create-page UI tests for every visible operator state.
4. Implement the UI panels using existing Mission Control evidence-region styling.
5. Run focused frontend and backend verification.
6. Run `/moonspec-verify` and record final evidence.

## Notes

- This task list covers one story only: MM-457.
- Do not implement automatic self-healing, new action registry kinds, raw log embedding, or direct raw storage access.
- Preserve the canonical `task.remediation` create contract and existing non-remediation task behavior.
