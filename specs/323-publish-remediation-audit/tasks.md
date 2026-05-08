# Tasks: Publish Remediation Audit Evidence

**Input**: Design documents from `/work/agent_jobs/mm:ee5f7fbd-9025-4fd3-b856-ce19a43c453d/repo/specs/323-publish-remediation-audit/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/remediation-audit-evidence.md, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks cover exactly one independently testable story: Review Remediation Evidence.

**Source Traceability**: MM-623 and the original Jira preset brief are preserved in `spec.md`. This task list covers FR-001 through FR-009, acceptance scenarios 1 through 6, edge cases, SC-001 through SC-006, and DESIGN-REQ-022, DESIGN-REQ-023, and DESIGN-REQ-028.

**Requirement Status Summary**: 12 partial rows require code-and-test completion, 2 missing rows require new tests and implementation, 7 implemented-unverified rows require verification-first tests with conditional fallback implementation, and 3 implemented-verified rows require traceability-preserving final validation only.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because the task touches different files and does not depend on incomplete work.
- Every implementation or validation task names exact file paths and requirement, scenario, success, or source IDs.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm feature artifacts and existing remediation test surfaces before story work begins.

- [X] T001 Review `specs/323-publish-remediation-audit/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/remediation-audit-evidence.md`, and `quickstart.md` for MM-623 traceability and one-story scope. (FR-009, SC-006)
- [X] T002 Inspect existing remediation helper and publisher surfaces in `moonmind/workflows/temporal/remediation_context.py`, `moonmind/workflows/temporal/remediation_tools.py`, and `moonmind/workflows/temporal/remediation_actions.py` before editing. (FR-001 through FR-008)
- [X] T003 Inspect existing remediation unit and integration coverage in `tests/unit/workflows/temporal/test_remediation_context.py` and `tests/integration/temporal/test_remediation_action_contracts.py` to avoid duplicate fixtures. (FR-001 through FR-008)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Prepare shared test fixtures and contract baselines that all story tests depend on.

**CRITICAL**: No production implementation work can begin until this phase is complete.

- [X] T004 [P] Add or update shared remediation evidence fixture helpers in `tests/unit/workflows/temporal/test_remediation_context.py` for diagnosis-only, action-attempted, skipped, denied, escalated, degraded, and no-PR cases. (FR-001, FR-003, FR-007, DESIGN-REQ-022, DESIGN-REQ-023)
- [X] T005 [P] Add or update integration fixture helpers in `tests/integration/temporal/test_remediation_action_contracts.py` for querying remediation artifacts, audit records, target-side annotations, and target-native artifacts by workflow/run identity. (FR-005, FR-006, SCN-003, SCN-004)
- [X] T006 Confirm the remediation audit evidence contract in `specs/323-publish-remediation-audit/contracts/remediation-audit-evidence.md` matches the current test fixture shapes before adding red tests. (FR-005, FR-006, DESIGN-REQ-023)

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Review Remediation Evidence

**Summary**: As an operator, I want each remediation run to publish durable evidence, summaries, target annotations, and compact audit records so that every diagnosis and intervention remains reviewable after the run completes.

**Independent Test**: Complete representative remediation runs across diagnosis-only, repair-attempted, prevention-attempted, degraded-evidence, and escalated paths, then validate that operator-visible evidence, summary fields, queryable audit records, and target annotations describe the same bounded run history.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009; SCN-001 through SCN-006; SC-001 through SC-006; DESIGN-REQ-022, DESIGN-REQ-023, DESIGN-REQ-028.

**Test Plan**:

- Unit: serialization, validation, bounded state normalization, redaction, decision-log outcome matrix, summary field matrix, audit event payloads, target annotation payloads.
- Integration: artifact persistence and metadata, queryable audit persistence, target-side annotation behavior, representative remediation paths, artifact preview/redaction safety.

### Unit Tests (write first) ⚠️

> Write these tests FIRST. Run them and confirm missing/partial behavior fails for the expected reason before production implementation.

- [X] T007 Add failing unit tests for applicable artifact set and non-applicable artifact reasons in `tests/unit/workflows/temporal/test_remediation_context.py`. (FR-001, FR-002, FR-007, SC-001, DESIGN-REQ-022)
- [X] T008 Add failing unit tests for decision log attempted, skipped, denied, escalated, prevention, verification-ref, and no-PR reason entries in `tests/unit/workflows/temporal/test_remediation_context.py`. (FR-003, FR-007, SC-004, DESIGN-REQ-023)
- [X] T009 Add verification-first unit tests for the full remediation summary field set across repaired, no-action, degraded, unsafe, and escalated outcomes in `tests/unit/workflows/temporal/test_remediation_context.py`. (FR-004, FR-007, SC-003, DESIGN-REQ-028)
- [X] T010 Add failing unit tests for queryable remediation audit event persistence payload validation, redaction, timestamp normalization, and idempotency key behavior in `tests/unit/workflows/temporal/test_remediation_context.py`. (FR-005, SC-002, DESIGN-REQ-023)
- [X] T011 Add failing unit tests for target-side remediation annotation payload validation, safe artifact refs, supplemental semantics, and retry-safe identity in `tests/unit/workflows/temporal/test_remediation_context.py`. (FR-006, SCN-004, DESIGN-REQ-023)
- [X] T012 Add verification-first unit tests proving remediation artifact metadata and default presentation payloads reject raw URLs, local paths, storage keys, tokens, and secret-like values in `tests/unit/workflows/temporal/test_remediation_context.py`. (FR-008, SC-005, DESIGN-REQ-022)
- [X] T013 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py` and confirm T007, T008, T010, and T011 fail for missing/partial behavior while T009 and T012 establish verification evidence or expose gaps. (FR-001, FR-003, FR-004, FR-005, FR-006, FR-008)

### Integration Tests (write first) ⚠️

- [ ] T014 Add failing integration test for a diagnosis-only remediation run publishing applicable artifacts, bounded non-applicable action evidence, decision log, and summary in `tests/integration/temporal/test_remediation_action_contracts.py`. (SCN-001, FR-001, FR-003, FR-007, SC-001)
- [X] T015 Add failing integration test for side-effecting action execution publishing action request, action result, verification, decision log, summary, queryable audit event, and target-side annotation evidence in `tests/integration/temporal/test_remediation_action_contracts.py`. (SCN-003, SCN-004, FR-005, FR-006, DESIGN-REQ-023)
- [ ] T016 Add failing integration test for skipped, denied, escalated, prevention, and no-PR decision evidence remaining bounded and linked from the final summary in `tests/integration/temporal/test_remediation_action_contracts.py`. (SCN-002, FR-003, FR-007, SC-004)
- [ ] T017 Add verification-first integration test for degraded and escalated remediation summaries exposing stable fields and bounded degraded reasons in `tests/integration/temporal/test_remediation_action_contracts.py`. (SCN-005, FR-004, FR-007, DESIGN-REQ-028)
- [ ] T018 Add verification-first integration test for remediation artifact metadata and preview/default-read safety in `tests/integration/temporal/test_remediation_action_contracts.py`. (SCN-006, FR-008, SC-005, DESIGN-REQ-022)
- [ ] T019 Run `./tools/test_integration.sh` and confirm T014, T015, and T016 fail for missing/partial behavior while T017 and T018 establish verification evidence or expose gaps. (SCN-001 through SCN-006)

### Red-First Confirmation

- [X] T020 Confirm and summarize the expected red-first failures from T013 and T019 for `tests/unit/workflows/temporal/test_remediation_context.py` and `tests/integration/temporal/test_remediation_action_contracts.py` before modifying production code. (FR-001, FR-003, FR-005, FR-006, FR-007)
- [ ] T021 Confirm no implementation tasks are started before missing/partial unit and integration tests fail for the intended reason in `tests/unit/workflows/temporal/test_remediation_context.py` and `tests/integration/temporal/test_remediation_action_contracts.py`. (FR-001, FR-003, FR-005, FR-006, DESIGN-REQ-022, DESIGN-REQ-023)

### Conditional Fallback Implementation for Implemented-Unverified Rows

- [ ] T022 If T009 exposes missing summary fields or invalid lifecycle state handling, update `moonmind/workflows/temporal/remediation_context.py` to complete the remediation summary field set and bounded validation. (FR-004, SC-003, DESIGN-REQ-028)
- [ ] T023 If T012 or T018 exposes artifact presentation safety gaps, update remediation artifact metadata generation in `moonmind/workflows/temporal/remediation_context.py` and `moonmind/workflows/temporal/remediation_tools.py` without duplicating generic artifact presentation logic. (FR-008, SC-005, DESIGN-REQ-022)
- [ ] T024 If T017 exposes degraded or escalated summary gaps, update lifecycle summary publication in `moonmind/workflows/temporal/remediation_tools.py` and bounded state helpers in `moonmind/workflows/temporal/remediation_context.py`. (FR-004, FR-007, SCN-005, DESIGN-REQ-028)

### Implementation

- [X] T025 Implement path-aware remediation evidence-set helpers and non-applicable artifact reason serialization in `moonmind/workflows/temporal/remediation_context.py`. (FR-001, FR-007, SC-001, DESIGN-REQ-022)
- [ ] T026 Update lifecycle publication in `moonmind/workflows/temporal/remediation_tools.py` so diagnosis-only, action-attempted, degraded, and escalated paths publish all applicable remediation evidence artifacts. (FR-001, FR-003, FR-007, SCN-001, SCN-002)
- [X] T027 Complete bounded decision-log outcome support in `moonmind/workflows/temporal/remediation_context.py` for attempted, skipped, denied, escalated, prevention, verification, and no-PR reasons. (FR-003, FR-007, SC-004, DESIGN-REQ-023)
- [X] T028 Implement compact queryable remediation audit event persistence and lookup support in `moonmind/workflows/temporal/remediation_context.py`, reusing existing control-event or artifact-backed persistence patterns where feasible. (FR-005, SC-002, DESIGN-REQ-023)
- [X] T029 Wire side-effecting action audit event publication from `RemediationEvidenceToolService.execute_action()` in `moonmind/workflows/temporal/remediation_tools.py`. (FR-005, SCN-003, DESIGN-REQ-023)
- [X] T030 Implement target-side remediation annotation payload construction in `moonmind/workflows/temporal/remediation_context.py`. (FR-006, SCN-004, DESIGN-REQ-023)
- [X] T031 Wire target-side remediation annotation publication from side-effecting action execution in `moonmind/workflows/temporal/remediation_tools.py` without replacing target-native artifacts. (FR-006, SCN-004, DESIGN-REQ-023)
- [X] T032 Add any required API or service query support for compact remediation audit records in `api_service/api/routers/task_runs.py` or an existing remediation-related service file, only if artifact/control-event querying is not already sufficient. (FR-005, SC-002)
- [X] T033 Preserve existing remediation artifact classification behavior in `moonmind/workflows/temporal/remediation_context.py` while adding new audit or annotation evidence. (FR-002, FR-009, DESIGN-REQ-022)

### Story Validation

- [X] T034 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py` and fix failures in `moonmind/workflows/temporal/remediation_context.py` and `moonmind/workflows/temporal/remediation_tools.py`. (FR-001 through FR-008)
- [ ] T035 Run `./tools/test_integration.sh` and fix failures in remediation evidence, audit, and annotation paths. (SCN-001 through SCN-006)
- [X] T036 Run `rg -n "MM-623|DESIGN-REQ-022|DESIGN-REQ-023|DESIGN-REQ-028" specs/323-publish-remediation-audit` and confirm traceability remains present in `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/remediation-audit-evidence.md`, `quickstart.md`, and `tasks.md`. (FR-009, SC-006)

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and independently testable.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen the completed story without expanding beyond MM-623.

- [X] T037 [P] Review `specs/323-publish-remediation-audit/contracts/remediation-audit-evidence.md` against implemented behavior and update it only if implementation reveals a contract clarification need. (FR-005, FR-006, DESIGN-REQ-023)
- [X] T038 [P] Review `specs/323-publish-remediation-audit/data-model.md` against implemented persistence and artifact behavior and update it only if implementation reveals a model clarification need. (FR-001 through FR-008)
- [X] T039 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for the full unit suite and fix any regressions. (FR-001 through FR-009)
- [ ] T040 Run `./tools/test_integration.sh` for the required hermetic integration suite and fix any regressions. (SCN-001 through SCN-006)
- [ ] T041 Run the quickstart validation steps in `specs/323-publish-remediation-audit/quickstart.md` and record any deviations in final verification evidence. (SC-001 through SC-006)
- [ ] T042 Run `/speckit.verify` to validate the final implementation against MM-623, the original Jira preset brief, `spec.md`, `plan.md`, `tasks.md`, and required tests. (FR-009, SC-006)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; start immediately.
- **Foundational (Phase 2)**: Depends on Phase 1; blocks story test and implementation work.
- **Story (Phase 3)**: Depends on Phase 2; tests must be written and red-first confirmed before production implementation.
- **Polish (Phase 4)**: Depends on the story being functionally complete with focused unit and integration tests passing.

### Within The Story

- T007 through T012 must be authored before T013.
- T014 through T018 must be authored before T019.
- T020 and T021 must complete before T022 through T033.
- T022 through T024 are conditional fallback tasks for implemented-unverified rows and should be skipped only when verification tests pass without production changes.
- T025 through T033 are production implementation tasks for missing and partial rows.
- T034 through T036 validate the complete story before polish work.
- T042 runs only after implementation and required tests pass.

### Parallel Opportunities

- T002 and T003 can run in parallel after T001.
- T004 and T005 can run in parallel after T003 because they update different test files.
- T007 through T012 are ordered unit-test authoring tasks because they all edit `tests/unit/workflows/temporal/test_remediation_context.py`.
- T014 through T018 are ordered integration-test authoring tasks because they all edit `tests/integration/temporal/test_remediation_action_contracts.py`.
- T028 and T030 can run in parallel after red-first confirmation because they build different domain helpers in the same module only with careful coordination; otherwise run sequentially.
- T037 and T038 can run in parallel after story validation.

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete setup and foundational fixture tasks.
2. Add unit tests and integration tests for missing and partial rows first.
3. Run focused unit and integration tests to confirm red-first failures.
4. Run verification-first tests for implemented-unverified summary and presentation rows; skip fallback implementation only if those tests pass.
5. Implement evidence-set completion, decision logs, queryable audit persistence, and target-side annotations.
6. Re-run focused tests, then full unit and hermetic integration suites.
7. Preserve `MM-623` and source IDs across all final artifacts.
8. Run `/speckit.verify` after implementation and tests pass.

### Coverage Mapping

- Code-and-test completion: FR-001, FR-003, FR-005, FR-006, FR-007, SCN-001, SCN-002, SCN-003, SCN-004, SC-001, SC-002, SC-004, DESIGN-REQ-022, DESIGN-REQ-023.
- Verification-first with conditional fallback: FR-004, FR-008, SCN-005, SCN-006, SC-003, SC-005, DESIGN-REQ-028.
- Already verified, preserve in final validation: FR-002, FR-009, SC-006.

## Notes

- The task list covers one story only: Review Remediation Evidence.
- Do not generate broad remediation backlog tasks outside MM-623.
- Do not create compatibility aliases for internal remediation contracts; update live callers and tests together if contracts change.
- Keep large artifact bodies out of workflow history; pass refs and compact metadata through workflow/activity boundaries.
- Secret-like values, raw local paths, raw storage keys, presigned URLs, cookies, and auth headers must not appear in artifacts, metadata, logs, audit records, PR text, or final verification evidence.
- Implementation evidence: unit red-first failed with missing `build_remediation_evidence_set`; integration red-first in a temporary HEAD worktree failed with missing `auditEvent`; focused unit, targeted integration, and full unit reruns passed after implementation.
- Environment limitation: `./tools/test_integration.sh` could not complete in this managed container because Docker build access returned 403 Forbidden.
