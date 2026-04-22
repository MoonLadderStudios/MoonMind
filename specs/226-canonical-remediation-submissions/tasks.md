# Tasks: Canonical Remediation Submissions

**Input**: Design documents from `/specs/226-canonical-remediation-submissions/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: The MM-451 behavior is classified as `implemented_verified`; tasks preserve traceability and rerun the focused router/service verification that already covers the runtime behavior. No new production implementation is planned unless verification fails.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py tests/unit/api/routers/test_executions.py`
- Integration tests: Existing FastAPI router and Temporal service boundary tests in the same focused command cover the runtime boundary for this create-time persistence slice; no compose-backed integration runner is required.
- Final verification: `/moonspec-verify`

**Source Traceability**: The original MM-451 Jira preset brief is preserved in `specs/226-canonical-remediation-submissions/spec.md`. Tasks cover FR-001 through FR-008, SC-001 through SC-006, and DESIGN-REQ-001 through DESIGN-REQ-005. All rows are classified `implemented_verified` in `plan.md` with existing code and test evidence.

## Phase 1: Setup

- [X] T001 Confirm active MM-451 feature artifacts exist in `specs/226-canonical-remediation-submissions/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/canonical-remediation-submissions.md`, and `quickstart.md`.
- [X] T002 Confirm the source preset brief is preserved in `specs/226-canonical-remediation-submissions/spec.md` for MM-451 traceability.
- [X] T003 Update Codex agent context from `specs/226-canonical-remediation-submissions/plan.md`.

## Phase 2: Foundational

- [X] T004 Confirm existing remediation persistence model in `api_service/db/models.py` covers pinned target linkage for FR-004 and FR-005.
- [X] T005 Confirm existing remediation service validation and lookup boundaries in `moonmind/workflows/temporal/service.py` cover FR-002, FR-003, FR-005, FR-006, and FR-008.
- [X] T006 Confirm existing task-shaped and convenience route boundaries in `api_service/api/routers/executions.py` cover FR-001, FR-002, and FR-007.

## Phase 3: Accept Canonical Remediation Submissions

**Summary**: Verify that canonical `task.remediation` submissions preserve the payload, pin target run identity, persist explicit non-dependency relationships, reject malformed requests, and support inbound/outbound lookup.

**Independent Test**: Submit a task creation request with `task.remediation.target.workflowId` for an existing visible run, then verify the created run preserves `initialParameters.task.remediation`, records the resolved target run, starts independently of target success, and exposes inbound and outbound remediation relationship lookups.

**Traceability IDs**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005.

**Unit Test Plan**: Rerun existing service and router tests that cover remediation payload preservation, target run pinning, link persistence, validation failures, no dependency edges, lookup directions, and convenience route expansion.

**Integration Test Plan**: Existing FastAPI router tests exercise the API boundary and existing service tests exercise the async persistence boundary; no additional compose-backed service integration is introduced by this already implemented slice.

- [X] T007 Map existing router tests in `tests/unit/api/routers/test_executions.py` to FR-001, FR-007, SC-001, and SC-006.
- [X] T008 Map existing service tests in `tests/unit/workflows/temporal/test_temporal_service.py` to FR-002, FR-003, FR-004, FR-005, FR-006, FR-008, SC-002, SC-003, SC-004, and SC-005.
- [X] T009 Confirm red-first history exists in the completed remediation create/link slice and no new missing/partial requirement requires new failing tests for MM-451.
- [X] T010 Run focused unit verification from `specs/226-canonical-remediation-submissions/quickstart.md`.
- [X] T011 Record final MoonSpec verification in `specs/226-canonical-remediation-submissions/verification.md`.

## Final Phase: Polish And Verification

- [X] T012 Run final artifact alignment check for `specs/226-canonical-remediation-submissions/spec.md`, `plan.md`, and `tasks.md`.
- [X] T013 Run `/moonspec-verify` by auditing implementation against `specs/226-canonical-remediation-submissions/spec.md` and recording the result.

## Dependencies And Execution Order

1. Setup tasks T001-T003 are complete before foundational evidence tasks.
2. Foundational tasks T004-T006 are complete before story verification.
3. Story verification T010 must pass before final verification T011-T013.

## Implementation Strategy

All requirements are already implemented and verified by existing code/tests. If focused verification fails, treat the failing requirement as `implemented_unverified`, add a targeted failing regression test first, then update the smallest affected boundary before rerunning verification.
