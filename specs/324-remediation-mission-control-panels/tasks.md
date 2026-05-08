# Tasks: Remediation Mission Control Panels

**Input**: Design documents from `specs/324-remediation-mission-control-panels/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/remediation-mission-control-contract.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around the single story "Operate Remediation From Mission Control" so the work stays focused, traceable, and independently testable.

**Source Traceability**: Original Jira issue `MM-624` and the canonical Jira preset brief are preserved in `spec.md`. This task list covers the single story, FR-001 through FR-019, acceptance scenarios 1-6, edge cases, SC-001 through SC-007, and DESIGN-REQ-010, DESIGN-REQ-024, DESIGN-REQ-025, and DESIGN-REQ-028. Plan status summary: 19 rows `partial`, 3 rows `missing`, 6 rows `implemented_unverified`, and 2 rows `implemented_verified`.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` for focused UI work; `./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py tests/unit/workflows/temporal/test_remediation_context.py` for backend contract/service work; final full unit suite with `./tools/test_unit.sh`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel when tasks touch different files and do not depend on incomplete tasks
- Include exact file paths in descriptions
- Include requirement, scenario, success criterion, or source IDs when the task implements or validates behavior

## Phase 1: Setup

**Purpose**: Confirm the active feature artifacts and test harnesses are ready before story work.

- [ ] T001 Confirm `specs/324-remediation-mission-control-panels/spec.md`, `specs/324-remediation-mission-control-panels/plan.md`, `specs/324-remediation-mission-control-panels/research.md`, `specs/324-remediation-mission-control-panels/data-model.md`, `specs/324-remediation-mission-control-panels/contracts/remediation-mission-control-contract.md`, and `specs/324-remediation-mission-control-panels/quickstart.md` are present and preserve `MM-624`, FR-001 through FR-019, SC-001 through SC-007, and DESIGN-REQ-010/DESIGN-REQ-024/DESIGN-REQ-025/DESIGN-REQ-028
- [ ] T002 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` once to prepare frontend dependencies and capture the current focused baseline for `frontend/src/entrypoints/task-detail.test.tsx`
- [ ] T003 Run `./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py tests/unit/workflows/temporal/test_remediation_context.py` once to capture the current backend remediation baseline for `tests/unit/workflows/temporal/test_temporal_service.py` and `tests/unit/workflows/temporal/test_remediation_context.py`

---

## Phase 2: Foundational

**Purpose**: Establish shared contract fixtures and typed model surfaces that block story test authoring.

**CRITICAL**: No production implementation work can begin until this phase is complete.

- [ ] T004 Add shared remediation Mission Control fixture builders for rich inbound/outbound links, selected steps, allowed actions, live observation, approval handoff, degraded evidence, and lock states in `frontend/src/entrypoints/task-detail.test.tsx` for FR-004 through FR-018 and SC-002 through SC-006
- [ ] T005 [P] Add backend unit-test fixtures for extended remediation link summaries and approval metadata in `tests/unit/workflows/temporal/test_temporal_service.py` for FR-003, FR-005, FR-010, FR-011, FR-012, and DESIGN-REQ-010/DESIGN-REQ-028
- [ ] T006 [P] Add evidence/live-follow fixture coverage helpers in `tests/unit/workflows/temporal/test_remediation_context.py` for FR-006, FR-008, FR-009, FR-013, FR-014, and DESIGN-REQ-024
- [ ] T007 [P] Add an integration test fixture or test module for the remediation Mission Control API boundary in `tests/integration/temporal/test_remediation_mission_control_panels.py` covering create/list/approval routes without external credentials for FR-003, FR-005, FR-010, FR-011, SC-003, and SC-005
- [ ] T008 Verify T004-T007 compile or fail only because expected behavior is not implemented, not because of fixture syntax or import errors, by running `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` and targeted backend unit collection

**Checkpoint**: Foundational fixtures and contract test scaffolding are ready; story tests can now be written.

---

## Phase 3: Story - Operate Remediation From Mission Control

**Summary**: As an operator, I want Mission Control to expose remediation creation, relationship, observation, evidence, lock, and approval panels so that remediation work is understandable and controllable from both target and remediation task detail pages.

**Independent Test**: Walk an operator through remediation creation from supported Mission Control surfaces, then inspect target and remediation task detail views across normal, degraded-evidence, live-follow, lock-conflict, and approval-gated scenarios.

**Traceability**: FR-001 through FR-019; acceptance scenarios 1-6; SC-001 through SC-007; DESIGN-REQ-010, DESIGN-REQ-024, DESIGN-REQ-025, DESIGN-REQ-028.

**Unit Test Plan**:

- Frontend unit tests in `frontend/src/entrypoints/task-detail.test.tsx` cover UI creation choices, relationship panels, evidence summaries, live observation, approval handoff, lock/precondition/degraded states, and accessibility containment.
- Backend unit tests in `tests/unit/workflows/temporal/test_temporal_service.py` cover route/service serialization, canonical `task.remediation` payload validation, pinned-run behavior, approval decision metadata, and structured validation errors.
- Backend unit tests in `tests/unit/workflows/temporal/test_remediation_context.py` cover evidence degradation, live-follow metadata, unavailable evidence classes, and fallback summaries.

**Integration Test Plan**:

- Hermetic integration coverage in `tests/integration/temporal/test_remediation_mission_control_panels.py` covers create/list/approval route behavior, response shape, bounded metadata, and no raw evidence leakage.
- Final required integration command is `./tools/test_integration.sh`.

### Unit Tests (write first)

> Write these tests first. Run them and confirm they fail for the expected missing behavior before implementing production code.

- [ ] T009 [P] Add failing frontend unit tests for remediation creation from eligible task detail, failed banner, attention-required/stuck state, and provider/session problem state in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-001, SC-001, acceptance scenario 1, and DESIGN-REQ-010
- [ ] T010 [P] Add failing frontend unit tests for selected step/all-step controls, pinned run, mode, authority, action policy, evidence preview, and canonical request body in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-002, FR-003, SC-001, and acceptance scenario 1
- [ ] T011 [P] Add failing frontend unit tests for inbound target-side remediation panel lock badge/holder, relationship degraded state, status, authority, latest action, and resolution in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-004, SC-002, acceptance scenario 2, DESIGN-REQ-010, and DESIGN-REQ-025
- [ ] T012 [P] Add failing frontend unit tests for outbound remediation target panel selected steps, current target state, evidence bundle, allowed actions, approval state, lock state, and target link in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-005, SC-003, acceptance scenario 3, and DESIGN-REQ-010
- [ ] T013 [P] Add failing frontend unit tests for remediation evidence classes, target logs/diagnostics, decision log, action request/result, verification artifacts, unavailable evidence classes, and durable fallback presentation in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-006, FR-009, FR-013, FR-014, SC-006, acceptance scenario 6, and DESIGN-REQ-024
- [ ] T014 [P] Add failing frontend unit tests for active live observation label, sequence cursor, reconnect state, epoch boundary, unsupported/policy-denied fallback, and non-authoritative wording in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-007, FR-008, FR-009, SC-004, acceptance scenario 4, and DESIGN-REQ-010/DESIGN-REQ-024
- [ ] T015 [P] Add failing frontend unit tests for approval handoff action, preconditions, blast radius, risk, audit ref, approve/reject controls, read-only state, and persisted approved/rejected decisions in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-010, SC-005, acceptance scenario 5, and DESIGN-REQ-028
- [ ] T016 [P] Add failing frontend unit tests for missing/invisible target errors, rerun pinned snapshot display, lock conflict, forced-termination approval policy, precondition/no-op/verification-failed outcomes, failed remediator final summary, and lock-release state in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-011, FR-012, FR-015, FR-016, FR-017, FR-018, SC-006, edge cases, DESIGN-REQ-024, DESIGN-REQ-025, and DESIGN-REQ-028
- [ ] T017 [P] Add failing backend unit tests for extended `RemediationLinkSummaryModel` fields, approval metadata serialization, bounded validation errors, pinned run preservation, and no raw storage leakage in `tests/unit/workflows/temporal/test_temporal_service.py` covering FR-003, FR-005, FR-010, FR-011, FR-012, FR-015, SC-003, and SC-005
- [ ] T018 [P] Add failing backend unit tests for live observation metadata, unavailable evidence classes, historical merged-log degradation, non-applicable evidence reasons, and final remediation summary/lock-release metadata in `tests/unit/workflows/temporal/test_remediation_context.py` covering FR-006, FR-008, FR-009, FR-013, FR-014, FR-018, SC-004, and SC-006

### Integration Tests (write first)

- [ ] T019 [P] Add failing hermetic integration test for `POST /api/executions/{workflow_id}/remediation` canonical payload normalization, selected steps, pinned run, structured invalid-target errors, and bounded metadata in `tests/integration/temporal/test_remediation_mission_control_panels.py` covering FR-001, FR-002, FR-003, FR-011, FR-012, SC-001, and DESIGN-REQ-010/DESIGN-REQ-024
- [ ] T020 [P] Add failing hermetic integration test for `GET /api/executions/{workflow_id}/remediations?direction=inbound|outbound` rich link summary fields, no raw storage leakage, degraded evidence, live observation metadata, lock state, and approval state in `tests/integration/temporal/test_remediation_mission_control_panels.py` covering FR-004, FR-005, FR-006, FR-008, FR-014, FR-015, SC-002, SC-003, SC-004, and DESIGN-REQ-010/DESIGN-REQ-024/DESIGN-REQ-025
- [ ] T021 [P] Add failing hermetic integration test for `POST /api/executions/{workflow_id}/remediation/approvals/{request_id}` decision persistence and subsequent operator-visible approval state in `tests/integration/temporal/test_remediation_mission_control_panels.py` covering FR-010, FR-016, FR-017, SC-005, and DESIGN-REQ-028

### Red-First Confirmation

- [ ] T022 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` and confirm T009-T016 fail for expected missing UI behavior, not syntax or fixture errors
- [ ] T023 Run `./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py tests/unit/workflows/temporal/test_remediation_context.py` and confirm T017-T018 fail for expected missing backend contract/evidence behavior
- [ ] T024 Run the narrow command for `tests/integration/temporal/test_remediation_mission_control_panels.py` through the repo integration harness if supported, or run `./tools/test_integration.sh`, and confirm T019-T021 fail for expected missing route/contract behavior

### Conditional Fallback Implementation For Implemented-Unverified Rows

- [ ] T025 If T017 or existing tests show canonical remediation payload normalization is incomplete, update `api_service/api/routers/executions.py` and `moonmind/workflows/temporal/service.py` to preserve `task.remediation` shape, selected steps, pinned run, and structured validation errors for FR-003, FR-011, and FR-012
- [ ] T026 If T011 shows target-side remediation panel verification gaps, update `frontend/src/entrypoints/task-detail.tsx` and `frontend/src/styles/mission-control.css` to render lock holder/badge semantics and relationship degraded state for FR-004 and SC-002
- [ ] T027 If T013 or T018 shows historical merged-log degradation is not operator-visible, update `api_service/api/routers/executions.py`, `moonmind/workflows/temporal/remediation_context.py`, and `frontend/src/entrypoints/task-detail.tsx` to propagate historical degraded evidence for FR-013

### Implementation

- [ ] T028 Extend remediation API response models in `api_service/api/routers/executions.py` with selected steps, current target state, allowed actions, evidence degradation, unavailable evidence classes, live observation state, richer approval handoff metadata, and lock outcome fields for FR-005, FR-008, FR-010, FR-014, FR-015, SC-003, SC-004, SC-005, and contracts/remediation-mission-control-contract.md
- [ ] T029 Update remediation service serialization sources in `moonmind/workflows/temporal/service.py` to populate the extended link summary from canonical execution records, remediation links, audit state, and bounded metadata without raw storage paths for FR-005, FR-010, FR-014, FR-015, FR-017, FR-018, DESIGN-REQ-025, and DESIGN-REQ-028
- [ ] T030 Update evidence and live-follow summary helpers in `moonmind/workflows/temporal/remediation_context.py` to expose bounded live observation, unavailable evidence, non-applicable artifact, final summary, and lock-release data needed by Mission Control for FR-006, FR-008, FR-009, FR-013, FR-014, FR-018, and DESIGN-REQ-024
- [ ] T031 Update remediation action or guard metadata in `moonmind/workflows/temporal/remediation_actions.py` when needed so allowed actions, forced-termination policy decisions, lock conflicts, and permitted outcomes can be displayed safely for FR-015, FR-016, FR-017, and DESIGN-REQ-025
- [ ] T032 Regenerate or update generated frontend API types in `frontend/src/generated/openapi.ts` after backend response/request contract changes for FR-005, FR-008, FR-010, FR-014, and contracts/remediation-mission-control-contract.md
- [ ] T033 Update Zod schemas and typed helpers in `frontend/src/entrypoints/task-detail.tsx` for extended remediation link, live observation, evidence degradation, approval handoff, selected steps, allowed actions, current target, and lock outcome fields for FR-005 through FR-018
- [ ] T034 Implement remediation creation controls in `frontend/src/entrypoints/task-detail.tsx` for specified eligible surfaces, selected steps/all steps, richer evidence preview, and canonical request body for FR-001, FR-002, FR-003, SC-001, and acceptance scenario 1
- [ ] T035 Implement target-side and remediation-side relationship panel rendering in `frontend/src/entrypoints/task-detail.tsx` for status, authority, latest action, resolution, active lock scope/holder, target link, pinned run, selected steps, current target state, evidence bundle, allowed actions, approval state, and degraded relationship states for FR-004, FR-005, SC-002, SC-003, acceptance scenarios 2-3, and DESIGN-REQ-010
- [ ] T036 Implement evidence and live observation panels in `frontend/src/entrypoints/task-detail.tsx` for remediation context, target logs/diagnostics, decision log, action request/result, verification, unavailable evidence classes, active live observation, cursor, reconnect, epoch, and durable fallback states for FR-006, FR-007, FR-008, FR-009, FR-013, FR-014, SC-004, SC-006, acceptance scenarios 4 and 6, and DESIGN-REQ-024
- [ ] T037 Implement approval handoff and failure/lock outcome rendering in `frontend/src/entrypoints/task-detail.tsx` for proposed action, preconditions, blast radius, approve/reject controls, persisted decision, forced-termination policy, lock conflict, precondition/no-op/verification-failed states, failed remediator final summary, and lock-release state for FR-010, FR-015, FR-016, FR-017, FR-018, SC-005, SC-006, acceptance scenario 5, and DESIGN-REQ-025/DESIGN-REQ-028
- [ ] T038 Update Mission Control remediation CSS in `frontend/src/styles/mission-control.css` for responsive containment, focus visibility, dense panel layout, long id wrapping, live-observation status, approval handoff controls, and degraded state affordances for FR-004 through FR-018 and SC-002 through SC-006
- [ ] T039 Ensure remediation panels never render raw storage paths, presigned URLs, secrets, or unbounded log bodies by adding guard formatting in `frontend/src/entrypoints/task-detail.tsx` and backend serializers in `api_service/api/routers/executions.py` for FR-006, FR-009, FR-014, SC-006, and Constitution IV/IX

### Story Validation

- [ ] T040 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` and verify frontend remediation Mission Control tests pass for FR-001 through FR-018
- [ ] T041 Run `./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py tests/unit/workflows/temporal/test_remediation_context.py` and verify backend remediation contract and evidence tests pass for FR-003, FR-005, FR-008, FR-010 through FR-018
- [ ] T042 Run `./tools/test_integration.sh` and verify hermetic integration coverage passes for remediation create/list/approval route contracts and no-credential behavior
- [ ] T043 Validate the independent story manually or through test fixtures using `specs/324-remediation-mission-control-panels/quickstart.md`, confirming an operator can create remediation, inspect bidirectional links, review evidence/live observation, see allowed actions/locks, approve/reject handoffs, and understand degraded states

**Checkpoint**: The single story is functionally complete, covered by unit and integration tests, and independently testable.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T044 [P] Review `frontend/src/entrypoints/task-detail.tsx` for duplicated remediation formatting logic and extract local helpers only where it reduces meaningful complexity without broad refactoring
- [ ] T045 [P] Review `api_service/api/routers/executions.py`, `moonmind/workflows/temporal/service.py`, and `moonmind/workflows/temporal/remediation_context.py` for bounded metadata, compatibility-sensitive payload handling, and secret-safe output for Constitution IV and IX
- [ ] T046 [P] Confirm `specs/324-remediation-mission-control-panels/spec.md`, `plan.md`, `tasks.md`, `research.md`, `data-model.md`, `contracts/remediation-mission-control-contract.md`, and `quickstart.md` preserve `MM-624`, the original Jira preset brief, and DESIGN-REQ-010/DESIGN-REQ-024/DESIGN-REQ-025/DESIGN-REQ-028 for FR-019 and SC-007
- [ ] T047 Run the full required unit suite with `./tools/test_unit.sh`
- [ ] T048 Run the required hermetic integration suite with `./tools/test_integration.sh`
- [ ] T049 Run `/moonspec-verify` for `specs/324-remediation-mission-control-panels/spec.md` and preserve the final verification report with coverage for MM-624, FR-001 through FR-019, SC-001 through SC-007, and DESIGN-REQ-010/DESIGN-REQ-024/DESIGN-REQ-025/DESIGN-REQ-028

---

## Dependencies And Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; start immediately.
- **Foundational (Phase 2)**: Depends on Setup completion; blocks story test and implementation work.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish And Verification (Phase 4)**: Depends on story implementation and tests passing.

### Within The Story

- T009-T021 must be written before implementation tasks.
- T022-T024 must confirm red-first behavior before production implementation tasks T025-T039.
- T025-T027 are conditional fallback tasks for implemented-unverified rows; skip only when their verification tests pass and record the evidence in final validation.
- Backend contract/model tasks T028-T032 should precede frontend rendering tasks T033-T039 when response shapes change.
- T040-T043 validate the story after implementation.
- T047-T049 are final full-suite and MoonSpec verification tasks.

### Parallel Opportunities

- T004-T007 can run in parallel after setup because they establish separate frontend/backend/integration fixtures.
- T009-T018 can run in parallel where they touch separate test sections or files.
- T019-T021 can run in parallel in the same integration test module if test fixtures are independent; coordinate edits to avoid conflicts.
- T028-T031 can run in parallel only if owners keep backend files disjoint; otherwise sequence them before T032.
- T034-T038 are mostly same-file frontend/CSS work and should be sequenced to avoid conflicts.
- T044-T046 can run in parallel after story validation.

## Parallel Example: Story Test Authoring

```bash
# Parallelizable test-authoring work after Phase 2:
Task: "T012 Add failing frontend unit tests for outbound remediation target panel in frontend/src/entrypoints/task-detail.test.tsx"
Task: "T017 Add failing backend unit tests for extended RemediationLinkSummaryModel in tests/unit/workflows/temporal/test_temporal_service.py"
Task: "T018 Add failing backend unit tests for live observation metadata in tests/unit/workflows/temporal/test_remediation_context.py"
```

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete setup and foundational fixture tasks.
2. Write focused frontend, backend unit, and hermetic integration tests for missing, partial, and implemented-unverified rows.
3. Confirm the tests fail for the expected missing behavior.
4. Implement backend contract changes first when response/request shapes change.
5. Regenerate or update frontend API types and schemas.
6. Implement Mission Control UI rendering and submission behavior.
7. Re-run focused unit tests, backend unit tests, and hermetic integration tests.
8. Run quickstart validation, full unit suite, full integration suite, and final `/moonspec-verify`.

### Status Handling

- `missing`: FR-008, FR-016, and SC-004 require red-first tests and implementation.
- `partial`: FR-001, FR-002, FR-005 through FR-010, FR-014 through FR-018, SC-001, SC-003, SC-005, SC-006, and source design rows require tests plus implementation to complete the operator-facing behavior.
- `implemented_unverified`: FR-003, FR-004, FR-011, FR-012, FR-013, and SC-002 require verification tests first, with conditional fallback implementation if those tests fail.
- `implemented_verified`: FR-019 and SC-007 require traceability preservation and final verification, not new production behavior.

## Notes

- This task list covers exactly one story: `Operate Remediation From Mission Control`.
- Keep production changes scoped to `api_service/api/routers/executions.py`, `moonmind/workflows/temporal/service.py`, `moonmind/workflows/temporal/remediation_context.py`, `moonmind/workflows/temporal/remediation_actions.py`, `frontend/src/entrypoints/task-detail.tsx`, `frontend/src/generated/openapi.ts`, and `frontend/src/styles/mission-control.css` unless tests expose a narrower or necessary adjacent path.
- Do not create new persistent storage unless implementation proves existing execution records, remediation links, artifact metadata, and audit/control-event surfaces cannot represent the contract.
- Do not expose raw storage paths, presigned URLs, secrets, or unbounded logs in Mission Control or API responses.
- Preserve `MM-624` and the original Jira preset brief through downstream implementation, verification, commit text, and PR metadata.
