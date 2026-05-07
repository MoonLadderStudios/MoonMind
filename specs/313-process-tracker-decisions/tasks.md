# Tasks: Process Verified Tracker Decisions

**Input**: Design documents from `specs/313-process-tracker-decisions/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped around the single MM-599 story so the work stays focused, traceable, and independently testable.

**Source Traceability**: MM-599 and the original Jira preset brief are preserved in `spec.md`. Tasks cover FR-001 through FR-018, SCN-001 through SCN-007, SC-001 through SC-007, and DESIGN-REQ-002, DESIGN-REQ-021, DESIGN-REQ-022, DESIGN-REQ-023, DESIGN-REQ-024, DESIGN-REQ-025, DESIGN-REQ-026, DESIGN-REQ-031.

**Requirement Status Summary**: 5 missing, 26 partial, 8 implemented_unverified, 1 implemented_verified. Missing and partial rows require code plus tests. Implemented_unverified rows require verification tests plus conditional fallback implementation if tests fail. FR-008 is implemented_verified and remains covered by final validation.

**Test Commands**:

- Unit tests: `python -m pytest tests/unit/workflows/task_proposals/test_delivery.py tests/unit/workflows/task_proposals/test_service.py tests/unit/api/routers/test_task_proposals.py tests/unit/workflows/temporal/test_proposal_activities.py -q`
- Integration tests: `python -m pytest tests/integration/temporal/test_proposal_review_delivery.py -q`
- Full unit verification: `./tools/test_unit.sh`
- Integration CI verification if `integration_ci` tests are added: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because it touches different files and has no dependency on incomplete work.
- Every task includes concrete file paths and requirement, scenario, success, or source IDs where applicable.

## Phase 1: Setup

**Purpose**: Confirm the current planning artifacts and test targets before writing red tests.

- [ ] T001 Confirm active MM-599 artifacts and `.specify/feature.json` point to `specs/313-process-tracker-decisions/` and record any mismatch in `specs/313-process-tracker-decisions/tasks.md` (FR-018, SC-007)
- [ ] T002 [P] Review current focused unit test files for proposal decision coverage in `tests/unit/workflows/task_proposals/test_delivery.py`, `tests/unit/workflows/task_proposals/test_service.py`, `tests/unit/api/routers/test_task_proposals.py`, and `tests/unit/workflows/temporal/test_proposal_activities.py` (FR-001 through FR-017)
- [ ] T003 [P] Review current integration boundary coverage in `tests/integration/temporal/test_proposal_review_delivery.py` and identify reusable fixtures for provider approval, duplicate event, rejected event, and recovery scenarios (SCN-001 through SCN-007)

---

## Phase 2: Foundational

**Purpose**: Establish blocking contracts and fixtures needed before story implementation.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [ ] T004 Add or update shared provider decision fixture builders in `tests/unit/workflows/task_proposals/test_service.py` for verified actors, provider event IDs, external issue state, runtime controls, explicit skills, authored presets, and step source provenance (FR-009, FR-010, FR-011, FR-012, DESIGN-REQ-023)
- [ ] T005 [P] Add or update API test fixture helpers in `tests/unit/api/routers/test_task_proposals.py` for GitHub/Jira webhook authenticity inputs, actor policy, and recovery/admin requests (FR-003, FR-004, FR-016, DESIGN-REQ-022, DESIGN-REQ-026)
- [ ] T006 [P] Add or update integration test fixture helpers in `tests/integration/temporal/test_proposal_review_delivery.py` for fake proposal service, fake execution service, delivered proposal records, and duplicate provider event replay (FR-006, FR-014, SCN-001, SCN-004)
- [ ] T007 Document any required state-shape decision for deferred and request-revision outcomes in `specs/313-process-tracker-decisions/data-model.md` before implementation if existing enum values are reused through metadata instead of new statuses (FR-007, FR-012, DESIGN-REQ-024)

**Checkpoint**: Foundation ready; red-first story test work can begin.

---

## Phase 3: Story - Process Verified Tracker Decisions

**Summary**: As a reviewer, I want verified external tracker decisions to either promote approved proposals into new MoonMind runs or record non-executing decisions, so MoonMind executes only trusted stored proposal snapshots and keeps review state auditable.

**Independent Test**: Submit representative approved, dismissed, deferred, reprioritized, request-revision, duplicate, unauthorized, and invalid-runtime tracker decisions for an existing proposal delivery and confirm proposal state, run creation behavior, audit details, and external issue updates.

**Traceability**: FR-001 through FR-018; SCN-001 through SCN-007; SC-001 through SC-007; DESIGN-REQ-002, DESIGN-REQ-021, DESIGN-REQ-022, DESIGN-REQ-023, DESIGN-REQ-024, DESIGN-REQ-025, DESIGN-REQ-026, DESIGN-REQ-031.

**Unit Test Plan**:

- Parser and schema tests for all five decisions, bounded controls, runtime override validation, missing provider event IDs, unsupported actions, and edited issue body safety.
- Service tests for authenticity/authorization results, provider decision audit rows, duplicate events, non-executing outcomes, stored-snapshot promotion, skill/preset provenance preservation, redaction, and external issue state metadata.
- API/router tests for trusted webhook or recovery endpoints, validation failures, sanitized errors, and canonical response shapes.

**Integration Test Plan**:

- Boundary tests for verified provider approval to one new run through canonical execution creation.
- Duplicate provider approval replay creates no second run.
- Unverified or unauthorized provider events do not create runs and persist only sanitized rejection evidence.
- Non-executing decisions record external state and create zero runs.
- Recovery inspection/sync/promote behavior follows the same validation and idempotency contract.

### Unit Tests (write first)

- [ ] T008 [P] Add failing unit tests for request-revision parsing, structured provider actions, blank provider event IDs, unsupported actions, and bounded runtime controls in `tests/unit/workflows/task_proposals/test_delivery.py` (FR-002, FR-013, DESIGN-REQ-021, DESIGN-REQ-025)
- [ ] T009 [P] Add failing unit tests for provider authenticity and actor authorization rejection helpers in `tests/unit/api/routers/test_task_proposals.py` or the new router test file chosen for provider decision ingress (FR-003, FR-004, FR-005, DESIGN-REQ-022, DESIGN-REQ-031)
- [ ] T010 [P] Add failing unit tests for `TaskProposalService.record_provider_decision_event()` audit rows covering dismiss, defer, reprioritize, request revision, external issue state, sanitized rejected decisions, and duplicate event replay in `tests/unit/workflows/task_proposals/test_service.py` (FR-007, FR-012, FR-014, FR-017, SC-001)
- [ ] T011 [P] Add failing unit tests for provider approval promotion orchestration preserving stored snapshot, explicit skill selectors, `task.authoredPresets`, and `steps[].source` in `tests/unit/workflows/task_proposals/test_service.py` (FR-006, FR-008, FR-009, FR-010, FR-011, DESIGN-REQ-023)
- [ ] T012 [P] Add failing unit tests for runtime override validation and failure-before-run behavior for provider approval controls in `tests/unit/workflows/task_proposals/test_service.py` (FR-013, SCN-006, SC-004, DESIGN-REQ-025)
- [ ] T013 [P] Add failing unit tests for recovery/inspection response models or route handlers in `tests/unit/api/routers/test_task_proposals.py` covering promoted run ID, decision history, redeliver/sync/promote controls, and sanitized failure output (FR-015, FR-016, DESIGN-REQ-026)
- [ ] T014 Run `python -m pytest tests/unit/workflows/task_proposals/test_delivery.py tests/unit/workflows/task_proposals/test_service.py tests/unit/api/routers/test_task_proposals.py tests/unit/workflows/temporal/test_proposal_activities.py -q` and confirm T008-T013 fail for missing MM-599 behavior, not unrelated setup errors (FR-001 through FR-017)

### Integration Tests (write first)

- [ ] T015 [P] Add failing integration test for verified provider approval creating exactly one MoonMind.Run from the stored snapshot in `tests/integration/temporal/test_proposal_review_delivery.py` (SCN-001, FR-001, FR-006, SC-002, DESIGN-REQ-002)
- [ ] T016 [P] Add failing integration test proving edited external issue text and Jira ADF are ignored through provider decision ingestion in `tests/integration/temporal/test_proposal_review_delivery.py` (SCN-002, FR-008, FR-009, SC-003, DESIGN-REQ-023)
- [ ] T017 [P] Add failing integration test for dismiss, defer, reprioritize, and request-revision decisions persisting audit state and creating zero runs in `tests/integration/temporal/test_proposal_review_delivery.py` (SCN-003, FR-007, FR-012, SC-005, DESIGN-REQ-024)
- [ ] T018 [P] Add failing integration test for duplicate provider approval replay creating zero additional runs and returning prior outcome in `tests/integration/temporal/test_proposal_review_delivery.py` (SCN-004, FR-014, SC-002)
- [ ] T019 [P] Add failing integration test for unverified, unauthorized, and policy-denied provider events stopping before run creation with sanitized audit evidence in `tests/integration/temporal/test_proposal_review_delivery.py` (SCN-005, FR-003, FR-004, FR-005, FR-017, DESIGN-REQ-031)
- [ ] T020 [P] Add failing integration test for recovery inspection/sync/promote behavior or the selected equivalent proposal recovery API in `tests/integration/temporal/test_proposal_review_delivery.py` (SCN-007, FR-015, FR-016, DESIGN-REQ-026)
- [ ] T021 Run `python -m pytest tests/integration/temporal/test_proposal_review_delivery.py -q` and confirm T015-T020 fail for missing MM-599 behavior, not unrelated setup errors (SCN-001 through SCN-007)

### Red-First Confirmation

- [ ] T022 Record the expected red test failures from T014 and T021 in `specs/313-process-tracker-decisions/tasks.md` or implementation notes before editing production code (FR-001 through FR-017)

### Conditional Fallback Implementation for Implemented-Unverified Rows

- [ ] T023 If T011 or T016 shows stored-snapshot promotion safety is not actually preserved through provider ingestion, update `moonmind/workflows/task_proposals/service.py` to force provider approvals through stored `task_create_request` only (FR-009, SCN-002, SC-003)
- [ ] T024 If T011 shows explicit skill selectors, `task.authoredPresets`, or `steps[].source` are dropped, update `moonmind/workflows/task_proposals/service.py` and serialization helpers in `api_service/api/routers/task_proposals.py` to preserve stored provenance fields (FR-010, FR-011, DESIGN-REQ-023)
- [ ] T025 If T015 or T018 shows duplicate provider approvals can create duplicate runs, update `moonmind/workflows/task_proposals/service.py` and the execution invocation path in `api_service/api/routers/task_proposals.py` to reuse stable provider/proposal idempotency keys (FR-014, SC-002)
- [ ] T026 If T013 or final traceability checks show MM-599 is missing from downstream evidence, update `specs/313-process-tracker-decisions/tasks.md` and later verification notes to preserve MM-599 explicitly (FR-018, SC-007)

### Implementation

- [ ] T027 Update provider decision models and parsing in `moonmind/workflows/task_proposals/delivery.py` to support `request_revision`, runtime controls, required provider event IDs, normalized `reprioritize`, and safe bounded control extraction (FR-002, FR-013, DESIGN-REQ-021, DESIGN-REQ-025)
- [ ] T028 Update schema models in `moonmind/schemas/task_proposal_models.py` for provider decision ingress, decision results, recovery requests, promoted execution IDs, non-executing outcomes, and sanitized rejection metadata (FR-003, FR-005, FR-015, FR-016)
- [ ] T029 Update `moonmind/workflows/task_proposals/service.py` to validate provider identity, authenticity result, actor authorization policy, allowed actions, duplicate provider event IDs, and sanitized rejected decisions before state mutation (FR-003, FR-004, FR-005, FR-014, FR-017, DESIGN-REQ-022, DESIGN-REQ-031)
- [ ] T030 Update `moonmind/workflows/task_proposals/service.py` to apply non-executing dismiss, defer, reprioritize, and request-revision decisions with full audit metadata, external issue state, and no run creation (FR-007, FR-012, SC-001, SC-005, DESIGN-REQ-024)
- [ ] T031 Update `moonmind/workflows/task_proposals/service.py` to prepare accepted provider promotion from the stored proposal snapshot plus bounded controls and return a promotion request/result without trusting provider issue body text (FR-001, FR-006, FR-008, FR-009, DESIGN-REQ-002, DESIGN-REQ-023)
- [ ] T032 Update `api_service/api/routers/task_proposals.py` or add a focused provider decision router under `api_service/api/routers/` for GitHub/Jira webhook ingestion, shared-secret/signature verification, provider event normalization, service invocation, and sanitized responses (FR-003, FR-004, FR-005, SCN-005, DESIGN-REQ-022, DESIGN-REQ-031)
- [ ] T033 Update `api_service/api/routers/task_proposals.py` to bridge accepted provider promotions to `TemporalExecutionService.create_execution()` through the same canonical run creation path as manual promotion and persist/expose the promoted execution ID (FR-006, FR-015, SCN-001, SC-006)
- [ ] T034 Update recovery or admin surfaces in `api_service/api/routers/task_proposals.py` or a dedicated router to inspect decision history, redeliver or sync external issue state, and controlled-promote through the same validation path (FR-016, SCN-007, DESIGN-REQ-026)
- [ ] T035 Update provider issue status/comment integration in `moonmind/workflows/task_proposals/delivery.py` or trusted provider adapter calls so successful promoted and non-executing decisions can update external issue state without leaking secrets (FR-015, FR-017, SC-006)
- [ ] T036 Update Temporal activity or workflow boundary helpers in `moonmind/workflows/temporal/activity_runtime.py` or `moonmind/workflows/task_proposals/service.py` only if provider-decision run creation needs an activity-bound contract instead of direct API/service orchestration (FR-006, FR-014, DESIGN-REQ-002)

### Story Validation

- [ ] T037 Run `python -m pytest tests/unit/workflows/task_proposals/test_delivery.py tests/unit/workflows/task_proposals/test_service.py tests/unit/api/routers/test_task_proposals.py tests/unit/workflows/temporal/test_proposal_activities.py -q` and fix MM-599 unit failures in `moonmind/workflows/task_proposals/`, `moonmind/schemas/task_proposal_models.py`, and `api_service/api/routers/task_proposals.py` (FR-001 through FR-017)
- [ ] T038 Run `python -m pytest tests/integration/temporal/test_proposal_review_delivery.py -q` and fix MM-599 integration failures in `api_service/api/routers/task_proposals.py`, `moonmind/workflows/task_proposals/service.py`, and `moonmind/workflows/task_proposals/delivery.py` (SCN-001 through SCN-007)
- [ ] T039 Verify the contract in `specs/313-process-tracker-decisions/contracts/proposal-decision-ingestion-contract.md` against implemented request/response shapes and update the contract only if implementation discoveries refine the same MM-599 story without broadening scope (FR-015, FR-016, SC-006)

**Checkpoint**: The single MM-599 story is implemented, covered by red-first unit and integration tests, and independently testable.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T040 [P] Add edge-case unit coverage in `tests/unit/workflows/task_proposals/test_delivery.py` for malformed provider payloads, secret-like fields, unknown decisions, and blank event IDs not already covered by T008-T013 (FR-005, FR-017)
- [ ] T041 [P] Add edge-case service coverage in `tests/unit/workflows/task_proposals/test_service.py` for already-promoted, dismissed, deferred, and revision-requested proposals receiving new provider decisions (FR-014, SC-004, SC-005)
- [ ] T042 [P] Review and tighten redaction/logging paths in `moonmind/workflows/task_proposals/service.py`, `moonmind/workflows/task_proposals/delivery.py`, and `api_service/api/routers/task_proposals.py` for raw signatures, shared secrets, auth headers, tokens, cookies, and private keys (FR-017, DESIGN-REQ-031)
- [ ] T043 Run quickstart validation from `specs/313-process-tracker-decisions/quickstart.md` and record any deviations in `specs/313-process-tracker-decisions/tasks.md` or final verification notes (SCN-001 through SCN-007)
- [ ] T044 Run `./tools/test_unit.sh` for full required unit verification and fix failures in files touched for MM-599 (FR-001 through FR-018)
- [ ] T045 Run `./tools/test_integration.sh` if any MM-599 tests are marked `integration_ci`; otherwise document why focused non-`integration_ci` boundary tests are sufficient in final verification notes (SCN-001 through SCN-007)
- [ ] T046 Confirm `MM-599` and the original Jira preset brief remain preserved across `specs/313-process-tracker-decisions/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/proposal-decision-ingestion-contract.md`, `quickstart.md`, `tasks.md`, and final evidence (FR-018, SC-007)
- [ ] T047 Run `/moonspec-verify` after implementation and all required tests pass, then record the verdict in final verification evidence for `specs/313-process-tracker-decisions/` (FR-018, SC-007)

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (Phase 1): no dependencies.
- Foundational (Phase 2): depends on Setup completion and blocks story work.
- Story (Phase 3): depends on Foundational completion.
- Polish and Verification (Phase 4): depends on story implementation and focused tests passing.

### Within The Story

- T008-T013 unit tests must be written before T014.
- T015-T020 integration tests must be written before T021.
- T014 and T021 must confirm red-first failures before T022.
- T023-T026 are conditional fallback tasks for implemented_unverified rows and execute only if red/verification tests expose gaps.
- T027-T036 production implementation starts only after red-first confirmation.
- T037-T039 validate the completed story before polish.

### Parallel Opportunities

- T002 and T003 can run in parallel.
- T005 and T006 can run in parallel after T004 is understood.
- T008-T013 can be authored in parallel where they touch different test files.
- T015-T020 can be authored in parallel in the same integration file only if coordinated to avoid overlapping fixtures; otherwise keep sequential.
- T040-T042 can run in parallel after story validation.

## Parallel Example: Story Test Authoring

```bash
# Safe parallel test-authoring examples:
Task: "T008 Add parser tests in tests/unit/workflows/task_proposals/test_delivery.py"
Task: "T009 Add route/auth tests in tests/unit/api/routers/test_task_proposals.py"
Task: "T010 Add service audit tests in tests/unit/workflows/task_proposals/test_service.py"
```

## Implementation Strategy

1. Confirm active artifacts and reusable fixtures.
2. Add red-first unit tests for parser, service, route, recovery, runtime, provenance, and redaction behavior.
3. Add red-first integration tests for provider approval, duplicate replay, rejected events, non-executing decisions, and recovery.
4. Confirm the new tests fail for missing MM-599 behavior.
5. Implement bounded provider decision parsing and schemas.
6. Implement trusted provider decision ingestion, actor authorization, idempotency, non-executing decisions, and provider approval run promotion.
7. Wire API/recovery surfaces and external issue state updates.
8. Run focused unit and integration tests, then full unit verification.
9. Finish with quickstart validation and `/moonspec-verify`.

## Notes

- This task list covers exactly one story: MM-599 Process Verified Tracker Decisions.
- FR-008 is already implemented_verified in the plan, so tasks preserve and boundary-test it rather than rebuilding it.
- Implemented_unverified rows have explicit conditional fallback tasks T023-T026.
- Code-and-test work covers missing and partial rows; final validation preserves already-verified evidence.
