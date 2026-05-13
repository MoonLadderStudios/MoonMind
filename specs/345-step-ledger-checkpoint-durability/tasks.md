# Tasks: Step Ledger Checkpoint Durability

**Input**: Design documents from `/work/agent_jobs/mm:017bbd8c-2454-4c77-ac2c-f8d42e1c7916/repo/specs/345-step-ledger-checkpoint-durability/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks cover exactly one story: Durable Step Evidence for Resume.

**Source Traceability**: MM-646; FR-001 through FR-010; SCN-001 through SCN-007; SC-001 through SC-008; DESIGN-REQ-001 through DESIGN-REQ-007; original coverage IDs DESIGN-REQ-019 and DESIGN-REQ-023.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/workflows/tasks/test_prepared_context.py tests/unit/workflows/temporal/test_step_ledger.py tests/unit/workflows/temporal/workflows/test_run_step_ledger.py tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py tests/unit/workflows/temporal/test_temporal_service.py`
- Integration tests: `./tools/test_integration.sh`
- Focused integration checks: `./tools/test_unit.sh tests/integration/temporal/test_backend_resume_eligibility.py tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because the task touches different files and does not depend on incomplete work.
- Every task includes exact file paths and traceability IDs where applicable.

## Phase 1: Setup

**Purpose**: Confirm the one-story planning context and active feature artifacts before writing tests.

- [X] T001 Confirm the active feature artifacts and one-story scope in `specs/345-step-ledger-checkpoint-durability/spec.md`, `specs/345-step-ledger-checkpoint-durability/plan.md`, and `specs/345-step-ledger-checkpoint-durability/research.md` for MM-646, FR-010, and SC-008
- [X] T002 Confirm no existing `specs/345-step-ledger-checkpoint-durability/tasks.md` implementation state is being preserved from an older run by reviewing `git status --short specs/345-step-ledger-checkpoint-durability/tasks.md`
- [X] T003 Confirm focused unit and integration commands from `specs/345-step-ledger-checkpoint-durability/quickstart.md` are available before story work starts

---

## Phase 2: Foundational

**Purpose**: Identify the existing workflow, schema, and service boundaries that all story tests and implementation tasks will touch.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T004 Map current prepared-input helper behavior in `moonmind/workflows/tasks/prepared_context.py` and existing tests in `tests/unit/workflows/tasks/test_prepared_context.py` for FR-001, SCN-001, SC-001, DESIGN-REQ-002
- [X] T005 Map current step ledger schema/helper behavior in `moonmind/schemas/temporal_models.py`, `moonmind/workflows/temporal/step_ledger.py`, and `tests/unit/workflows/temporal/test_step_ledger.py` for FR-002, FR-007, FR-008, SCN-002, SCN-006, DESIGN-REQ-003, DESIGN-REQ-005
- [X] T006 Map current parent workflow evidence projection in `moonmind/workflows/temporal/workflows/run.py` and existing tests in `tests/unit/workflows/temporal/workflows/test_run_step_ledger.py` for FR-002, FR-003, FR-006, FR-009, SCN-002, SCN-003, SCN-007, DESIGN-REQ-001, DESIGN-REQ-004, DESIGN-REQ-006, DESIGN-REQ-007
- [X] T007 Map current Resume checkpoint validation and recovery service behavior in `moonmind/workflows/temporal/service.py`, `moonmind/schemas/temporal_models.py`, and `tests/unit/workflows/temporal/test_temporal_service.py` for FR-004, FR-005, FR-006, SCN-004, SCN-005, DESIGN-REQ-004, DESIGN-REQ-007

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Durable Step Evidence for Resume

**Summary**: As an execution-plane engineer, I want task runs to durably record prepared input refs, per-step output refs, and workspace checkpoints around step boundaries so that failed-step Resume eligibility is provable from durable evidence.

**Independent Test**: Execute representative task runs with successful preparation, successful steps, workspace-mutating steps, retried checkpoint writes, large checkpoint payloads, and completed steps missing recovery evidence; verify that durable refs and checkpoint evidence are recorded when required, remain bounded, are idempotent under retry, and drive Resume eligibility or ineligibility decisions.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SCN-006, SCN-007, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, SC-007, SC-008, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007.

**Unit Test Plan**: prepared refs, semantic output refs, state checkpoint refs, idempotent checkpoint identity, no inline large/binary content, Resume preservation eligibility markers, bounded ineligible reasons, and delegated-step parent ownership.

**Integration Test Plan**: parent `MoonMind.Run` or Temporal execution service boundary scenarios for successful prepare, successful step output evidence, workspace-mutating checkpoint emission, retry/idempotency, no-inline checkpoint payloads, missing evidence ineligibility, and delegated child evidence projection.

### Unit Tests (write first)

- [X] T008 [P] Add failing unit tests for durable prepared refs and compact prepared evidence in `tests/unit/workflows/tasks/test_prepared_context.py` covering FR-001, FR-005, SCN-001, SCN-005, SC-001, SC-005, DESIGN-REQ-002
- [X] T009 [P] Add failing unit tests for `stateCheckpointRef`, `resumePreservation`, semantic output refs, and bounded ineligible reasons in `tests/unit/workflows/temporal/test_step_ledger.py` covering FR-002, FR-003, FR-007, FR-008, SCN-002, SCN-003, SCN-006, SC-002, SC-003, SC-006, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005
- [X] T010 [P] Add failing unit tests for parent `MoonMind.Run` checkpoint/evidence projection in `tests/unit/workflows/temporal/workflows/test_run_step_ledger.py` covering FR-001, FR-002, FR-003, FR-004, FR-006, FR-009, SCN-001, SCN-002, SCN-003, SCN-004, SCN-007, SC-001, SC-002, SC-003, SC-004, SC-007, DESIGN-REQ-001, DESIGN-REQ-004, DESIGN-REQ-006, DESIGN-REQ-007
- [X] T011 [P] Add failing unit tests for Resume checkpoint validation of prepared refs, preserved-step checkpoint refs, no-inline checkpoint payloads, and plan/workspace evidence in `tests/unit/workflows/temporal/test_temporal_service.py` covering FR-004, FR-005, FR-006, FR-008, SCN-004, SCN-005, SCN-006, SC-004, SC-005, SC-006, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-007
- [X] T012 [P] Add verification-first unit tests for delegated child/runtime checkpoint ownership in `tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py` covering implemented_unverified FR-009, SCN-007, SC-007, DESIGN-REQ-006

### Integration Tests (write first)

- [X] T013 [P] Add failing integration coverage for parent run durable prepared refs, completed-step refs, state checkpoint refs, and missing evidence ineligibility in `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` covering FR-001, FR-002, FR-003, FR-006, FR-007, FR-008, SCN-001, SCN-002, SCN-003, SCN-006, SC-001, SC-002, SC-003, SC-006, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-007
- [X] T014 [P] Add failing integration coverage for Resume eligibility from durable evidence, delegated child ownership, idempotent checkpoint retry, and no-inline checkpoint payloads in `tests/integration/temporal/test_backend_resume_eligibility.py` covering FR-004, FR-005, FR-006, FR-009, SCN-004, SCN-005, SCN-007, SC-004, SC-005, SC-007, DESIGN-REQ-004, DESIGN-REQ-006, DESIGN-REQ-007

### Red-First Confirmation

- [X] T015 Run `./tools/test_unit.sh tests/unit/workflows/tasks/test_prepared_context.py tests/unit/workflows/temporal/test_step_ledger.py tests/unit/workflows/temporal/workflows/test_run_step_ledger.py tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py tests/unit/workflows/temporal/test_temporal_service.py` and confirm T008-T012 fail for the intended missing/partial MM-646 behavior before production changes
- [X] T016 Run `./tools/test_unit.sh tests/integration/temporal/test_backend_resume_eligibility.py tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` and confirm T013-T014 fail for the intended missing/partial MM-646 behavior before production changes

### Conditional Fallback for Implemented-Unverified Rows

- [X] T017 If T012 or T014 show delegated-step parent ownership is already fully covered, record FR-009 as verification-only in `specs/345-step-ledger-checkpoint-durability/verification.md`; otherwise keep T026 in scope as the fallback implementation task for FR-009, SCN-007, SC-007, DESIGN-REQ-006

### Implementation

- [X] T018 Add or extend prepared-ref extraction for parent resume evidence in `moonmind/workflows/tasks/prepared_context.py` covering FR-001, FR-005, SCN-001, SCN-005, SC-001, SC-005, DESIGN-REQ-002
- [X] T019 Add step ledger schema fields or bounded models for `stateCheckpointRef` and `resumePreservation` in `moonmind/schemas/temporal_models.py` covering FR-003, FR-007, FR-008, SCN-003, SCN-006, SC-003, SC-006, DESIGN-REQ-004, DESIGN-REQ-005
- [X] T020 Add step ledger helper logic for semantic output refs, state checkpoint refs, eligibility markers, and bounded ineligible reasons in `moonmind/workflows/temporal/step_ledger.py` covering FR-002, FR-003, FR-007, FR-008, SCN-002, SCN-003, SCN-006, SC-002, SC-003, SC-006, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005
- [X] T021 Implement parent `MoonMind.Run` prepared-ref recording, step-boundary checkpoint evidence, idempotent checkpoint identity, and no-inline payload handling in `moonmind/workflows/temporal/workflows/run.py` covering FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-007
- [X] T022 Wire durable checkpoint evidence into Resume checkpoint hydration and eligibility validation in `moonmind/workflows/temporal/service.py` covering FR-006, FR-007, FR-008, SCN-006, SC-006, DESIGN-REQ-005, DESIGN-REQ-007
- [X] T023 Ensure Resume checkpoint payload validation remains compact and rejects missing or inline checkpoint evidence in `moonmind/schemas/temporal_models.py` covering FR-004, FR-005, FR-006, SCN-004, SCN-005, SC-004, SC-005, DESIGN-REQ-004, DESIGN-REQ-007
- [X] T024 Update parent workflow result mapping so managed-session, workload, and child runtime checkpoint/output refs project into parent-owned evidence in `moonmind/workflows/temporal/workflows/run.py` covering FR-009, SCN-007, SC-007, DESIGN-REQ-006
- [X] T025 Preserve MM-646 and original preset brief traceability in implementation comments only where needed and in downstream evidence artifacts under `specs/345-step-ledger-checkpoint-durability/` covering FR-010, SC-008

### Story Validation

- [X] T026 Run `./tools/test_unit.sh tests/unit/workflows/tasks/test_prepared_context.py tests/unit/workflows/temporal/test_step_ledger.py tests/unit/workflows/temporal/workflows/test_run_step_ledger.py tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py tests/unit/workflows/temporal/test_temporal_service.py` and fix failures in `moonmind/workflows/tasks/prepared_context.py`, `moonmind/schemas/temporal_models.py`, `moonmind/workflows/temporal/step_ledger.py`, `moonmind/workflows/temporal/workflows/run.py`, or `moonmind/workflows/temporal/service.py`
- [X] T027 Run `./tools/test_unit.sh tests/integration/temporal/test_backend_resume_eligibility.py tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` and fix failures in `moonmind/workflows/temporal/workflows/run.py`, `moonmind/workflows/temporal/service.py`, or `moonmind/schemas/temporal_models.py`
- [X] T028 Validate the story end to end against `specs/345-step-ledger-checkpoint-durability/quickstart.md` and record the result in `specs/345-step-ledger-checkpoint-durability/verification.md` covering MM-646, FR-001 through FR-010, SCN-001 through SCN-007, SC-001 through SC-008, DESIGN-REQ-001 through DESIGN-REQ-007

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and testable independently.

---

## Phase 4: Polish and Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [X] T029 [P] Review `moonmind/schemas/temporal_models.py` and `moonmind/workflows/temporal/step_ledger.py` for bounded payloads, no secret leakage, no inline large/binary checkpoint content, and no compatibility aliases covering FR-005, SCN-005, SC-005, DESIGN-REQ-004
- [X] T030 [P] Review `moonmind/workflows/temporal/workflows/run.py` and `moonmind/workflows/temporal/service.py` for Temporal workflow/activity payload compatibility and retry-safe/idempotent behavior covering FR-004, FR-006, SCN-004, SC-004, DESIGN-REQ-004, DESIGN-REQ-007
- [X] T031 Update `specs/345-step-ledger-checkpoint-durability/contracts/step-ledger-checkpoint-evidence.md`, `specs/345-step-ledger-checkpoint-durability/data-model.md`, and `specs/345-step-ledger-checkpoint-durability/quickstart.md` if implementation discovers required contract wording changes, preserving MM-646 traceability for FR-010 and SC-008
- [X] T032 Run `./tools/test_unit.sh` for full required unit verification and document the result in `specs/345-step-ledger-checkpoint-durability/verification.md`
- [X] T033 Run `./tools/test_integration.sh` for hermetic integration verification when Docker is available, or document the concrete environment blocker in `specs/345-step-ledger-checkpoint-durability/verification.md`
- [ ] T034 Run `/speckit.verify` for `specs/345-step-ledger-checkpoint-durability/` after implementation and tests pass, covering MM-646, the original Jira preset brief, FR-001 through FR-010, SCN-001 through SCN-007, SC-001 through SC-008, DESIGN-REQ-001 through DESIGN-REQ-007, DESIGN-REQ-019, and DESIGN-REQ-023

---

## Dependencies and Execution Order

### Phase Dependencies

- Setup (Phase 1): no dependencies.
- Foundational (Phase 2): depends on Setup completion and blocks story work.
- Story (Phase 3): depends on Foundational completion.
- Polish and Verification (Phase 4): depends on story tests and implementation passing.

### Story Order

- T008-T012 unit tests must be written before implementation.
- T013-T014 integration tests must be written before implementation.
- T015-T016 red-first confirmations must complete before T018-T024 production changes.
- T017 determines whether FR-009 remains verification-only or needs fallback implementation.
- T018-T024 implement helper, schema, workflow, and service behavior.
- T026-T028 validate the story after implementation.
- T029-T034 complete polish, full verification, and `/speckit.verify`.

### Parallel Opportunities

- T008, T009, T010, T011, and T012 can be authored in parallel because they touch different test files.
- T013 and T014 can be authored in parallel because they touch different integration files.
- T029 and T030 can run in parallel after story validation because they inspect different production files.

## Parallel Example

```bash
# Parallel unit test authoring after Phase 2:
Task: "T008 Add failing prepared-ref unit tests in tests/unit/workflows/tasks/test_prepared_context.py"
Task: "T009 Add failing step ledger eligibility unit tests in tests/unit/workflows/temporal/test_step_ledger.py"
Task: "T010 Add failing parent workflow projection unit tests in tests/unit/workflows/temporal/workflows/test_run_step_ledger.py"

# Parallel integration test authoring after Phase 2:
Task: "T013 Add failing run resume integration coverage in tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py"
Task: "T014 Add failing backend eligibility integration coverage in tests/integration/temporal/test_backend_resume_eligibility.py"
```

## Implementation Strategy

### Requirement Status Handling

- Code-and-test work: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008.
- Verification-first with conditional fallback: FR-009.
- Already verified traceability: FR-010.

### Test-Driven Delivery

1. Complete setup and foundational mapping tasks.
2. Write focused unit tests and integration tests.
3. Run red-first confirmations and verify failures are for MM-646 gaps.
4. Implement prepared-ref evidence, step ledger schema/helpers, parent checkpoint emission, and Resume evidence wiring.
5. Run focused unit and integration validation.
6. Run quickstart validation, full unit suite, hermetic integration suite when available, and `/speckit.verify`.

## Notes

- This task list covers exactly one story.
- Do not create implementation tasks for broad adjacent Resume execution work outside MM-646.
- Unit and integration tests are required before production implementation.
- Preserve `MM-646` and the original Jira preset brief in verification evidence, commit text, and pull request metadata.
