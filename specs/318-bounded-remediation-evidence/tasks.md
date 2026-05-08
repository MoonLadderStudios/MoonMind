# Tasks: Bounded Remediation Evidence Context

**Input**: Design documents from `/work/agent_jobs/mm:a6d63116-cfbf-4474-90db-6af6f461079b/repo/specs/318-bounded-remediation-evidence/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/remediation-evidence.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended MM-618 reason, then implement the production code until they pass. Rows marked `implemented_verified` in `plan.md` keep traceability and final validation coverage without unnecessary implementation tasks.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py tests/unit/workflows/temporal/test_temporal_service.py tests/unit/api/routers/test_executions.py tests/unit/api/routers/test_task_runs.py`
- Frontend unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

**Source Traceability**: Original Jira issue `MM-618` and the canonical Jira preset brief are preserved in `spec.md`. This task list covers the single story "Diagnose With Bounded Evidence", FR-001 through FR-014, acceptance scenarios 1-6, edge cases, SC-001 through SC-006, and DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, and DESIGN-REQ-025. Plan status summary: 10 rows `implemented_verified`, 20 rows `partial`, 0 `missing`, and 0 `implemented_unverified`.

## Phase 1: Setup

**Purpose**: Confirm the active MoonSpec artifacts and test surfaces before story work.

- [ ] T001 Confirm `.specify/feature.json` points to `specs/318-bounded-remediation-evidence` and that `specs/318-bounded-remediation-evidence/spec.md` contains exactly one `## User Story - Diagnose With Bounded Evidence` section for MM-618.
- [ ] T002 Confirm planning artifacts exist and are mutually consistent in `specs/318-bounded-remediation-evidence/plan.md`, `specs/318-bounded-remediation-evidence/research.md`, `specs/318-bounded-remediation-evidence/data-model.md`, `specs/318-bounded-remediation-evidence/contracts/remediation-evidence.md`, and `specs/318-bounded-remediation-evidence/quickstart.md`.
- [ ] T003 [P] Confirm Python unit test tooling for `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` is available for `tests/unit/workflows/temporal/test_remediation_context.py`, `tests/unit/api/routers/test_executions.py`, and `tests/unit/api/routers/test_task_runs.py`.
- [ ] T004 [P] Confirm frontend unit test tooling for `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` is available.
- [ ] T005 [P] Confirm hermetic integration test tooling for `./tools/test_integration.sh` is available and identify the target integration file under `tests/integration/temporal/test_remediation_evidence_context.py`.

---

## Phase 2: Foundational

**Purpose**: Establish shared test fixtures and adapters that block the story tests and implementation.

**CRITICAL**: No production implementation work starts until foundational fixtures and red-first tests are in place.

- [ ] T006 Extend remediation context unit-test fixtures for target task-run evidence, observability refs, log refs, diagnostics refs, provider snapshots, continuity refs, and historical partial-evidence targets in `tests/unit/workflows/temporal/test_remediation_context.py` for FR-002, FR-010, FR-011, DESIGN-REQ-008, and DESIGN-REQ-025.
- [ ] T007 [P] Add reusable fake log/live-follow reader fixtures in `tests/unit/workflows/temporal/test_remediation_context.py` for declared task-run log reads, cursor resume, unavailable live follow, and policy-denied live follow covering FR-006, FR-007, FR-008, FR-009, and DESIGN-REQ-010.
- [ ] T008 [P] Add API/router test fixtures for task-run log/diagnostic authorization and bounded read responses in `tests/unit/api/routers/test_task_runs.py` for FR-006, FR-009, and DESIGN-REQ-009.
- [ ] T009 [P] Add Mission Control mock payload fixtures for remediation evidence classes, degraded evidence, and live observation states in `frontend/src/entrypoints/task-detail.test.tsx` for FR-012, Scenario 6, and SC-005.
- [ ] T010 Create hermetic integration fixture scaffolding for remediation creation, context artifact publication, declared evidence reads, unavailable live follow fallback, and forbidden payload checks in `tests/integration/temporal/test_remediation_evidence_context.py` for Scenario 1, Scenario 4, SC-001, SC-002, and DESIGN-REQ-009.

**Checkpoint**: Foundation ready; story test authoring can proceed.

---

## Phase 3: Story - Diagnose With Bounded Evidence

**Summary**: As a remediation task, I want a bounded artifact-first context bundle with typed evidence access and optional live-follow state so I can diagnose a target execution without scraping Mission Control, importing unbounded logs, or receiving raw storage access.

**Independent Test**: Create a remediation run for a target execution that has step evidence, artifact refs, and managed-run observability, then verify the remediation context artifact is linked before the remediation task begins, contains only bounded refs and summaries, exposes typed evidence access including optional live-follow state when allowed, and records missing evidence without blocking diagnosis.

**Traceability IDs**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, FR-014, Scenarios 1-6, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-025.

**Unit Test Plan**: Add focused unit tests for context payload enrichment, evidence availability, live-follow state normalization, real log/live adapter behavior, server-mediated access guardrails, fresh target health reread, and UI presentation of evidence classes.

**Integration Test Plan**: Add hermetic integration coverage for remediation context publication, linked artifact metadata, typed evidence reads, live-follow fallback behavior, and secret/path/URL exclusion from context payloads.

### Unit Tests

- [ ] T011 [P] Add failing unit tests for enriched `evidence.taskRuns` refs and compact selected-step summaries in `tests/unit/workflows/temporal/test_remediation_context.py` covering FR-002, Scenario 1, DESIGN-REQ-008, and SC-001.
- [ ] T012 [P] Add failing unit tests for context `availability` records, degraded historical targets, partial artifact refs, and merged-log fallback in `tests/unit/workflows/temporal/test_remediation_context.py` covering FR-010, FR-011, Scenario 5, SC-004, and DESIGN-REQ-025.
- [ ] T013 [P] Add failing unit tests for live-follow state normalization values `active`, `unavailable`, `unsupported`, and `policy_denied` in `tests/unit/workflows/temporal/test_remediation_context.py` covering FR-007, FR-008, Scenario 3, SC-003, and DESIGN-REQ-010.
- [ ] T014 [P] Add failing unit tests for typed log read adapter binding, stream normalization, tail-line caps, and undeclared task-run rejection in `tests/unit/workflows/temporal/test_remediation_context.py` covering FR-006, FR-009, Scenario 4, and DESIGN-REQ-009.
- [ ] T015 [P] Add failing unit tests preserving forbidden payload exclusions for enriched context fields in `tests/unit/workflows/temporal/test_remediation_context.py` covering FR-003, FR-004, Scenario 2, and SC-002.
- [ ] T016 [P] Add failing API/router unit tests for server-mediated task-run log/diagnostic reads usable by remediation evidence adapters in `tests/unit/api/routers/test_task_runs.py` covering FR-006, FR-009, and DESIGN-REQ-009.
- [ ] T017 [P] Add failing Mission Control unit tests for context, target logs, diagnostics, decision log, action request/result, verification artifact links, degraded evidence state, and live observation state in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-012, Scenario 6, and SC-005.
- [ ] T018 Confirm implemented-verified guardrail unit coverage still passes for linked artifact creation, boundedness, typed membership checks, fresh target health reread, and MM-618 traceability in `tests/unit/workflows/temporal/test_remediation_context.py` for FR-001, FR-003, FR-004, FR-005, FR-013, FR-014, SC-002, SC-006, and DESIGN-REQ-009.

### Integration Tests

- [ ] T019 [P] Add failing hermetic integration test for remediation context artifact publication and `context_artifact_ref` linkage before diagnostic work in `tests/integration/temporal/test_remediation_evidence_context.py` covering FR-001, Scenario 1, and SC-001.
- [ ] T020 [P] Add failing hermetic integration test for declared target artifact/log reads through typed server-mediated surfaces and undeclared evidence rejection in `tests/integration/temporal/test_remediation_evidence_context.py` covering FR-005, FR-006, Scenario 2, and DESIGN-REQ-009.
- [ ] T021 [P] Add failing hermetic integration test for live-follow unavailable fallback to durable merged/stdout/stderr/diagnostics refs in `tests/integration/temporal/test_remediation_evidence_context.py` covering FR-007, FR-008, FR-009, Scenario 4, and DESIGN-REQ-010.
- [ ] T022 [P] Add failing hermetic integration test for historical or partial target diagnosis with explicit degraded evidence and missing evidence classes in `tests/integration/temporal/test_remediation_evidence_context.py` covering FR-010, FR-011, Scenario 5, SC-004, and DESIGN-REQ-025.
- [ ] T023 [P] Add failing integration or contract test for Mission Control/API evidence artifact class exposure in `tests/integration/temporal/test_remediation_evidence_context.py` or `tests/unit/api/routers/test_executions.py` covering FR-012, Scenario 6, and SC-005.

### Red-First Confirmation

- [ ] T024 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py tests/unit/api/routers/test_task_runs.py` and confirm T011-T016 fail for the intended MM-618 gaps before production implementation.
- [ ] T025 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` and confirm T017 fails for the intended MM-618 evidence-presentation gaps before production implementation.
- [ ] T026 Run the focused integration command for `tests/integration/temporal/test_remediation_evidence_context.py` through `./tools/test_integration.sh` or the repo-supported integration runner and confirm T019-T023 fail for the intended MM-618 gaps before production implementation.

### Implementation

- [ ] T027 Update `moonmind/workflows/temporal/remediation_context.py` to resolve observability summary refs, stdout/stderr/merged log refs, diagnostics refs, provider snapshot refs, continuity refs, compact selected-step summaries, and target evidence availability for FR-002, FR-010, FR-011, DESIGN-REQ-008, and DESIGN-REQ-025.
- [ ] T028 Update `moonmind/workflows/temporal/remediation_context.py` to compute live-follow state values `active`, `unavailable`, `unsupported`, and `policy_denied`, plus durable fallbacks and resume cursor state when available, for FR-007, FR-008, FR-009, SC-003, and DESIGN-REQ-010.
- [ ] T029 Update `moonmind/workflows/temporal/remediation_tools.py` to bind or expose real task-run log/live-follow reader adapters while preserving context membership checks, tail-line caps, cursor handling, and server-mediated access for FR-005, FR-006, FR-008, FR-009, and DESIGN-REQ-009.
- [ ] T030 Update `api_service/api/routers/task_runs.py` only if required by T016/T020 to provide bounded server-mediated log/diagnostic reads for remediation evidence without exposing storage paths, presigned URLs, or secret-bearing details for FR-006, FR-009, and DESIGN-REQ-009.
- [ ] T031 Update `api_service/api/routers/executions.py` only if required by T023 to serialize remediation evidence artifact classes, live observation state, and degraded evidence summaries for Mission Control without broadening MM-618 scope for FR-012 and SC-005.
- [ ] T032 Update `frontend/src/entrypoints/task-detail.tsx` to render context, target logs, diagnostics, decision log, action request/result, verification artifacts, degraded evidence, and live observation/fallback state for FR-012, Scenario 6, and SC-005.
- [ ] T033 Update `frontend/src/styles/mission-control.css` only if T017 requires evidence presentation layout, containment, or accessibility styling changes for FR-012 and Scenario 6.
- [ ] T034 Preserve existing fresh target health reread behavior in `moonmind/workflows/temporal/remediation_tools.py` and adjust only if T018 exposes a regression for FR-013.
- [ ] T035 Preserve MM-618 traceability in `specs/318-bounded-remediation-evidence/tasks.md`, future `specs/318-bounded-remediation-evidence/verification.md`, commit text, pull request metadata, and Jira-visible handoff for FR-014 and SC-006.

### Story Validation

- [ ] T036 Run focused backend unit tests with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py tests/unit/api/routers/test_task_runs.py tests/unit/api/routers/test_executions.py` and fix failures in the files touched by T027-T031.
- [ ] T037 Run focused frontend tests with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` and fix failures in `frontend/src/entrypoints/task-detail.tsx` or `frontend/src/styles/mission-control.css`.
- [ ] T038 Run hermetic integration validation with `./tools/test_integration.sh` and confirm MM-618 integration tests pass or record the exact environment blocker in `specs/318-bounded-remediation-evidence/verification.md`.
- [ ] T039 Validate the independent story criteria from `specs/318-bounded-remediation-evidence/quickstart.md` against implemented behavior and record command evidence for FR-001 through FR-014, Scenarios 1-6, SC-001 through SC-006, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, and DESIGN-REQ-025.

**Checkpoint**: The MM-618 story is fully functional, covered by unit and integration tests, and independently testable.

---

## Final Phase: Polish And Verification

**Purpose**: Strengthen the completed MM-618 story without adding hidden scope.

- [ ] T040 [P] Review `moonmind/workflows/temporal/remediation_context.py` and `moonmind/workflows/temporal/remediation_tools.py` for duplicated evidence-normalization logic and refactor only within the MM-618 boundaries.
- [ ] T041 [P] Review `specs/318-bounded-remediation-evidence/plan.md`, `research.md`, `data-model.md`, `contracts/remediation-evidence.md`, `quickstart.md`, and `tasks.md` for traceability drift after implementation.
- [ ] T042 [P] Run forbidden-content checks or focused assertions to ensure remediation context/lifecycle payloads still exclude presigned URLs, storage keys, absolute local paths, and secrets in `tests/unit/workflows/temporal/test_remediation_context.py`.
- [ ] T043 Run full unit verification with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` after focused backend and frontend tests pass.
- [ ] T044 Run quickstart validation from `specs/318-bounded-remediation-evidence/quickstart.md` and record exact commands and results in `specs/318-bounded-remediation-evidence/verification.md`.
- [ ] T045 Run final `/moonspec-verify` for `specs/318-bounded-remediation-evidence` after implementation and tests pass, and record the verdict in `specs/318-bounded-remediation-evidence/verification.md`.

---

## Dependencies And Execution Order

### Phase Dependencies

- Setup T001-T005 can start immediately.
- Foundational T006-T010 depends on Setup completion and blocks story implementation.
- Story tests T011-T023 depend on foundational fixtures.
- Red-first confirmation T024-T026 depends on story tests being written.
- Implementation T027-T035 depends on red-first confirmation.
- Story validation T036-T039 depends on implementation.
- Polish and final verification T040-T045 depends on story validation.

### Within The Story

- Unit tests T011-T018 must be written before implementation tasks T027-T035.
- Integration tests T019-T023 must be written before implementation tasks T027-T035.
- Red-first confirmation T024-T026 must complete before production code changes.
- Context/data-model implementation T027-T028 must precede evidence tool adapter work T029 where adapter behavior depends on enriched context shape.
- Backend service/API work T027-T031 must precede frontend rendering work T032-T033.
- Story validation T036-T039 must pass before final polish T040-T045.

## Parallel Opportunities

- T003, T004, and T005 can run in parallel because they inspect different test tooling.
- T007, T008, T009, and T010 can run in parallel after T006 when fixture ownership is coordinated.
- T011 through T017 can be authored in parallel where each task edits its named test file area without conflicting changes.
- T019 through T023 can be authored in parallel if they use separate test functions in `tests/integration/temporal/test_remediation_evidence_context.py`.
- T030 and T031 can run in parallel with T032 after T027-T029 stabilize the backend contract.
- T040, T041, and T042 can run in parallel after story validation.

## Parallel Example

```bash
# Backend and frontend red-first test authoring can proceed together:
Task: "T011 Add failing context evidence refs unit tests in tests/unit/workflows/temporal/test_remediation_context.py"
Task: "T017 Add failing Mission Control evidence presentation tests in frontend/src/entrypoints/task-detail.test.tsx"

# Independent implementation follow-ups after context shape stabilizes:
Task: "T030 Update task-run router bounded reads in api_service/api/routers/task_runs.py"
Task: "T032 Update Mission Control evidence rendering in frontend/src/entrypoints/task-detail.tsx"
```

## Implementation Strategy

1. Complete setup and foundational fixture tasks.
2. Write all focused unit and integration tests first.
3. Run red-first confirmation and verify failures are for the MM-618 partial requirements from `plan.md`.
4. Implement enriched context generation, live-follow state, typed evidence adapter binding, API serialization, and UI presentation in that order.
5. Preserve already-verified behavior for artifact linkage, boundedness, secret safety, typed membership checks, fresh target health reread, and MM-618 traceability.
6. Run focused backend, frontend, and integration tests.
7. Run full unit validation and quickstart validation.
8. Finish with `/moonspec-verify`.

## Requirement Status Coverage Summary

- Code-and-test work for `partial` rows: FR-002, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, Scenarios 1 and 3-6, SC-001, SC-003, SC-004, SC-005, DESIGN-REQ-008, DESIGN-REQ-010, DESIGN-REQ-025.
- Integration reinforcement for implemented rows: FR-001, FR-003, FR-005, FR-013, Scenario 2, DESIGN-REQ-009.
- Already-verified final validation only: FR-004, FR-014, SC-002, SC-006.
- Conditional fallback rows: none, because `plan.md` has no `implemented_unverified` rows.
- Missing rows: none.

## Notes

- This task list covers exactly one story: MM-618 "Diagnose With Bounded Evidence".
- Do not create new specs, run `/moonspec-breakdown`, or broaden into remediation action-authority behavior beyond the fresh-health guard already in FR-013.
- Do not create `tasks.md` work for future MM-619 authority/policy scope except preserving linked-issue awareness in traceability.
