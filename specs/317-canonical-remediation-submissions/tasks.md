# Tasks: Canonical Remediation Submissions

**Input**: Design documents from `/work/agent_jobs/mm:51ba770c-9aeb-45d6-855a-72e6749d2c73/repo/specs/317-canonical-remediation-submissions/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/remediation-submissions.md`, `quickstart.md`

**Tests**: Unit tests and integration-boundary tests are required before any production fallback work. The plan classifies the core behavior as `implemented_verified`, so implementation tasks are conditional and only run if focused verification fails.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py tests/unit/api/routers/test_executions.py`
- Integration tests: FastAPI router and async service-boundary tests in the focused unit command cover this story's integration boundary; run `./tools/test_integration.sh` only if fallback changes touch compose-backed artifact/API lifecycle behavior.
- Final verification: `/moonspec-verify`

**Source Traceability**: Original Jira issue `MM-617` is preserved in `spec.md`. Tasks cover FR-001 through FR-010, SC-001 through SC-006, DESIGN-REQ-001 through DESIGN-REQ-007, and the acceptance scenarios in `spec.md`. Plan status summary: 21 rows implemented_verified, 2 rows partial for downstream MM-617 traceability (`FR-010`, `SC-006`), 0 missing, 0 implemented_unverified.

## Phase 1: Setup

- [X] T001 Confirm active feature artifacts exist for MM-617 in `specs/317-canonical-remediation-submissions/spec.md`, `specs/317-canonical-remediation-submissions/plan.md`, `specs/317-canonical-remediation-submissions/research.md`, `specs/317-canonical-remediation-submissions/data-model.md`, `specs/317-canonical-remediation-submissions/contracts/remediation-submissions.md`, and `specs/317-canonical-remediation-submissions/quickstart.md`.
- [X] T002 Confirm `.specify/feature.json` points to `/work/agent_jobs/mm:51ba770c-9aeb-45d6-855a-72e6749d2c73/repo/specs/317-canonical-remediation-submissions` for downstream MoonSpec commands.
- [X] T003 Confirm `specs/317-canonical-remediation-submissions/spec.md` defines exactly one `## User Story - Create Canonical Remediation Submissions` section and preserves `MM-617` in the Input field.

## Phase 2: Foundational

- [X] T004 Map existing service evidence for FR-002, FR-003, FR-004, FR-005, FR-006, FR-008, FR-009, SC-001, SC-002, SC-003, SC-005, DESIGN-REQ-002, DESIGN-REQ-004, and DESIGN-REQ-005 in `moonmind/workflows/temporal/service.py` and `tests/unit/workflows/temporal/test_temporal_service.py`.
- [X] T005 Map existing router/read-model evidence for FR-001, FR-007, FR-009, SC-004, DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-006, and DESIGN-REQ-007 in `api_service/api/routers/executions.py`, `api_service/db/models.py`, and `tests/unit/api/routers/test_executions.py`.

## Phase 3: Story - Create Canonical Remediation Submissions

**Summary**: Operators can create a remediation task against a target execution through the normal run create path, with validated nested remediation metadata, pinned target run identity, durable bidirectional target links, and no dependency gate.

**Independent Test**: Submit a remediation create request for an existing visible target execution without a target run ID, then verify the created run contains remediation metadata, records the resolved target run, starts without dependency gating, and exposes inbound and outbound link records with compact status and lifecycle fields.

**Traceability IDs**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007.

**Unit Test Plan**: Reuse and rerun focused service tests for create-time payload preservation, target run pinning, validation failures, durable link persistence, lookup behavior, and dependency non-creation. If gaps are found, add failing unit tests first in `tests/unit/workflows/temporal/test_temporal_service.py` before production changes.

**Integration Test Plan**: Reuse and rerun FastAPI router integration-boundary tests for task-shaped creation, convenience route expansion, malformed request handling, and inbound/outbound relationship responses. If gaps are found, add failing router tests first in `tests/unit/api/routers/test_executions.py` before production changes.

### Unit Tests

- [X] T006 [P] Confirm existing service unit coverage for valid remediation creation, payload pinning, exactly one link, and no dependency prerequisites in `tests/unit/workflows/temporal/test_temporal_service.py` for FR-002, FR-003, FR-004, FR-008, SC-001, SC-002, and SC-005.
- [X] T007 [P] Confirm existing service unit coverage for missing target, run-id-as-workflow-id, missing/invisible target, non-run target, self-target, nested target, malformed/foreign taskRunIds, unsupported authorityMode, and unsupported actionPolicyRef in `tests/unit/workflows/temporal/test_temporal_service.py` for FR-005, FR-006, FR-009, SC-003, and DESIGN-REQ-005.
- [X] T008 [P] If T006 or T007 finds a coverage gap, add the smallest failing service unit test in `tests/unit/workflows/temporal/test_temporal_service.py` for the uncovered FR/SC/DESIGN-REQ before any production code changes.

### Integration Tests

- [X] T009 [P] Confirm existing router integration-boundary coverage for task-shaped remediation payload preservation and convenience-route expansion in `tests/unit/api/routers/test_executions.py` for FR-001, FR-002, FR-009, DESIGN-REQ-001, and DESIGN-REQ-003.
- [X] T010 [P] Confirm existing router integration-boundary coverage for inbound and outbound remediation relationship summaries with status, authority mode, lock, latest action, resolution, artifact ref, and timestamps in `tests/unit/api/routers/test_executions.py` for FR-007, SC-004, DESIGN-REQ-006, and DESIGN-REQ-007.
- [X] T011 [P] If T009 or T010 finds a coverage gap, add the smallest failing router integration-boundary test in `tests/unit/api/routers/test_executions.py` for the uncovered FR/SC/DESIGN-REQ before any production code changes.

### Red-First Confirmation

- [X] T012 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py tests/unit/api/routers/test_executions.py` and record whether existing tests pass; if new T008 or T011 tests were added, confirm they fail for the intended MM-617 reason before implementation.
- [X] T013 If no new failing tests were needed because all rows remain `implemented_verified`, record the verification-only red-first rationale in `specs/317-canonical-remediation-submissions/verification.md` when final verification runs.

### Conditional Fallback Implementation

- [X] T014 If service verification fails, update `moonmind/workflows/temporal/service.py` to complete create-time remediation validation, target run pinning, durable link persistence, and no-dependency behavior for FR-002, FR-003, FR-004, FR-005, FR-006, FR-008, and FR-009.
- [X] T015 If persistence fields are missing or incorrect, update `api_service/db/models.py` and the relevant migration under `api_service/migrations/versions/` to preserve remediation workflow/run identity, target workflow/run identity, status, authority, lock, latest action, resolution, and timestamps for FR-003 and FR-007.
- [X] T016 If router verification fails, update `api_service/api/routers/executions.py` to preserve task-shaped remediation metadata, expand convenience-route submissions, reject malformed remediation objects, and serialize inbound/outbound relationship summaries for FR-001, FR-007, and FR-009.
- [X] T017 Rerun `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py tests/unit/api/routers/test_executions.py` after any T014, T015, or T016 fallback change and confirm the focused suite passes.

### Story Validation

- [X] T018 Validate MM-617 acceptance scenarios against focused test evidence and code in `api_service/api/routers/executions.py`, `moonmind/workflows/temporal/service.py`, `api_service/db/models.py`, `tests/unit/api/routers/test_executions.py`, and `tests/unit/workflows/temporal/test_temporal_service.py`.
- [X] T019 Preserve MM-617 traceability for FR-010 and SC-006 in `specs/317-canonical-remediation-submissions/tasks.md`, future `specs/317-canonical-remediation-submissions/verification.md`, commit text, pull request metadata, and Jira-visible handoff.

## Final Phase: Polish And Verification

- [X] T020 Confirm `specs/317-canonical-remediation-submissions/plan.md`, `research.md`, `data-model.md`, `contracts/remediation-submissions.md`, `quickstart.md`, and `tasks.md` remain aligned with the single MM-617 story after any fallback edits.
- [X] T021 Run quickstart validation from `specs/317-canonical-remediation-submissions/quickstart.md` and record exact commands and results in `specs/317-canonical-remediation-submissions/verification.md`.
- [ ] T022 Run final `/moonspec-verify` for `specs/317-canonical-remediation-submissions` after implementation and focused tests pass, and record the verdict in `specs/317-canonical-remediation-submissions/verification.md`.

## Dependencies And Execution Order

1. Setup tasks T001-T003 must complete before foundational mapping.
2. Foundational tasks T004-T005 must complete before story test confirmation.
3. Unit and integration test review tasks T006-T011 must complete before red-first confirmation T012-T013.
4. Conditional fallback implementation tasks T014-T016 run only when T012 or added failing tests expose a gap.
5. T017 runs only after fallback implementation changes.
6. Story validation T018-T019 must complete before final polish and verification.
7. Final `/moonspec-verify` in T022 is the closing gate.

## Parallel Opportunities

- T004 and T005 can run in parallel because they inspect different code/test boundaries.
- T006, T007, T009, and T010 can run in parallel because they review different coverage groups.
- T008 and T011 can run in parallel only if both coverage gaps exist and they edit different test files.
- T014, T015, and T016 must be coordinated with failing tests and should not run unless their corresponding verification gap exists.

## Implementation Strategy

The plan classifies core behavior as `implemented_verified`, so the default path is verification-only with no production code changes. If focused verification fails, follow TDD strictly: add the smallest failing unit or integration-boundary test first, confirm red, implement only the failing boundary, rerun focused tests, then proceed to story validation and `/moonspec-verify`. Preserve MM-617 in every downstream artifact and publication handoff.
